"""Behavioral status boundary tests, with real loopback HTTP transport."""

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import Future
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from sparehash import ipc, status
from sparehash.__main__ import main
from sparehash.config import Config
from sparehash.power import Evidence, PowerSample
from sparehash.status import decode_json as decode
from sparehash.xmrig_config import ApiError, read_token


class PowerHelper:
    def query(self) -> Future[bool | None]:
        result: Future[bool | None] = Future()
        result.set_result(None)
        return result

    def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class FixtureProvider:
    value: status.StatusSnapshot

    def snapshot(self) -> status.StatusSnapshot:
        return self.value


def disconnected() -> ipc.Document:
    raise ipc.IPCError('socket missing')


class StatusTests(unittest.TestCase):
    def test_exit_one_when_ipc_error(self) -> None:
        for error in (ipc.IPCError('permission denied'), ipc.IPCError('timeout'),
                      ipc.IPCError('invalid JSON'), OSError('connection failure')):
            # Given
            provider = status.LiveProvider(controller_reader=Mock(side_effect=error))
            output = io.StringIO()
            # When
            with self.subTest(error=error), contextlib.redirect_stdout(output):
                code = status.run(True, provider)
            # Then
            self.assertEqual(code, 1)
            data = decode(output.getvalue())
            self.assertEqual(data['controller'], 'ipc-error')
            self.assertEqual(data['miner'], 'stopped/unavailable')
            self.assertIsNone(data['hashrate'])

    def test_exit_three_when_connection_refused(self) -> None:
        # Given
        provider = status.LiveProvider(controller_reader=Mock(side_effect=ipc.IPCError('connection refused')))
        # When
        with contextlib.redirect_stdout(io.StringIO()):
            code = status.run(True, provider)
        # Then
        self.assertEqual(code, 3)

    def test_ipc_call_when_transport_fails_preserves_error_category(self) -> None:
        for error, expected in ((TimeoutError(), 'timeout'), (PermissionError(), 'permission denied'),
                                (FileNotFoundError(), 'socket missing'),
                                (ConnectionRefusedError(), 'connection refused'), (OSError(), 'connection error')):
            # Given
            with self.subTest(expected=expected), patch('sparehash.ipc.socket.socket') as socket:
                socket.return_value.__enter__.return_value.connect.side_effect = error
                # When
                with self.assertRaises(ipc.IPCError) as raised:
                    _ = ipc.call({'cmd': 'status'})
                # Then
                self.assertEqual(raised.exception.code, expected)
                self.assertIs(raised.exception.cause, None if expected == 'timeout' else error)

    def test_exit_three_when_controller_disconnected(self) -> None:
        # Given
        provider = status.LiveProvider(controller_reader=disconnected)
        output = io.StringIO()
        # When
        with contextlib.redirect_stdout(output):
            code = status.run(True, provider)
        # Then
        self.assertEqual(code, 3)
        self.assertEqual(decode(output.getvalue())['miner'], 'stopped/unavailable')
        self.assertIsNone(decode(output.getvalue())['hashrate'])

    def test_cli_dispatch_when_status_json_requested(self) -> None:
        # Given
        output = io.StringIO()
        provider = FixtureProvider(status.StatusSnapshot())
        # When
        with patch('sparehash.status.LiveProvider.snapshot', provider.snapshot), contextlib.redirect_stdout(output):
            code = main(['status', '--json'], geteuid=lambda: 0)
        # Then
        self.assertEqual(code, 3)
        self.assertEqual(decode(output.getvalue())['controller'], 'unavailable')

    def test_power_when_disagreeing_unknown_stale_or_ups_only(self) -> None:
        # Given
        cases = ((True, True, False, 20, True, 'battery/unknown'),
                 (True, False, True, 20, False, 'battery/unknown'),
                 (None, None, None, 20, None, 'battery/unknown'),
                 (True, False, False, 0, None, 'battery/unknown'),
                 (False, True, None, 20, True, 'desktop/UPS-only'))
        for inventory, upower, sysfs, at, expected, guard in cases:
            sample = PowerSample(20, Evidence(inventory, 20), Evidence(upower, at), Evidence(sysfs, 20))
            # When
            result = decode(status.format_status(status.StatusSnapshot(power=sample), True))
            power = result['power']
            assert isinstance(power, dict)
            upower_result, sysfs_result = power['upower'], power['sysfs']
            assert isinstance(upower_result, dict) and isinstance(sysfs_result, dict)
            # Then
            self.assertEqual(upower_result['value'], expected)
            self.assertEqual(sysfs_result['value'], sysfs)
            self.assertEqual(upower_result['stale'], at == 0)
            self.assertEqual(result['ups_guard'], guard)

    def test_fields_when_hot_partial_multi_numa_and_unknown_gamer(self) -> None:
        # Given
        snapshot = status.StatusSnapshot(controller='ready', miner='stopped',
            blockers=(('thermal', 'hot'), ('gaming', 'unknown user')),
            gaming=(status.Gaming(12345, 'unknown', None, True),),
            thermal_sensor='/fixture/cpu', temperature_c=90,
            hugepages_actual=64, hugepages_target=1280, numa_nodes=(0, 1))
        # When
        result = decode(status.format_status(snapshot, True))
        # Then
        self.assertEqual(result['temperature_c'], 90)
        self.assertEqual(result['hugepages_attainment'], 'partial')
        self.assertIsNotNone(result['numa_warning'])
        self.assertEqual(result['gaming'], [{'uid': 12345, 'user': 'unknown', 'active': None, 'stale': True}])
        self.assertEqual(result['blockers'], [['thermal', 'hot'], ['gaming', 'unknown user']])

    def test_power_diagnostics_when_ac_maintenance_discharge(self) -> None:
        # Given
        sample = PowerSample(0, Evidence(True, 0), Evidence(False, 0), Evidence(True, 0), True, 0.179)
        # When
        result = decode(status.format_status(status.StatusSnapshot(power=sample), True))
        # Then
        power = result['power']
        assert isinstance(power, dict)
        self.assertIs(power['ac_online'], True)
        self.assertEqual(power['discharge_rate_w'], 0.179)

    def test_power_diagnostics_when_evidence_unavailable(self) -> None:
        # Given / When
        result = decode(status.format_status(status.StatusSnapshot(), True))
        # Then
        power = result['power']
        assert isinstance(power, dict)
        self.assertIsNone(power['ac_online'])
        self.assertIsNone(power['discharge_rate_w'])

    def test_live_power_diagnostics_when_sysfs_reports_rate(self) -> None:
        # Given
        provider = status.LiveProvider(controller_reader=lambda: {'ok': True, 'ready': True},
                                       resolver=lambda host: True)
        evidence = (Evidence(True, 0), Evidence(True, 0), True, 0.179)
        with patch('sparehash.status.read_sysfs', return_value=evidence), patch(
            'sparehash.status.Helpers', PowerHelper
        ), patch('sparehash.status.load', return_value=Config()):
            # When
            snapshot = provider.snapshot()
        # Then
        assert snapshot.power is not None
        self.assertIs(snapshot.power.ac_online, True)
        self.assertEqual(snapshot.power.discharge_rate_w, 0.179)

    def test_scheduler_when_effective_values_missing(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory:
            cgroup = Path(directory)
            _ = (cgroup / 'cpu.weight').write_text('1\n')
            _ = (cgroup / 'cpu.idle').write_text('1\n')
            # When
            observed = status.scheduling(cgroup)
            # Then
            self.assertIsNone(observed.cpu_idle)
            self.assertIsNone(observed.cpu_weight)
            self.assertIsNone(observed.pid)

    def test_scheduler_when_current_kernel_process_and_cgroup_match(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory:
            cgroup = Path(directory)
            _ = (cgroup / 'cgroup.procs').write_text(f'{os.getpid()}\n')
            _ = (cgroup / 'cpu.weight').write_text('7\n')
            _ = (cgroup / 'cpu.idle').write_text('0\n')
            with patch.object(Path, 'read_text', return_value='0::/system.slice/sparehash-xmrig.service\n'):
                # When
                observed = status.scheduling(cgroup)
            # Then
            self.assertEqual(observed.policy, os.sched_getscheduler(0))
            self.assertEqual(observed.nice, os.getpriority(os.PRIO_PROCESS, 0))
            self.assertEqual((observed.cpu_weight, observed.cpu_idle), (7, 0))

    def test_dns_hint_when_resolver_fails(self) -> None:
        # Given
        provider = status.LiveProvider(controller_reader=lambda: {'ok': True, 'ready': True,
            'running': False, 'gaming': [[12345, True]], 'observation_age': 11,
            'reasons': {'gaming': 'stale'}}, resolver=lambda host: False)
        # When
        with patch('sparehash.status.Helpers', PowerHelper), patch('sparehash.status.load', return_value=Config()):
            snapshot = provider.snapshot()
        # Then
        self.assertIsNotNone(snapshot.dns_hint)
        self.assertEqual(snapshot.gaming[0].uid, 12345)
        self.assertIsNone(snapshot.gaming[0].active)
        self.assertTrue(snapshot.gaming[0].stale)
        self.assertFalse(status.resolve_pool('invalid.invalid'))

    def test_thermal_when_unavailable(self) -> None:
        # Given / When
        result = decode(status.format_status(status.StatusSnapshot(), True))
        # Then
        self.assertEqual(result['thermal_status'], 'UNAVAILABLE')
        self.assertIsNone(result['temperature_c'])

    def test_api_when_authenticated_loopback_returns_summary(self) -> None:
        # Given
        token = 'a' * 64
        received: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                received.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                _ = self.wfile.write(json.dumps({'hashrate': {'total': [123.5]},
                    'connection': {'pool': 'local:' + token}, 'secret': token}).encode())

        with tempfile.TemporaryDirectory() as directory, patch.object(Handler, 'log_message'), HTTPServer(('127.0.0.1', 0), Handler) as server:
            config = Path(directory) / 'xmrig.json'
            _ = config.write_text(json.dumps({'http': {'access-token': token}}))
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            try:
                # When
                summary = status.miner_summary(server.server_port, lambda: read_token(config))
            finally:
                thread.join(timeout=3)
            # Then
            self.assertEqual(received, ['Bearer ' + token])
            self.assertEqual(summary.hashrate, 123.5)
            self.assertEqual(summary.api, 'available')
            snapshot = replace(summary, controller='ready', miner='running')
            self.assertNotIn(token, status.format_status(snapshot))
            self.assertNotIn(token, status.format_status(snapshot, True))

    def test_api_when_port_closed(self) -> None:
        # Given
        with HTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler) as server:
            port = server.server_port
        # When
        observed = status.miner_summary(port, lambda: 'b' * 64)
        # Then
        self.assertEqual(observed.api, 'unavailable')
        self.assertIsNone(observed.hashrate)
        self.assertIsNone(observed.pool)

    def test_api_when_error_contains_token(self) -> None:
        # Given
        token = 'c' * 64
        with patch('sparehash.xmrig_config.summary', side_effect=ApiError(1, token)):
            # When
            result = status.miner_summary(1, lambda: token)
        # Then
        self.assertNotIn(token, status.format_status(result, True))
        self.assertEqual(result.api, 'unavailable')

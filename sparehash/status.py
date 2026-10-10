# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Read-only status observations; no lifecycle, cache, or capability inference."""

from __future__ import annotations

import json
import math
import os
import pwd
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final, Protocol

from . import ipc, xmrig_config
from .config import ConfigError, load
from .paths import XMRIG_JSON
from .power import Evidence, Helpers, PowerSample, read_sysfs
from .thermal import SensorReadError, find_sensor, read_celsius

CGROUP: Final = Path('/sys/fs/cgroup/system.slice/sparehash-xmrig.service')
DNS_HINT: Final = 'Pool resolution failed; filtered DNS is possible; check resolver/network policy.'


class Decoder(Protocol):
    def __call__(self, text: str, /) -> ipc.Document: ...


def decode_json(text: str, decoder: Decoder = json.loads) -> ipc.Document:
    return decoder(text)


@dataclass(frozen=True, slots=True)
class Scheduler:
    pid: int | None = None
    policy: int | None = None
    nice: int | None = None
    cpu_weight: int | None = None
    cpu_idle: int | None = None
    requested: str = 'SCHED_IDLE; Nice=19; CPUWeight=idle if supported, otherwise 1'


@dataclass(frozen=True, slots=True)
class Gaming:
    uid: int
    user: str
    active: bool | None
    stale: bool


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    controller: str = 'unavailable'
    miner: str = 'stopped/unavailable'
    blockers: tuple[tuple[str, str], ...] = ()
    power: PowerSample | None = None
    gaming: tuple[Gaming, ...] = ()
    thermal_sensor: str | None = None
    temperature_c: float | None = None
    hugepages_actual: int | None = None
    hugepages_target: int | None = None
    numa_nodes: tuple[int, ...] | None = None
    scheduler: Scheduler = Scheduler()
    api: str = 'unavailable'
    hashrate: float | None = None
    pool: str | None = None
    dns_hint: str | None = None
    msr: str = 'unavailable in v1'


class Provider(Protocol):
    def snapshot(self) -> StatusSnapshot: ...


def number(path: Path) -> int | None:
    """Bounded kernel integer read; absent/invalid evidence stays unavailable."""
    try:
        with path.open(encoding='ascii') as source:
            raw = source.read(64)
        return int(raw) if re.fullmatch(r'[0-9]{1,20}\n?', raw) else None
    except (OSError, UnicodeError):
        return None


def scheduling(cgroup: Path = CGROUP) -> Scheduler:
    """Inspect only the fixed miner cgroup; fixture path is construction-only."""
    pid = number(cgroup / 'cgroup.procs')
    if pid is None or pid <= 0:
        return Scheduler()
    try:
        membership = Path(f'/proc/{pid}/cgroup').read_text(encoding='ascii')
        if membership != '0::/system.slice/sparehash-xmrig.service\n':
            return Scheduler()
        result = Scheduler(pid, os.sched_getscheduler(pid), os.getpriority(os.PRIO_PROCESS, pid),
                           number(cgroup / 'cpu.weight'), number(cgroup / 'cpu.idle'))
        return result if number(cgroup / 'cgroup.procs') == pid else Scheduler()
    except (OSError, UnicodeError):
        return Scheduler()


def resolve_pool(host: str) -> bool:
    """Killable fixed-path DNS worker; never block this process in getaddrinfo."""
    if host.endswith('.invalid'):
        return False
    try:
        result = subprocess.run(('/usr/bin/python3', '-I', '-c',
                                 'import socket,sys; socket.getaddrinfo(sys.argv[1], None)', host),
                                timeout=2, check=False, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env={'LC_ALL': 'C'})
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def miner_summary(port: int, token_reader: Callable[[], str] = xmrig_config.read_token) -> StatusSnapshot:
    """Select only hashrate/pool, discard arbitrary API fields and raw errors."""
    try:
        token = token_reader()
        raw = xmrig_config.summary('127.0.0.1', port, token, timeout=1)
        rate, connection = raw.get('hashrate'), raw.get('connection')
        value: float | None = None
        pool: str | None = None
        if isinstance(rate, dict):
            total = rate.get('total')
            if isinstance(total, list) and total and isinstance(total[0], (int, float)) and type(total[0]) is not bool:
                value = float(total[0])
                if not math.isfinite(value) or value < 0:
                    value = None
        if isinstance(connection, dict):
            candidate = connection.get('pool')
            if isinstance(candidate, str):
                pool = candidate.replace(token, '[redacted]')[:256]
        return StatusSnapshot(api='available', hashrate=value, pool=pool)
    except (OSError, xmrig_config.RenderError, xmrig_config.ApiError):
        return StatusSnapshot(api='unavailable')


def protected_token() -> str:
    """Pin a regular root-owned non-public credential file before existing parser."""
    import stat

    fd = os.open(XMRIG_JSON, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if info.st_uid != 0 or info.st_mode & 0o027 or not stat.S_ISREG(info.st_mode):
            raise xmrig_config.RenderError('unprotected generated configuration')
        return xmrig_config.read_token(Path(f'/proc/self/fd/{source.fileno()}'))


@dataclass(frozen=True, slots=True)
class LiveProvider:
    controller_reader: Callable[[], ipc.Document] = lambda: ipc.call({'cmd': 'status'}, timeout=1)
    resolver: Callable[[str], bool] = resolve_pool

    def snapshot(self) -> StatusSnapshot:
        try:
            raw = self.controller_reader()
        except ipc.IPCError as error:
            return StatusSnapshot(controller='unavailable' if error.code in ('socket missing', 'connection refused') else 'ipc-error')
        except OSError:
            return StatusSnapshot(controller='ipc-error')
        if raw.get('ok') is not True or type(raw.get('ready')) is not bool:
            return StatusSnapshot()
        reasons = raw.get('reasons')
        blockers = tuple((name, reason) for name, reason in reasons.items()
                         if isinstance(reason, str)) if isinstance(reasons, dict) else ()
        age = raw.get('observation_age')
        fresh = type(age) in (int, float) and isinstance(age, (int, float)) and 0 <= age <= 10
        users: list[Gaming] = []
        gaming = raw.get('gaming')
        if isinstance(gaming, list):
            for entry in gaming:
                if (isinstance(entry, list) and len(entry) == 2 and type(entry[0]) is int
                        and (entry[1] is None or type(entry[1]) is bool)):
                    uid = entry[0]
                    try:
                        user = pwd.getpwuid(uid).pw_name
                    except KeyError:
                        user = 'unknown'
                    active = entry[1] if isinstance(entry[1], bool) and fresh else None
                    users.append(Gaming(uid, user, active, not fresh))
        now = time.monotonic()
        inventory, sysfs = read_sysfs(Path('/sys'), now)
        helpers = Helpers()
        try:
            try:
                upower = Evidence(helpers.query().result(timeout=7), now)
            except TimeoutError:
                upower = Evidence(None, now)
        finally:
            helpers.close()
        power = PowerSample(time.monotonic(), inventory, upower, sysfs)
        sensor = find_sensor(Path('/sys/class/hwmon'))
        temperature = None
        if sensor is not None:
            try:
                temperature = read_celsius(sensor)
            except SensorReadError:
                temperature = None
        nodes = tuple(int(p.name[4:]) for p in Path('/sys/devices/system/node').glob('node[0-9]*')
                      if re.fullmatch(r'node[0-9]+', p.name) and _memory_node(p))
        running = raw.get('running') is True and raw.get('ready') is True
        result = StatusSnapshot(controller='ready' if raw.get('ready') is True else 'starting',
                                miner='running/unverified' if running else 'stopped', blockers=blockers,
                                power=power, gaming=tuple(users), thermal_sensor=str(sensor.path) if sensor else None,
                                temperature_c=temperature, numa_nodes=nodes or None,
                                hugepages_actual=number(Path('/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages')),
                                scheduler=scheduling() if running else Scheduler())
        try:
            cfg = load()
        except (OSError, ConfigError):
            return result
        api = miner_summary(cfg.api.port, protected_token) if running else StatusSnapshot()
        return replace(result, api=api.api, hashrate=api.hashrate, pool=api.pool,
                       miner='running' if running and api.api == 'available' else result.miner,
                       hugepages_target=cfg.hugepages.pages,
                       dns_hint=None if self.resolver(cfg.pool.host) else DNS_HINT)


def _memory_node(path: Path) -> bool:
    try:
        with (path / 'meminfo').open(encoding='ascii') as source:
            return re.search(r'MemTotal:\s+[1-9][0-9]*\s+kB', source.read(8192)) is not None
    except (OSError, UnicodeError):
        return False


def format_status(snapshot: StatusSnapshot, json_output: bool = False) -> str:
    """One JSON contract drives both formats; unknown is never converted to clear."""
    data = decode_json(json.dumps(asdict(snapshot)))
    data['thermal_status'] = 'UNAVAILABLE' if snapshot.temperature_c is None else 'available'
    data['numa_warning'] = None if snapshot.numa_nodes is not None and len(snapshot.numa_nodes) == 1 else 'NUMA allocation unavailable'
    data['hugepages_attainment'] = ('unavailable' if snapshot.hugepages_actual is None or snapshot.hugepages_target is None
                                  else 'partial' if snapshot.hugepages_actual < snapshot.hugepages_target else 'satisfied')
    power: ipc.Document = {name: {'value': None, 'stale': False} for name in ('inventory', 'upower', 'sysfs')} if snapshot.power is None else {
        name: {'value': evidence.credible(snapshot.power.at), 'stale': evidence.at < snapshot.power.at - 10}
        for name, evidence in (('inventory', snapshot.power.inventory), ('upower', snapshot.power.upower_evidence),
                               ('sysfs', snapshot.power.sysfs_evidence))}
    data['power'] = power
    data['ups_guard'] = 'unavailable' if snapshot.power is None else ('desktop/UPS-only' if snapshot.power.battery_present is False else 'battery/unknown')
    rendered = re.sub(r'[0-9a-fA-F]{64}', '[redacted]', json.dumps(data, allow_nan=False))
    if json_output:
        return rendered
    return '\n'.join(f'{name}: {json.dumps(value)}' for name, value in decode_json(rendered).items()) + '\nMSR: unavailable in v1'


def run(json_output: bool = False, provider: Provider | None = None) -> int:
    snapshot = (provider if provider is not None else LiveProvider()).snapshot()
    print(format_status(snapshot, json_output))
    return {'unavailable': 3, 'ipc-error': 1}.get(snapshot.controller, 0)

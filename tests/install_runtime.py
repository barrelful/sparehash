"""Runtime launch refusals and real notify readiness at the assembly boundary."""

from __future__ import annotations

import json
import pwd
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import __main__, runtime_spec
from sparehash.controller_io import Job, Observation, Ports, SampleId
from sparehash.runtime import Lifecycle, initialize
from sparehash.state import BlockerState


@final
class FixtureProviders:
    def request(self, sample: SampleId, deadline: float) -> None:
        _ = sample, deadline


@final
class FixtureJobs:
    def submit(self, job: Job) -> None:
        _ = job

    def cancel(self) -> None:
        pass

    def published(self) -> None:
        return None


@final
class RuntimeTests(unittest.TestCase):
    def test_miner_refused_when_root(self) -> None:
        # Given / When
        result = __main__.main(["__miner_exec__"], geteuid=lambda: 0, getusername=lambda _: "xmrig")
        # Then
        self.assertEqual(result, 1)

    def test_miner_refused_when_caller_supplies_operation_path(self) -> None:
        # Given / When
        result = __main__.main(["__miner_exec__", "--operation", "/tmp/operation.json"], geteuid=lambda: 996, getusername=lambda _: "xmrig")
        # Then
        self.assertEqual(result, 1)

    def test_miner_refused_when_mode_is_unrecognized(self) -> None:
        # Given
        account = pwd.struct_passwd(("xmrig", "x", 996, 984, "", "/nonexistent", "/usr/sbin/nologin"))
        document = json.dumps({"generation": 1, "mode": "exec-anything", "binary": "/bin/sh", "config": "/tmp/config"}).encode()
        with (mock.patch("sparehash.runtime_spec.os.geteuid", return_value=996),
              mock.patch("sparehash.runtime_spec.pwd.getpwnam", return_value=account),
              mock.patch.object(runtime_spec, "protected", return_value=document)):
            # When
            result = runtime_spec.miner()
        # Then
        self.assertEqual(result, 1)

    def test_notify_barrier_when_fresh_required_observations_arrive(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
            root = Path(directory)
            address = str(root / "notify.sock")
            listener.bind(address)
            listener.settimeout(1)
            bound = threading.Event()
            bound.set()
            owner = initialize(BlockerState(root / "state.json"), Ports(FixtureProviders(), FixtureJobs(), Lifecycle(bound, address), lambda: 0.0))
            self.assertFalse(owner.ready)
            # When
            owner.observe(Observation(owner.sample, 0.0, False, None, None, (), False))
            # Then
            self.assertTrue(owner.ready)
            self.assertEqual(listener.recv(128), b"READY=1")

"""Public config transactions and controller-only benchmark requests."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, assert_never

from . import ipc
from .cli_ops import Client, OperationError
from .config import Config, ConfigError, load, save, validate
from .paths import CONFIG_TOML, UNIT_CONTROLLER, UNIT_MINER, XMRIG_JSON
from .sysdetect import Capabilities
from .wizard import load_answers, run_interactive
from .xmrig_config import IsolatedRunner, read_token, render_config, validate_rendered, write_xmrig_json


def _safe_text(text: str) -> str:
    return re.sub(r"[0-9a-fA-F]{64}", "[redacted]", " ".join(text.split()))[:512]


def _request(command: str, client: Client) -> ipc.Document:
    response = client({"cmd": command}, timeout=1)
    if response.get("ok") is not True:
        raise OperationError(command, "controller rejected request")
    return response


@dataclass(frozen=True, slots=True)
class ConfigFiles:
    """Trusted deployment destinations; root/uid seams are fixture-only."""

    public: Path = CONFIG_TOML
    miner: Path = XMRIG_JSON
    root: Path = Path("/")
    uid: int = 0

    def pin(self, stack: ExitStack) -> Path:
        if self.public.parent != self.miner.parent:
            raise OperationError("config", "destinations must share a protected directory")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _ = stack.callback(os.close, fd)
        for part in (*self.public.parent.relative_to(self.root).parts, ""):
            info = os.fstat(fd)
            if info.st_uid != self.uid or info.st_mode & 0o022:
                raise OperationError("config", "unsafe configuration directory")
            if part:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                _ = stack.callback(os.close, fd)
        return Path(f"/proc/self/fd/{fd}")

    def source(self, stack: ExitStack, path: Path) -> Path:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        _ = stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid
                or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
            raise OperationError("config", "unsafe configuration file")
        if path.name == self.miner.name and stat.S_IMODE(info.st_mode) != 0o640:
            raise OperationError("config", "generated config must remain protected 0640")
        return Path(f"/proc/self/fd/{fd}")


def show(path: Path = CONFIG_TOML) -> int:
    """Only serialize the public schema, never open generated credentials."""
    try:
        cfg = load(path)
        errors = validate(cfg)
        if errors:
            raise ConfigError("; ".join(errors))
        print(json.dumps(cfg.to_dict(), indent=2))
    except (ConfigError, OSError, UnicodeError):
        print("sparehash: config show: invalid or unavailable public config", file=sys.stderr)
        return 1
    return 0


@dataclass(frozen=True, slots=True)
class Editor:
    """Injected interactive editor; persistence stays outside the wizard."""

    caps: Capabilities
    input_fn: Callable[[str], str] = input
    output_fn: Callable[[str], None] = print

    def __call__(self, current: Config) -> Config:
        return run_interactive(self.caps, self.input_fn, self.output_fn, current=current)


def _backup(source: Path, directory: Path) -> Path:
    """Exclusive same-directory backup copied from a verified pinned descriptor."""
    info = source.stat()
    with source.open("rb") as original, tempfile.NamedTemporaryFile(
        dir=directory, prefix=".sparehash-backup-", delete=False,
    ) as backup:
        path = Path(backup.name)
        try:
            os.fchown(backup.fileno(), info.st_uid, info.st_gid)
            os.fchmod(backup.fileno(), stat.S_IMODE(info.st_mode))
            _ = backup.write(original.read())
            backup.flush()
            os.fsync(backup.fileno())
        except OSError:
            path.unlink()
            raise
    return path


def edit(files: ConfigFiles | None = None, editor: Callable[[Config], Config] | None = None, *,
         geteuid: Callable[[], int] = os.geteuid,
         confirm: Callable[[str], str] = input, client: Client = ipc.call,
         runner: IsolatedRunner | None = None,
         capabilities: Callable[[], Capabilities] = Capabilities.detect) -> int:
    """Confirm, validate, back up and publish; failures restore both config files.

    The independent I/O, identity, IPC and isolation keyword seams are deliberately
    explicit for offline safety tests. Missing tokens are installer first-setup
    work, not permission/malformed-file fallbacks during edit.
    """
    if geteuid() != 0:
        print("sparehash: config edit requires root", file=sys.stderr)
        return 2
    destinations = files or ConfigFiles()
    try:
        with ExitStack() as stack:
            directory = destinations.pin(stack)
            public, miner = directory / destinations.public.name, directory / destinations.miner.name
            original = destinations.source(stack, public)
            caps = capabilities()
            current = load_answers(original, caps)
            updated = (editor or Editor(caps))(current)
            if confirm("Overwrite public and generated configuration? Type yes: ").strip().lower() != "yes":
                print("Configuration unchanged.")
                return 0
            # Use the same strict wizard boundary even for injected editor results.
            with tempfile.TemporaryDirectory(prefix="sparehash-edit-") as staging:
                candidate = Path(staging) / "config.toml"
                save(updated, candidate, warn_overwrite=False)
                updated = load_answers(candidate, caps)
            protected = destinations.source(stack, miner)
            token = read_token(protected)
            rendered = render_config(updated, token, Path("/nonexistent-sparehash-render"))
            if os.path.lexists(updated.xmrig.binary):
                validate_rendered(rendered, Path(updated.xmrig.binary), runner)
            _ = _request("status", client)  # unavailable controller: no publication
            public_backup = _backup(original, directory)
            miner_backup = _backup(protected, directory)
            try:
                save(updated, public, warn_overwrite=False)
                write_xmrig_json(updated, token, miner, runner=runner)
                _ = _request("reload", client)  # controller owns reauthorization
            except (ConfigError, OSError, KeyError, ipc.IPCError, OperationError, KeyboardInterrupt):
                os.replace(public_backup, public)
                os.replace(miner_backup, miner)
                raise
            miner_backup.unlink()
        print("Configuration saved; controller reload requested (not a mining-start confirmation).")
        return 0
    except KeyboardInterrupt:
        return 130
    except (ConfigError, OSError, KeyError, UnicodeError, ipc.IPCError, OperationError):
        # Do not expose parser/runner/client payloads that may contain credentials.
        print("sparehash: config edit failed; check config, protected paths, isolation and controller availability", file=sys.stderr)
        return 1


class BenchmarkOutcome(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class OperationOutput(Protocol):
    """Todo18 adapter must correlate the accepted operation and follow these units.

    EOF/controller loss is aborted, never success. It must wait for the controller's
    terminal operation result, not infer completion from journal prose or running.
    No start/restart/miner execution is permitted in this output adapter.
    """

    def __call__(self, units: tuple[str, str], emit: Callable[[str], None]) -> BenchmarkOutcome: ...


def benchmark(client: Client = ipc.call, output: OperationOutput | None = None,
              geteuid: Callable[[], int] = os.geteuid) -> int:
    """Fixed 1M mode is controller-owned; accepted is not completed."""
    if geteuid() != 0:
        print("sparehash: benchmark unavailable without root", file=sys.stderr)
        return 2
    try:
        response = client({"cmd": "benchmark"}, timeout=1)
        if response.get("ok") is not True:
            status = _request("status", client)
            blockers = status.get("blockers")
            names = ", ".join(_safe_text(name) for name in blockers if isinstance(name, str)) if isinstance(blockers, list) else "unknown"
            raise OperationError("benchmark", f"controller denied operation; blockers: {names or 'unknown/busy'}")
        if output is None:
            print("Benchmark request accepted; completion unavailable until Todo18 operation-output adapter.", file=sys.stderr)
            return 3
        result = output((UNIT_CONTROLLER, UNIT_MINER), lambda line: print(_safe_text(line)))
        match result:
            case BenchmarkOutcome.COMPLETED:
                print("Benchmark completed.")
                return 0
            case BenchmarkOutcome.ABORTED | BenchmarkOutcome.FAILED:
                raise OperationError("benchmark", result.value)
            case _:
                assert_never(result)
    except (ipc.IPCError, OSError):
        print("sparehash: benchmark: controller unavailable or operation aborted", file=sys.stderr)
        return 1
    except OperationError as error:
        print(f"sparehash: {error}", file=sys.stderr)
        return 1

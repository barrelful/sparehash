"""Serialized, best-effort 2MiB leases; trusted adapters own launch exclusion.

Roots and ownership injection are construction-only, never IPC/environment input.
The Jobs adapter must hold lifecycle exclusion, pass fresh authorization, and keep
starts excluded throughout cleanup. Cgroup discovery/CLI wiring remain adapters.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import BinaryIO, Final

LOGGER: Final = logging.getLogger(__name__)
PAGE_PATH: Final = Path("kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")


class LeaseError(ValueError):
    """Untrusted lease/kernel evidence cannot establish ownership."""


@dataclass(frozen=True, slots=True)
class Roots:
    """Root-controlled filesystem locations; missing cgroup is unknown, not empty."""

    sys: Path = Path("/sys")
    state: Path = Path("/var/lib/sparehash")
    proc: Path = Path("/proc")
    cgroup: Path | None = None


@dataclass(frozen=True, slots=True)
class Ownership:
    uid: int = 0
    gid: int = 0
    chown: Callable[[int, int, int], None] = os.fchown


@dataclass(frozen=True, slots=True)
class Result:
    status: str
    actual: int | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class Ledger:
    boot_id: str
    baseline: int
    target: int
    path: str
    applied: int | None = None


def _count(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="ascii") as source:
        text = source.read(64)
    if re.fullmatch(r"[0-9]{1,20}\n?", text) is None:
        raise LeaseError("invalid page count")
    return int(text)


def _write(path: Path, count: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_TRUNC)
    with os.fdopen(fd, "w", encoding="ascii") as destination:
        _ = destination.write(f"{count}\n")


@dataclass(frozen=True, slots=True)
class LeaseManager:
    roots: Roots = Roots()
    boot_id: str | None = None
    ownership: Ownership = Ownership()
    empty_proof: Callable[[], bool] | None = None

    @property
    def ledger_path(self) -> Path:
        return self.roots.state / "hugepages.json"

    def _boot(self) -> str:
        value = self.boot_id
        if value is None:
            value = (self.roots.proc / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None:
            raise LeaseError("unknown boot id")
        return value

    def _protected(self, fd: int) -> None:
        info = os.fstat(fd)
        if info.st_uid != self.ownership.uid or info.st_mode & 0o022:
            raise LeaseError("unprotected state evidence")

    def _load(self) -> Ledger:
        fd = os.open(self.ledger_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="ascii") as source:
            self._protected(source.fileno())
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise LeaseError("ledger is not regular")
            text = source.read(4097)
        if len(text) > 4096:
            raise LeaseError("oversized ledger")
        # Parse only our exact schema; duplicate keys/coercions never gain ownership.
        found = re.fullmatch(
            r'\{"applied": (null|[0-9]{1,20}), "baseline": ([0-9]{1,20}), '
            + r'"boot_id": "([0-9a-f-]{36})", "path": '
            + re.escape(json.dumps(str(self.roots.sys / PAGE_PATH)))
            + r', "target": ([0-9]{1,20})\}\n', text,
        )
        if found is None:
            raise LeaseError("malformed ledger")
        applied = None if found[1] == "null" else int(found[1])
        baseline, target = int(found[2]), int(found[4])
        if baseline < 0 or target < baseline or (applied is not None and not baseline <= applied <= target):
            raise LeaseError("ambiguous ledger counts")
        ledger = Ledger(found[3], baseline, target, str(self.roots.sys / PAGE_PATH), applied)
        if text != json.dumps(asdict(ledger), sort_keys=True) + "\n":
            raise LeaseError("noncanonical ledger")
        return ledger

    def _persist(self, ledger: Ledger) -> None:
        name: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="ascii", dir=self.roots.state, delete=False) as output:
                name = output.name
                self.ownership.chown(output.fileno(), self.ownership.uid, self.ownership.gid)
                os.fchmod(output.fileno(), 0o640)
                _ = output.write(json.dumps(asdict(ledger), sort_keys=True) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.ledger_path)
            self._sync_dir()
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def _sync_dir(self) -> None:
        fd = os.open(self.roots.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _empty(self) -> bool:
        path = self.roots.cgroup
        if path is None:
            return self.empty_proof() if self.empty_proof is not None else False
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            contents: list[str] = []
            for name in ("cgroup.events", "cgroup.procs"):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, "r", encoding="ascii") as source:
                    contents.append(source.read(4097))
            events, procs = contents
        finally:
            os.close(fd)
        populated = [line for line in events.splitlines() if line.startswith("populated")]
        return max(len(events), len(procs)) <= 4096 and populated == ["populated 0"] and not procs.strip()

    def _restore(self) -> Result:
        if not os.path.lexists(self.ledger_path):
            return Result("idle")
        ledger = self._load()
        if ledger.applied is None:
            raise LeaseError("unresolved reservation; operator inspection required")
        if ledger.boot_id != self._boot() or ledger.path != str(self.roots.sys / PAGE_PATH):
            raise LeaseError("boot or sysfs path changed")
        if not self._empty():
            raise LeaseError("miner cgroup is populated or unknown")
        path = self.roots.sys / PAGE_PATH
        if _count(path) != ledger.applied:
            raise LeaseError("external page count changed")
        if ledger.applied != ledger.baseline:
            _write(path, ledger.baseline)
            # Kernel writes are best effort, not a setter guarantee.
            if _count(path) != ledger.baseline:
                raise LeaseError("kernel did not restore baseline")
        self.ledger_path.unlink()
        self._sync_dir()
        return Result("restored", ledger.baseline)

    def _locked(self) -> BinaryIO:
        """Serialize the controller and ExecStopPost across ledger transitions."""
        parent = os.open(self.roots.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._protected(parent)
            fd = os.open("hugepages.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o640, dir_fd=parent)
        finally:
            os.close(parent)
        stream = os.fdopen(fd, "rb")
        try:
            self._protected(stream.fileno())
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise LeaseError("lock is not regular")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except (OSError, LeaseError):
            stream.close()
            raise
        return stream

    def reserve_after_authorization(self, enabled: bool, numa_nodes: tuple[int, ...] | None,
                                    target: int = 1280, *, authorized: bool = False) -> Result:
        """Adapter supplies fresh authorization under launch exclusion, not IPC input.

        Four independent policy inputs preserve the requested integration seam;
        authorization defaults false and is never cached by this manager.
        """
        if not authorized or not enabled:
            return Result("skipped")
        if numa_nodes is None or len(numa_nodes) != 1 or numa_nodes[0] < 0:
            return self._warning("single credible memory-bearing NUMA node required")
        if type(target) is not int or target <= 0:
            return self._warning("invalid target")
        try:
            with self._locked():
                _ = self._restore()
                boot = self._boot()
                path = self.roots.sys / PAGE_PATH
                baseline = _count(path)
                if baseline >= target:
                    return Result("satisfied", baseline)
                ledger = Ledger(boot, baseline, max(baseline, target), str(path))
                self._persist(ledger)
                _write(path, ledger.target)
                actual = _count(path)
                if not baseline <= actual <= ledger.target:
                    raise LeaseError("ambiguous observed reservation")
                self._persist(replace(ledger, applied=actual))
                if actual < target:
                    return self._warning("partial allocation", actual)
                return Result("reserved", actual)
        except (OSError, ValueError, UnicodeError) as exc:
            return self._warning(str(exc))

    def restore_after_cgroup_empty(self) -> Result:
        """Read fixed cgroup evidence itself; never trust a caller boolean."""
        try:
            with self._locked():
                return self._restore()
        except (OSError, ValueError, UnicodeError) as exc:
            return self._warning(str(exc))

    def cleanup(self) -> Result:
        """Fixed __cleanup__ adapter entry; no systemctl or miner execution."""
        return self.restore_after_cgroup_empty()

    def _warning(self, reason: str, actual: int | None = None) -> Result:
        LOGGER.warning("hugepage lease degraded", extra={"reason": reason, "actual": actual})
        return Result("degraded", actual, reason)

# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: python3 -I -B qa/atomic_probe.py (via qa_atomic_probe.sh only).
"""Task-3-only authorization and raw-observation validation; no product imports."""

from __future__ import annotations

import ast
import grp
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Final

# -I excludes cwd; only the explicitly trusted harness root supplies sibling imports.
sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
from qa.atomic_probe_evidence import PROPERTIES as PROPERTIES  # noqa: E402
from qa.atomic_probe_evidence import Evidence as Evidence  # noqa: E402
from qa.atomic_probe_evidence import Json as Json  # noqa: E402
from qa.atomic_probe_evidence import RefusalError as RefusalError  # noqa: E402
from qa.atomic_probe_evidence import read_raw, summarize_raw  # noqa: E402
from qa.atomic_probe_evidence import validate_evidence as validate_evidence  # noqa: E402

SENSITIVE: Final = frozenset(("root", "wheel", "sudo", "adm", "disk", "kmem", "kvm", "netdev", "network", "audio", "video", "input", "render", "sparehash", "xmrig"))


@dataclass(frozen=True, slots=True)
class Manifest:
    kind: str
    surface: str
    machine_id: str
    root_identity: str
    candidate_sha: str
    source_sha256: str
    executable: str
    executable_sha256: str


@dataclass(frozen=True, slots=True)
class Facts:
    machine_id: str
    root_identity: str
    candidate_sha: str
    source_sha256: str
    executable_sha256: str
    selinux: str
    uid: int
    gid: int
    group: str
    guest: bool


def authorize(manifest: Manifest, facts: Facts) -> None:
    if manifest.kind != "disposable-vm" or manifest.surface != "atomic-task3-nonmining":
        raise RefusalError("unauthorized environment/surface")
    for name in ("machine_id", "root_identity", "candidate_sha", "source_sha256", "executable_sha256"):
        if getattr(manifest, name) != getattr(facts, name):
            raise RefusalError(f"identity mismatch: {name}")
    if facts.selinux != "Enforcing" or not facts.guest:
        raise RefusalError("requires enforcing disconnected Atomic VM with systemd PID1")
    if facts.uid <= 0 or facts.gid <= 0 or not facts.group or facts.group in SENSITIVE:
        raise RefusalError("missing safe nobody UID/primary GID")


def protected_read(path: Path, limit: int = 16384) -> bytes:
    """Pin every component; only root sticky /tmp and /var/tmp may be writable."""
    if not path.is_absolute() or ".." in path.parts:
        raise RefusalError("absolute no-traversal path required")
    with ExitStack() as stack:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        _ = stack.callback(os.close, fd)
        current = Path("/")
        for part in path.parts[1:]:
            current /= part
            fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            _ = stack.callback(os.close, fd)
            info = os.fstat(fd)
            sticky = current in (Path("/tmp"), Path("/var/tmp")) and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid != 0 or (info.st_mode & 0o022 and not sticky):
                raise RefusalError("path is not root-controlled")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise RefusalError("requires bounded single-link regular file")
        with os.fdopen(os.dup(fd), "rb") as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise RefusalError("file grew beyond limit")
        return data


def unique_pairs(pairs: list[tuple[str, Json]]) -> Json:
    if len({key for key, _ in pairs}) != len(pairs):
        raise RefusalError("duplicate manifest fields")
    return dict(pairs)


def read_manifest(path: Path, reader: Callable[[Path], bytes] = protected_read,
                  decode: Callable[..., Json] = json.loads) -> Manifest:
    raw = decode(reader(path), object_pairs_hook=unique_pairs)
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(Manifest)}:
        raise RefusalError("unknown/missing manifest fields")
    strings = {key: value for key, value in raw.items() if isinstance(value, str)}
    if len(strings) != len(raw) or any(not re.fullmatch(r"[a-zA-Z0-9_./:-]{1,256}", value) for value in strings.values()):
        raise RefusalError("manifest values must be narrow strings")
    manifest = Manifest(**strings)
    for value, length in ((manifest.machine_id, 32), (manifest.candidate_sha, 40),
                          (manifest.source_sha256, 64), (manifest.executable_sha256, 64)):
        if not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
            raise RefusalError("invalid identity digest")
    return manifest


def source_digest(root: Path) -> str:
    """External authority pins this inventory digest, not a self-reported signature."""
    paths = sorted((*root.glob("sparehash/*.py"), *root.glob("systemd/*.in"),
                    *root.glob("qa/atomic_probe*.py"), *root.glob("qa/__init__.py"),
                    root / "qa/qa_atomic_probe.sh"))
    digest = hashlib.sha256()
    for path in paths:
        data = protected_read(path, 1024 * 1024)
        digest.update(str(path.relative_to(root)).encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def preflight(path: Path, *, retained: bool = False) -> Manifest:
    if os.environ.get("SPAREHASH_QA_DISPOSABLE_VM") != "YES":
        raise RefusalError("SPAREHASH_QA_DISPOSABLE_VM=YES required")
    manifest = read_manifest(path)
    if os.environ.get("CANDIDATE_SHA") != manifest.candidate_sha:
        raise RefusalError("CANDIDATE_SHA must match authorized candidate")
    if os.geteuid() != 0:
        raise RefusalError("guest root required; never use host sudo")
    root = Path(__file__).absolute().parent.parent
    helpers: list[str] = []
    for tool in ("systemd-run", "systemctl", "getenforce", "systemd-detect-virt", "matchpathcon",
                 "id", "sh", "timeout", "findmnt", "ls", "cp", "mkdir", "rm", "rmdir",
                 "stat", "chmod", "sha256sum", "date", "cat", "cmp", "grep", "printf", "python3"):
        resolved = Path("/usr/bin", tool).resolve(strict=True)
        _ = protected_read(resolved, 64 * 1024 * 1024)
        helpers.append(str(resolved))
    for tool in ("ausearch", "auditctl"):
        _ = protected_read(Path("/usr/sbin", tool).resolve(strict=True), 64 * 1024 * 1024)
    account = pwd.getpwnam("nobody")
    group = grp.getgrgid(account.pw_gid).gr_name
    legacy = [entry for entry in grp.getgrall() if entry.gr_name == "nogroup"]
    if legacy and legacy[0].gr_gid <= 0:
        raise RefusalError("legacy comparison would use privileged primary GID")
    if any(entry.gr_name in SENSITIVE and "nobody" in entry.gr_mem for entry in grp.getgrall()):
        raise RefusalError("nobody has sensitive supplementary membership")
    executable = protected_read(Path(manifest.executable), 64 * 1024 * 1024)
    if not executable.startswith(b"\x7fELF"):
        raise RefusalError("offline executable must be authenticated ELF")
    properties = ast.parse(protected_read(root / "sparehash/xmrig_config.py").decode())
    declared = [ast.literal_eval(node.value) for node in properties.body
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "PROPERTIES" and node.value is not None]
    if declared != [PROPERTIES]:
        raise RefusalError("candidate isolation contract changed; review harness")
    env = {"PATH": "/usr/sbin:/usr/bin", "LANG": "C", "LC_ALL": "C"}
    shown = subprocess.check_output(["/usr/bin/systemctl", "show", "systemd-journald.service",
                                    *(f"-p{item.split('=')[0]}" for item in PROPERTIES)],
                                   env=env, timeout=5, text=True).splitlines()
    if any(not item.partition("=")[2] for item in shown) or {item.split("=")[0] for item in shown} != {item.split("=")[0] for item in PROPERTIES}:
        raise RefusalError("mandatory unit properties unavailable before staging")
    def observe(tool: str) -> str:
        return subprocess.check_output([f"/usr/bin/{tool}"], env=env, timeout=5, text=True).strip()
    info = Path("/").stat()
    guest = (observe("systemd-detect-virt") in ("kvm", "qemu")
             and Path("/proc/1/comm").read_text().strip() == "systemd"
             and Path("/run/ostree-booted").exists()
             and any(line in ('ID=bazzite', 'ID="bazzite"') for line in protected_read(Path("/usr/lib/os-release")).decode().splitlines())
             and all(p.name == "lo" or (p / "operstate").read_text().strip() != "up"
                     for p in Path("/sys/class/net").iterdir()))
    facts = Facts(protected_read(Path("/etc/machine-id")).decode().strip(),
                  f"{info.st_dev}:{info.st_ino}", protected_read(root / "candidate.sha").decode().strip(),
                  source_digest(root), hashlib.sha256(executable).hexdigest(), observe("getenforce"),
                  account.pw_uid, account.pw_gid, group, guest)
    authorize(manifest, facts)
    for link, literals in (("/opt", ("var/opt", "/var/opt")),
                            ("/usr/local", ("../var/usrlocal", "/var/usrlocal"))):
        if os.readlink(link) not in literals:
            raise RefusalError("unexpected Atomic redirect")
    for parent in ("/var/opt", "/var/usrlocal", "/var/usrlocal/bin"):
        info = Path(parent).lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or os.statvfs(parent).f_flag & os.ST_NOEXEC:
            raise RefusalError("unsafe/noexec canonical prefix")
    if not retained and os.path.lexists("/var/opt/xmrig"):
        raise RefusalError("foreign planned staging prefix already exists")
    target = os.environ.get("EVIDENCE_DIR", "")
    if not re.fullmatch(r"/var/tmp/sparehash-task3-[a-zA-Z0-9-]+", target) or (not retained and os.path.lexists(target)):
        raise RefusalError("new /var/tmp/sparehash-task3-NAME evidence path required")
    if retained:
        _ = protected_read(Path(target) / "authorization.json")
    print(f"{facts.uid}\n{facts.gid}\n{manifest.executable}\n{manifest.executable_sha256}")
    print(json.dumps({"manifest": asdict(manifest), "facts": asdict(facts), "helpers": helpers}))
    return manifest


def bound_identity(directory: Path, decode: Callable[..., Json] = json.loads) -> tuple[int, int]:
    raw = decode(read_raw(directory / "authorization.json"), object_pairs_hook=unique_pairs)
    if not isinstance(raw, dict) or set(raw) != {"manifest", "facts", "helpers"}:
        raise RefusalError("authorization record missing/malformed")
    helpers = raw["helpers"]
    if not isinstance(helpers, list) or not helpers or any(not isinstance(value, str) or not value.startswith("/usr/") for value in helpers):
        raise RefusalError("protected helper inventory unavailable")
    manifest = read_manifest(Path("/unused"), reader=lambda _: json.dumps(raw["manifest"]).encode())
    observed = raw["facts"]
    if not isinstance(observed, dict) or set(observed) != {field.name for field in fields(Facts)}:
        raise RefusalError("authorization facts missing/malformed")
    texts = {key: value for key, value in observed.items() if isinstance(value, str)}
    if set(texts) != {"machine_id", "root_identity", "candidate_sha", "source_sha256", "executable_sha256", "selinux", "group"}:
        raise RefusalError("authorization fact types invalid")
    uid, gid, guest = observed["uid"], observed["gid"], observed["guest"]
    if not isinstance(uid, int) or isinstance(uid, bool) or not isinstance(gid, int) or isinstance(gid, bool) or not isinstance(guest, bool):
        raise RefusalError("authorization identity types invalid")
    facts = Facts(texts["machine_id"], texts["root_identity"], texts["candidate_sha"],
                  texts["source_sha256"], texts["executable_sha256"], texts["selinux"], uid, gid, texts["group"], guest)
    authorize(manifest, facts)
    if read_raw(directory / "candidate.sha") != manifest.candidate_sha + "\n" or read_raw(directory / "identity") != f"{uid} {gid}\n":
        raise RefusalError("retained candidate/identity contradict authorization")
    return uid, gid


def summarize(directory: Path) -> None:
    summarize_raw(directory, bound_identity)


if __name__ == "__main__":
    try:
        if sys.argv[1] == "guard":
            _ = preflight(Path(sys.argv[2]))
        elif sys.argv[1] == "summarize":
            summarize(Path(sys.argv[2]))
        elif sys.argv[1] in ("stage", "cleanup"):
            from qa.atomic_probe_cleanup import cleanup_staging, create_staging
            manifest = preflight(Path(sys.argv[2]), retained=True)
            receipt = Path(os.environ["EVIDENCE_DIR"]) / "ownership"
            if sys.argv[1] == "stage":
                payload = protected_read(Path(manifest.executable), 64 * 1024 * 1024)
                if hashlib.sha256(payload).hexdigest() != manifest.executable_sha256:
                    raise RefusalError("candidate changed before exclusive staging")
                create_staging(Path("/var/opt"), receipt, payload)
            elif not cleanup_staging(Path("/var/opt"), receipt):
                raise RefusalError("partial staging removed; complete-run evidence unavailable")
        else:
            raise RefusalError("unknown internal operation")
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError, RefusalError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(1)

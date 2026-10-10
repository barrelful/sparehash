# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: sudo bash qa/qa_security.sh (installed disposable VM only).
"""Fail-closed QA assertions; no host installation or service lifecycle changes."""
from __future__ import annotations

import grp
import hashlib
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparehash import config, ipc, xmrig_detect, xmrig_verify as verify


class CheckFailure(Exception):
    """An asserted security boundary or required control failed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise CheckFailure(detail)


def command(argv: tuple[str, ...]) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def check_permissions(path: Path, identity: tuple[int, int, int], kind: int) -> None:
    """Require the exact mode/uid/gid and reject symlink substitutions."""
    info = path.lstat()
    require(stat.S_IFMT(info.st_mode) == kind, f"wrong file type: {path}")
    require((stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) == identity,
            f"wrong mode/ownership: {path}")


def check_tree(root: Path) -> None:
    """Inspect descendants and symlink targets, including external targets."""
    require(root.is_dir(), f"missing installed tree: {root}")
    pending = [root]
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(info.st_uid == 0, f"not root-owned: {path}")
        if path.is_symlink():
            target = path.resolve(strict=True)
            pending.append(target)
            # Writable ancestors could replace an otherwise protected target.
            for parent in target.parents:
                ancestor = parent.stat()
                require(ancestor.st_uid == 0 and not ancestor.st_mode & 0o022,
                        f"unsafe target ancestor: {parent}")
            continue
        require(not info.st_mode & 0o022, f"group/world writable: {path}")
        if path in seen:
            continue
        seen.add(path)
        if path.is_dir():
            pending.extend(path.iterdir())


def check_miner(pid: int, uid: int, binary: Path) -> None:
    """Check the live systemd MainPID, not a name-based process search."""
    require(pid > 0 and uid > 0, "miner MainPID and non-root xmrig uid required")
    process = Path(f"/proc/{pid}")
    status = process.joinpath("status").read_text(encoding="ascii")
    ids = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
    require(ids is not None and all(int(value) == uid for value in ids.groups()),
            "miner must run with all UIDs equal to xmrig")
    require(re.search(r"^State:\s+[ZX]", status, re.M) is None, "miner is not live")
    require(process.joinpath("exe").resolve(strict=True) == binary.resolve(strict=True),
            "miner MainPID executable is not configured XMRig")


def check_api(output: str, port: int, pid: int) -> None:
    """Only the configured port matters; unrelated SSH listeners are irrelevant."""
    require(0 < port <= 65535 and pid > 0, "invalid API port or miner PID")
    listeners: list[tuple[str, tuple[int, ...]]] = []
    for line in output.splitlines():
        fields = line.split()
        require(len(fields) >= 5 and fields[0] == "LISTEN", "malformed ss listener")
        address, separator, raw_port = fields[3].rpartition(":")
        require(bool(separator) and raw_port.isdecimal(), "malformed ss endpoint")
        if int(raw_port) == port:
            listeners.append((address, tuple(int(value) for value in re.findall(r"pid=(\d+)", line))))
    require(bool(listeners), f"API not listening on configured port {port}")
    require(all(address == "127.0.0.1" and owners == (pid,) for address, owners in listeners),
            "API must listen only on 127.0.0.1 and belong to miner MainPID")


def manual_blocker() -> str | None:
    raw = ipc.call({"cmd": "status"})
    require(raw.get("ok") is True and raw.get("ready") is True, "root IPC status unavailable/not ready")
    reasons = raw.get("reasons")
    match reasons:
        case dict():
            manual = reasons.get("manual")
            require(manual is None or isinstance(manual, str), "invalid manual blocker")
            return manual if isinstance(manual, str) else None
        case _:
            raise CheckFailure("missing blocker status")


def check_socket_access() -> None:
    """A real kernel EACCES is required; missing CLI/socket is not denial evidence."""
    nobody = pwd.getpwnam("nobody")
    require(nobody.pw_uid != 0, "nobody must be unprivileged")
    command(("sparehash", "status", "--json"))
    before = manual_blocker()
    require(before is None, "running-miner control requires manual blocker clear")
    probe = '''import errno,os,socket,sys
assert os.geteuid() == int(sys.argv[1]) != 0
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(2)
    try:
        connection.connect('/run/sparehash/control.sock')
        connection.sendall(b'{"cmd":"pause"}\\n')
    except OSError as error:
        sys.exit(0 if error.errno == errno.EACCES else 2)
    sys.exit(3)
'''
    result = subprocess.run(("runuser", "-u", "nobody", "--", "/usr/bin/python3", "-I", "-c", probe,
                             str(nobody.pw_uid)), check=False, capture_output=True, text=True, timeout=10)
    require(manual_blocker() == before, "denied pause changed manual blocker")
    require(result.returncode == 0, f"nobody socket probe did not get EACCES (exit {result.returncode})")


def check_release() -> None:
    """Real positive verification followed by archive and signed-metadata corruption."""
    with tempfile.TemporaryDirectory(prefix="sparehash-security-") as directory:
        root = Path(directory)
        home = root / "keys"
        home.mkdir(mode=0o700)

        def gpg(args: tuple[str, ...]) -> bytes:
            return subprocess.run(("/usr/bin/gpg", "--no-options", "--homedir", str(home), "--batch",
                                   "--yes", "--pinentry-mode", "loopback", "--passphrase", "", *args),
                                  check=True, capture_output=True, timeout=30).stdout

        try:
            gpg(("--quick-generate-key", "QA fixture <qa@example.invalid>", "ed25519", "cert", "0"))
            listing = gpg(("--with-colons", "--list-keys")).decode()
            fingerprints = [line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")]
            require(len(fingerprints) == 1, "ambiguous QA test certificate")
            fingerprint = fingerprints[0]
            gpg(("--quick-add-key", fingerprint, "ed25519", "sign", "0"))
            public = gpg(("--armor", "--export", fingerprint))
            archive, sums, signature = (root / name for name in ("fixture.tar.gz", "SHA256SUMS", "SHA256SUMS.sig"))
            content = b"disposable QA archive; never executed"
            signed = f"{hashlib.sha256(content).hexdigest()}  {archive.name}\n".encode()
            archive.write_bytes(content)
            sums.write_bytes(signed)
            gpg(("--local-user", fingerprint, "--output", str(signature), "--detach-sign", str(sums)))
            signature_bytes = signature.read_bytes()
            # Only this process-local fixture pin is patched; production trust is unchanged.
            with patch.object(verify, "XMRIG_GPG_FINGERPRINT", fingerprint):
                verify.verify_release(archive, sums, signature, public)
                require(archive.read_bytes() == content, "valid release control not retained")
                for stage in ("sha256", "signature"):
                    archive.write_bytes(content + b"corrupt" if stage == "sha256" else content)
                    sums.write_bytes(signed + b"corrupt\n" if stage == "signature" else signed)
                    signature.write_bytes(signature_bytes)
                    try:
                        verify.verify_release(archive, sums, signature, public)
                    except verify.VerifyError as error:
                        require(error.stage == stage, f"expected {stage} rejection, got {error.stage}")
                    else:
                        raise CheckFailure(f"verification accepted {stage} corruption")
        finally:
            command(("gpgconf", "--homedir", str(home), "--kill", "all"))
    old = xmrig_detect.parse_version("XMRig 6.21.1")
    newer = xmrig_detect.parse_version("XMRig 6.26.0")
    if old is None or newer is None:
        raise CheckFailure("version controls must parse successfully")
    require(old == (6, 21, 1) and not xmrig_detect.is_supported(old), "6.21.1 must parse and be rejected")
    require(newer == (6, 26, 0) and xmrig_detect.is_supported(newer), "newer version positive control failed")


def main() -> int:
    try:
        require(os.getuid() == 0 and os.geteuid() == 0, "security QA requires real/effective root")
        require("SPAREHASH_RUN_DIR" not in os.environ, "QA socket override is forbidden")
        spare_gid = grp.getgrnam("sparehash").gr_gid
        miner = pwd.getpwnam("xmrig")
        for name, mode, gid in (("config.toml", 0o644, spare_gid), ("xmrig.json", 0o640, spare_gid),
                                ("xmrig.runtime.json", 0o640, grp.getgrnam("xmrig").gr_gid)):
            check_permissions(Path("/etc/sparehash") / name, (mode, 0, gid), stat.S_IFREG)
        check_permissions(Path("/run/sparehash/control.sock"), (0o660, 0, spare_gid), stat.S_IFSOCK)
        check_permissions(Path("/run/sparehash"), (0o750, 0, spare_gid), stat.S_IFDIR)
        cfg = config.load()
        for tree in (Path("/opt/sparehash"), Path("/opt/xmrig")):
            check_tree(tree)
        binary = Path(cfg.xmrig.binary)
        check_tree(binary.parent)
        pid = int(command(("systemctl", "show", "sparehash-xmrig.service", "--property=MainPID", "--value")))
        require(command(("systemctl", "is-active", "sparehash-xmrig.service")) == "active", "miner inactive")
        check_miner(pid, miner.pw_uid, binary)
        check_api(command(("ss", "-H", "-tlnp")), cfg.api.port, pid)
        print("PASS: exact permissions, protected trees, live xmrig MainPID and loopback API")
        check_socket_access()
        print("PASS: root status works; nobody gets kernel EACCES; manual blocker unchanged")
        check_release()
        print("PASS: valid signed release, sha256/signature rejection and version controls")
        return 0
    except (CheckFailure, OSError, KeyError, ValueError, subprocess.SubprocessError,
            config.ConfigError, ipc.IPCError, verify.VerifyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Portable probe identity and effective unit ownership; no real manager commands."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sparehash import install_xmrig, systemd
from sparehash.deployment import DeploymentError
from sparehash.sysdetect import Capabilities
from sparehash.xmrig_config import (
    PROPERTIES,
    DryRunRequest,
    DryRunResult,
    JsonObject,
    RenderError,
    validate_rendered,
)


def capabilities() -> Capabilities:
    return Capabilities("x86_64", True, 252, True, "cgroup2", True, True, False,
                        (0,), (), 4, 4096, None, None, True)


def identity_root(base: Path, *, uid: int = 65534, gid: int = 65534, groups: str | None = None) -> Path:
    etc = base / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    _ = (etc / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        f"nobody:x:{uid}:{gid}:nobody:/nonexistent:/usr/sbin/nologin\n", encoding="utf-8")
    _ = (etc / "group").write_text(
        groups if groups is not None else f"root:x:0:\nnogroup:x:{gid}:\n", encoding="utf-8")
    return base


def unit_root(base: Path) -> Path:
    (base / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
    return base


class RecordingRunner:
    """Mutable injected runner that never executes candidate bytes."""

    def __init__(self, result: DryRunResult) -> None:
        self.result = result
        self.requests: list[DryRunRequest] = []

    def __call__(self, request: DryRunRequest) -> DryRunResult:
        self.requests.append(request)
        return self.result


def test_accepts_validated_primary_group() -> None:
    # Given a dedicated nobody account with a safe primary group
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp))
        # When / Then the actual UID/GID are resolved, not assumed
        assert install_xmrig.resolve_probe_identity(root) == (65534, 65534)
    # Given a non-conventional but safe unprivileged identity on another distro
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp), uid=12345, gid=54321, groups="root:x:0:\nnogroup:x:54321:\n")
        # When / Then the real values are returned instead of a numeric guess
        assert install_xmrig.resolve_probe_identity(root) == (12345, 54321)


def test_resolves_accounts_from_atomic_altfiles() -> None:
    # Given a Bazzite-style root whose /etc/passwd is a stub and the account lives in /usr/lib
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "etc").mkdir(parents=True, exist_ok=True)
        (base / "usr/lib").mkdir(parents=True, exist_ok=True)
        _ = (base / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
        _ = (base / "etc/group").write_text("root:x:0:\n", encoding="utf-8")
        _ = (base / "usr/lib/passwd").write_text(
            "nobody:x:99:99:Nobody:/:/usr/sbin/nologin\n", encoding="utf-8")
        _ = (base / "usr/lib/group").write_text("nobody:x:99:\n", encoding="utf-8")
        # When / Then the NSS-visible account is found even though /etc is a stub
        assert install_xmrig.resolve_probe_identity(base) == (99, 99)


def test_etc_account_database_wins_over_altfiles() -> None:
    # Given an account present in both databases with different ids
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp))
        (root / "usr/lib").mkdir(parents=True, exist_ok=True)
        _ = (root / "usr/lib/passwd").write_text(
            "nobody:x:1234:1234:x:/nonexistent:/usr/sbin/nologin\n", encoding="utf-8")
        # When / Then the mutable /etc entry takes precedence, as NSS does
        assert install_xmrig.resolve_probe_identity(root) == (65534, 65534)


def test_accepts_owned_units_and_threads_expected_probe_uid() -> None:
    # Given the rendered SpareHash units installed under a synthetic root
    units = systemd.render(capabilities())
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        for name, text in units.items():
            _ = (root / "etc/systemd/system" / name).write_text(text, encoding="utf-8")
        # When / Then full effective unit ownership validation passes
        systemd.validate_units(root)
    # Given a dry-run runner attesting a non-conventional resolved identity
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "xmrig"
        _ = candidate.write_bytes(b"candidate is never executed")
        rendered: JsonObject = {"http": {}}
        runner = RecordingRunner(DryRunResult(0, 1234, PROPERTIES, True))
        # When the expected UID is threaded instead of hardcoded
        validate_rendered(rendered, candidate, runner, probe_uid=1234)
        # Then the request carries the resolved identity and it is compared
        assert runner.requests[0].uid == 1234
    # Given a runner that attests a different effective UID
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "xmrig"
        _ = candidate.write_bytes(b"candidate is never executed")
        mismatched = RecordingRunner(DryRunResult(0, 65534, PROPERTIES, True))
        # When / Then the mismatch fails closed rather than being guessed away
        with pytest.raises(RenderError):
            validate_rendered({"http": {}}, candidate, mismatched, probe_uid=1234)


def test_rejects_unsafe_identity_or_units() -> None:
    # UID 0: nobody must never resolve to root
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp), uid=0, gid=0, groups="root:x:0:\nnogroup:x:65534:\n")
        with pytest.raises(DeploymentError):
            _ = install_xmrig.resolve_probe_identity(root)
    # Sensitive supplementary membership: nobody is in sudo
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp), groups="root:x:0:\nnogroup:x:65534:\nsudo:x:27:nobody\n")
        with pytest.raises(DeploymentError):
            _ = install_xmrig.resolve_probe_identity(root)
    # Sensitive primary group: nobody's primary group is wheel
    with tempfile.TemporaryDirectory() as tmp:
        root = identity_root(Path(tmp), groups="root:x:0:\nwheel:x:65534:\n")
        with pytest.raises(DeploymentError):
            _ = install_xmrig.resolve_probe_identity(root)
    # Absent account: no dedicated nobody identity exists
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "etc").mkdir()
        _ = (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
        _ = (root / "etc/group").write_text("root:x:0:\n", encoding="utf-8")
        with pytest.raises(DeploymentError):
            _ = install_xmrig.resolve_probe_identity(root)
    # Policy/link denial: a substituted identity file is never followed
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "etc").mkdir()
        real = root / "passwd.real"
        _ = real.write_text("nobody:x:65534:65534::/:/nologin\n", encoding="utf-8")
        _ = (root / "etc/passwd").symlink_to(real)
        _ = (root / "etc/group").write_text("nogroup:x:65534:\n", encoding="utf-8")
        with pytest.raises(DeploymentError):
            _ = install_xmrig.resolve_probe_identity(root)
    # Foreign marked unit: a marked unit that is not one of ours
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        _ = (root / "etc/systemd/system/evil.service").write_text(
            systemd.MARKER + "\n[Service]\nExecStart=+/usr/local/bin/evil\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)
    # Missing ExecCondition/credential on our marked miner unit
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        _ = (root / "etc/systemd/system/sparehash-xmrig.service").write_text(
            systemd.MARKER + "\n[Service]\nType=simple\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)
    # Unsafe ExecCondition path on an otherwise valid marked miner unit
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        _ = (root / "etc/systemd/system/sparehash-xmrig.service").write_text(
            systemd.MARKER + "\n[Service]\nExecCondition=+/usr/local/bin/other __authorize_miner__\n"
            f"{systemd.CREDENTIAL}\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)
    # Foreign drop-in that could weaken our units
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        override = root / "etc/systemd/system/sparehash.service.d"
        override.mkdir()
        _ = (override / "override.conf").write_text("[Service]\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)
    # Foreign xmrig.service is never tolerated
    with tempfile.TemporaryDirectory() as tmp:
        root = unit_root(Path(tmp))
        _ = (root / "etc/systemd/system/xmrig.service").write_text("[Service]\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)
    # Root-exec injection in a foreign unit under the system search path
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "usr/lib/systemd/system").mkdir(parents=True)
        _ = (root / "usr/lib/systemd/system/helper.service").write_text(
            "[Service]\nExecStart=+/usr/local/bin/helper\n", encoding="utf-8")
        with pytest.raises(systemd.UnitError):
            systemd.validate_units(root)


@pytest.mark.parametrize("probe", ["version", "config"])
@pytest.mark.parametrize("outcome", ["success", "rejected", "error"])
def test_probe_stages_executable_snapshot_outside_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: str, outcome: str,
) -> None:
    # Given protected snapshot bytes and an established executable application root
    root = identity_root(tmp_path, uid=99, gid=99)
    application = tmp_path / "var/tmp/sparehash-probe"
    application.mkdir(parents=True)
    snapshot = tmp_path / "snapshot"
    snapshot.write_bytes(b"private candidate snapshot")
    config = tmp_path / "config.json"
    config.write_bytes(b'{"http":{"access-token":"' + b"0" * 64 + b'"}}')
    temporary_directory = tempfile.TemporaryDirectory
    staged: list[Path] = []

    def stage_directory(*, prefix: str, dir: str) -> tempfile.TemporaryDirectory[str]:
        # The application tree is executable; the state tree need not be.
        assert dir == "/var/tmp/sparehash-probe"
        return temporary_directory(prefix=prefix, dir=application)

    def isolated(binary: Path, arguments: tuple[str, ...], *, root: Path) -> tuple[int, str]:
        assert root == tmp_path
        assert binary.parent.parent == application
        assert binary.read_bytes() == snapshot.read_bytes()
        assert binary.stat().st_mode & 0o777 == 0o555
        assert binary.parent.stat().st_mode & 0o777 == 0o755
        staged.append(binary.parent)
        if probe == "config":
            copied_config = Path(arguments[1])
            assert arguments == ("--config", str(copied_config), "--dry-run")
            assert copied_config.parent == binary.parent
            assert copied_config.read_bytes() == config.read_bytes()
            assert copied_config.stat().st_mode & 0o777 == 0o444
        else:
            assert arguments == ("--version",)
        if outcome == "error":
            raise DeploymentError("probe", "injected sandbox failure")
        return (203, "") if outcome == "rejected" else (0, "XMRig 6.26.0")

    monkeypatch.setattr(install_xmrig.tempfile, "TemporaryDirectory", stage_directory)
    monkeypatch.setattr(install_xmrig, "isolated", isolated)

    def invoke() -> str | None | DryRunResult:
        if probe == "version":
            return install_xmrig.version_probe(
                snapshot, timeout=15, uid=99, required_properties=PROPERTIES, root=root,
            )
        return install_xmrig.dry_run(DryRunRequest(snapshot, config, uid=99), root=root)

    # When either production adapter succeeds, rejects, or raises
    if outcome == "error":
        with pytest.raises(DeploymentError):
            invoke()
    else:
        result = invoke()
        if probe == "config":
            assert result == DryRunResult(
                203 if outcome == "rejected" else 0, 99, PROPERTIES, True,
                "" if outcome == "rejected" else "XMRig 6.26.0",
            )
        else:
            assert result == (None if outcome == "rejected" else "XMRig 6.26.0")
    # Then cleanup occurs for every outcome and the original inputs survive
    assert len(staged) == 1
    assert not staged[0].exists()
    assert snapshot.read_bytes() == b"private candidate snapshot"
    assert config.exists()

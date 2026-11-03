"""Package checks coexist with the complete-source archive/signature assets."""
import io
import tarfile
import zipfile
from pathlib import Path

from qa.check_artifacts import check


def test_package_checker_distinguishes_complete_source_asset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "sparehash").mkdir(parents=True)
    (source / "sparehash/example.py").write_bytes(b"# module\n")
    (source / "LICENSE").write_bytes(b"GPL fixture\n")
    (source / "THIRD_PARTY_NOTICES.md").write_bytes(b"attribution fixture\n")
    with zipfile.ZipFile(tmp_path / "sparehash-0.3.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("sparehash/example.py", b"# module\n")
        wheel.writestr("sparehash/THIRD_PARTY_NOTICES.md", b"attribution fixture\n")
        wheel.writestr("sparehash-0.3.0.dist-info/licenses/LICENSE", b"GPL fixture\n")
        wheel.writestr("sparehash-0.3.0.dist-info/METADATA", b"Metadata-Version: 2.4\nName: sparehash\nVersion: 0.3.0\n")
    with tarfile.open(tmp_path / "sparehash-0.3.0.tar.gz", "w:gz") as archive:
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            payload = (source / name).read_bytes()
            info = tarfile.TarInfo("sparehash-0.3.0/" + name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    (tmp_path / "sparehash-0.3.0-source.tar.gz").write_bytes(b"separate complete-source asset")
    (tmp_path / "sparehash-0.3.0-source.tar.gz.asc").write_bytes(b"separate signature")
    check(tmp_path, source=source)

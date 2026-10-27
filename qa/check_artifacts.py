"""Read-only wheel/sdist licence, source and dependency checks (stdlib only)."""

import email
import sys
import tarfile
import zipfile
from pathlib import Path


def check(directory: Path) -> None:
    wheel, = directory.glob("*.whl")
    sdist, = directory.glob("*.tar.gz")
    source = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(wheel) as archive:
        metadata = email.message_from_bytes(archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))))
        assert not metadata.get_all("Requires-Dist"), "mandatory runtime dependencies are forbidden"
        assert archive.read("sparehash/THIRD_PARTY_NOTICES.md") == (source / "THIRD_PARTY_NOTICES.md").read_bytes()
        licence = next(n for n in archive.namelist() if n.endswith("/licenses/LICENSE"))
        assert archive.read(licence) == (source / "LICENSE").read_bytes()
        for path in (source / "sparehash").glob("*.py"):
            assert archive.read("sparehash/" + path.name) == path.read_bytes(), path.name
    with tarfile.open(sdist) as archive:
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            member = next(n for n in archive.getnames() if n.endswith("/" + name))
            assert archive.extractfile(member).read() == (source / name).read_bytes()
    print("Artifacts passed: source, GPL/attribution, no runtime dependencies")


if __name__ == "__main__":
    check(Path(sys.argv[1]))

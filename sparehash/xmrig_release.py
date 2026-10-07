"""Bounded HTTPS-only XMRig release transport (stdlib, no artifact execution)."""
from __future__ import annotations

import io
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path
from typing import BinaryIO, Final, Protocol, TypeAlias, final
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPErrorProcessor, Request, build_opener

from . import __version__

LATEST_URL: Final = "https://api.github.com/repos/xmrig/xmrig/releases/latest"
METADATA_LIMIT: Final = 2 * 1024 * 1024
DOWNLOAD_LIMIT: Final = 128 * 1024 * 1024
DNS_HINT: Final = "if you use a filtered DNS provider, github.com must resolve"
Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
_decode: Callable[[bytes], Json] = json.loads


class FetchError(Exception):
    """Transport or release-contract failure, safe for callers to display."""
    def __init__(self, detail: str, retryable: bool = False) -> None:
        self.detail: str = detail
        self.retryable: bool = retryable
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Release:
    tag_name: str
    assets: tuple[Asset, ...]


class Response(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...
    def geturl(self) -> str: ...
    def getcode(self) -> int: ...
    def getheader(self, name: str, default: str | None = None) -> str | None: ...


class Opener(Protocol):
    def __call__(self, request: Request, timeout: float) -> AbstractContextManager[Response]: ...


def _identity_response(self: HTTPErrorProcessor, request: Request, response: HTTPResponse) -> HTTPResponse:
    _ = self, request
    return response


@final
class ManualRedirects(HTTPErrorProcessor):
    """Return statuses to our bounded loop before urllib can follow a redirect."""
    http_response = _identity_response
    https_response = _identity_response


class UrlTransport(Protocol):
    def open(self, fullurl: Request, data: None = None, timeout: float = 30) -> AbstractContextManager[Response]: ...


_TRANSPORT: Final[UrlTransport] = build_opener(ManualRedirects())


def _open(request: Request, timeout: float,
          transport: UrlTransport = _TRANSPORT) -> AbstractContextManager[Response]:
    return transport.open(request, timeout=timeout)


def _https(url: str) -> str:
    """Reject URL ambiguity before initial requests and every redirect hop."""
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and bool(parts.hostname)
                 and parts.username is None and parts.password is None
                 and parts.port in (None, 443) and not parts.fragment
                 and not re.search(r"[\s\\\x00-\x1f\x7f]", url))
    except ValueError as exc:
        raise FetchError("Malformed HTTPS URL") from exc
    if not valid:
        raise FetchError("Only unambiguous HTTPS URLs are allowed")
    return url


def _asset_url(url: str, tag: str, name: str) -> str:
    _ = _https(url)
    if url != f"https://github.com/xmrig/xmrig/releases/download/{tag}/{name}":
        raise FetchError(f"Malformed or foreign release URL for {name}")
    return url


def _parse_release(data: bytes) -> Release:
    """Parse untrusted JSON once into immutable release values."""
    try:
        raw = _decode(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FetchError("Invalid GitHub release JSON; retry later") from exc
    if not isinstance(raw, dict):
        raise FetchError("Release metadata must be an object")
    tag = raw.get("tag_name")
    entries = raw.get("assets")
    if not isinstance(tag, str) or re.fullmatch(r"v\d+\.\d+\.\d+", tag) is None:
        raise FetchError("Release tag must be v<major>.<minor>.<patch>")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise FetchError("Release assets must be a bounded array")
    assets: list[Asset] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise FetchError("Malformed release asset")
        name, url = entry.get("name"), entry.get("browser_download_url")
        if (not isinstance(name, str) or not isinstance(url, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,199}", name) is None
                or name in names):
            raise FetchError("Malformed or duplicate release asset name/URL")
        names.add(name)
        assets.append(Asset(name, _asset_url(url, tag, name)))
    return Release(tag, tuple(assets))


def select_asset(assets: Sequence[Asset], arch: str = "x86_64") -> tuple[str, str, str]:
    """Select a Linux x64 archive plus the mandatory signed checksum pair."""
    if arch != "x86_64":
        raise FetchError("SpareHash v1 supports x86_64 only")
    by_name = {asset.name: asset.url for asset in assets}
    for required in ("SHA256SUMS", "SHA256SUMS.sig"):
        if required not in by_name:
            raise FetchError(f"Release is missing {required}")
    for flavor in ("linux-static-x64", "linux-x64"):
        candidates = [a for a in assets if flavor in a.name and a.name.endswith(".tar.gz")]
        if len(candidates) > 1:
            raise FetchError(f"Ambiguous {flavor} release assets")
        if candidates:
            selected = (candidates[0].url, by_name["SHA256SUMS"], by_name["SHA256SUMS.sig"])
            for url in selected:
                _ = _https(url)
            return selected
    raise FetchError("Release has no Linux x86_64 tarball")


@dataclass(frozen=True, slots=True)
class ReleaseClient:
    """Reusable transport seams; two retries, five redirects, 120-second budget."""
    opener: Opener = _open
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def transfer(self, url: str, output: BinaryIO, limit: int) -> int:
        # Four parameters include self; output and limit are the streaming contract.
        _ = _https(url)
        deadline = self.clock() + 120
        for attempt in range(3):
            _ = output.seek(0)
            _ = output.truncate()
            current, total = url, 0
            try:
                for hop in range(6):
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise FetchError("GitHub transfer time budget exceeded")
                    request = Request(current, headers={"User-Agent": f"sparehash/{__version__}",
                                      "Accept": "application/vnd.github+json"})
                    with self.opener(request, min(30, remaining)) as response:
                        _ = _https(response.geturl())
                        status = response.getcode()
                        if status in (301, 302, 303, 307, 308):
                            location = response.getheader("Location")
                            if not location or hop == 5:
                                raise FetchError("Missing redirect target or too many redirects")
                            current = _https(urljoin(current, location))
                            if urlsplit(current).hostname not in {
                                "api.github.com", "github.com", "raw.githubusercontent.com",
                                "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                            }:
                                raise FetchError("Foreign GitHub redirect target")
                            continue
                        if status != 200:
                            raise FetchError(f"GitHub HTTP {status}; check access/rate limits and retry later",
                                             status == 429 or 500 <= status <= 599)
                        length = response.getheader("Content-Length")
                        if length is not None and (not length.isdecimal() or int(length) > limit):
                            raise FetchError("Invalid or oversized Content-Length")
                        while True:
                            if self.clock() >= deadline:
                                raise FetchError("GitHub transfer time budget exceeded")
                            chunk = response.read(min(65536, limit - total + 1))
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > limit:
                                raise FetchError("GitHub response exceeds size limit")
                            _ = output.write(chunk)
                        if length is not None and total != int(length):
                            raise FetchError("Truncated GitHub response; retry later", True)
                        return total
            except FetchError as exc:
                if not exc.retryable or attempt == 2:
                    raise
            except (URLError, OSError, IncompleteRead) as exc:
                if attempt == 2:
                    raise FetchError(f"GitHub transfer failed: {exc}; {DNS_HINT}") from exc
            self.sleep((1.0, 2.0, 4.0)[attempt])
        raise FetchError("GitHub transfer exhausted retries")

    def fetch_latest_release(self) -> Release:
        with io.BytesIO() as output:
            _ = self.transfer(LATEST_URL, output, METADATA_LIMIT)
            return _parse_release(output.getvalue())

    def download(self, url: str, dest: Path, expected_size: int | None = None) -> None:
        """Exclusively create the exact caller path; remove incomplete output."""
        if expected_size is not None and not 0 <= expected_size <= DOWNLOAD_LIMIT:
            raise FetchError("Expected download size is outside the allowed range")
        _ = _https(url)
        created, complete = False, False
        try:
            with dest.open("xb") as output:
                created = True
                size = self.transfer(url, output, DOWNLOAD_LIMIT)
                if expected_size is not None and size != expected_size:
                    raise FetchError("Downloaded size does not match expected_size")
                complete = True
        except OSError as exc:
            raise FetchError(f"Cannot write caller destination {dest}: {exc}") from exc
        finally:
            if created and not complete:
                dest.unlink(missing_ok=True)


def fetch_latest_release(opener: Opener | None = None) -> Release:
    return ReleaseClient(opener=opener or _open).fetch_latest_release()


# Signature retained from the plan; progress is reserved and never prints artifact data.
def download(url: str, dest: Path, expected_size: int | None = None, progress: bool = False) -> None:
    _ = progress
    ReleaseClient().download(url, dest, expected_size)


if __name__ == "__main__":
    if sys.argv[1:] != ["--selfcheck"]:
        sys.exit("Usage: python3 -m sparehash.xmrig_release --selfcheck")
    try:
        release = fetch_latest_release()
        print(release.tag_name, *select_asset(release.assets), sep="\n")
    except FetchError as error:
        sys.exit(str(error))

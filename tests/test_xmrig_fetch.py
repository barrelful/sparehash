"""Deterministic release transport contract tests; no artifact network traffic."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

from sparehash import __version__, xmrig
from sparehash.xmrig_release import (
    Asset, DNS_HINT, FetchError, LATEST_URL, ReleaseClient, ManualRedirects, Response,
    fetch_latest_release, select_asset,
)

BASE = "https://github.com/xmrig/xmrig/releases/download/v6.26.0/"
STATIC = "xmrig-6.26.0-linux-static-x64.tar.gz"
DYNAMIC = "xmrig-6.26.0-linux-x64.tar.gz"
NAMES = (DYNAMIC, STATIC, "SHA256SUMS", "SHA256SUMS.sig")


def metadata(names: tuple[str, ...] = NAMES) -> bytes:
    return json.dumps({"tag_name": "v6.26.0", "assets": [
        {"name": name, "browser_download_url": BASE + name} for name in names
    ]}).encode()


class FakeResponse:
    def __init__(self, data: bytes, url: str = LATEST_URL, status: int = 200) -> None:
        self.stream: io.BytesIO = io.BytesIO(data)
        self.fail_after_first: bool = False
        self.url: str = url
        self.status: int = status
        self.headers: dict[str, str] = {}

    def read(self, size: int = -1, /) -> bytes:
        if self.fail_after_first and self.stream.tell():
            raise URLError("connection reset")
        return self.stream.read(2 if self.fail_after_first else size)

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)


class FakeOpener:
    """Queue of wire responses/errors, with observable requests and timeouts."""
    def __init__(self, *responses: FakeResponse | URLError) -> None:
        self.responses: list[FakeResponse | URLError] = list(responses)
        self.calls: list[tuple[Request, float]] = []

    @contextmanager
    def __call__(self, request: Request, timeout: float) -> Iterator[Response]:
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, URLError):
            raise response
        with response.stream:
            yield response


class FetchTests(unittest.TestCase):
    def test_facade_when_selfcheck_prints_selected_release(self) -> None:
        # Given
        opener = FakeOpener(FakeResponse(metadata()))
        output = io.StringIO()
        # When
        with redirect_stdout(output):
            code = xmrig.main(["--selfcheck"], opener)
        # Then
        self.assertEqual((code, output.getvalue().splitlines()), (0, [
            "v6.26.0", BASE + STATIC, BASE + "SHA256SUMS", BASE + "SHA256SUMS.sig"]))

    def test_facade_when_invalid_args_rejected_before_fetch(self) -> None:
        # Given
        for args in ([], ["--bad"], ["--selfcheck", "extra"]):
            opener, error = FakeOpener(), io.StringIO()
            # When
            with self.subTest(args=args), redirect_stderr(error):
                code = xmrig.main(args, opener)
            # Then
            self.assertEqual((code, opener.calls), (2, []))
            self.assertTrue(error.getvalue())

    def test_facade_when_run_as_module_dispatches_main(self) -> None:
        # Given
        command = [sys.executable, "-m", "sparehash.xmrig", "--bad"]
        # When
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        # Then
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertTrue(result.stderr)

    def test_facade_when_fetch_fails_returns_error_without_success_output(self) -> None:
        # Given
        output, error = io.StringIO(), io.StringIO()
        opener = FakeOpener(FakeResponse(b"invalid json"))
        # When
        with redirect_stdout(output), redirect_stderr(error):
            code = xmrig.main(["--selfcheck"], opener)
        # Then
        self.assertEqual((code, output.getvalue()), (1, ""))
        self.assertTrue(error.getvalue())

    def test_release_when_metadata_valid(self) -> None:
        # Given
        opener = FakeOpener(FakeResponse(metadata()))
        # When
        release = fetch_latest_release(opener)
        # Then
        self.assertEqual(release.tag_name, "v6.26.0")
        self.assertEqual(tuple(a.name for a in release.assets), NAMES)

    def test_request_when_fetching_has_required_headers_and_timeout(self) -> None:
        # Given
        opener = FakeOpener(FakeResponse(metadata()))
        # When
        _ = fetch_latest_release(opener)
        # Then
        request, timeout = opener.calls[0]
        self.assertEqual((request.full_url, timeout), (LATEST_URL, 30))
        self.assertEqual(request.get_header("User-agent"), f"sparehash/{__version__}")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")

    def test_selection_when_static_or_fallback_available(self) -> None:
        # Given
        for names, selected in ((NAMES, STATIC), ((DYNAMIC, *NAMES[2:]), DYNAMIC)):
            with self.subTest(names=names):
                assets = tuple(Asset(name, BASE + name) for name in names)
                # When
                result = select_asset(assets)
                # Then
                self.assertEqual(result, (BASE + selected, BASE + "SHA256SUMS", BASE + "SHA256SUMS.sig"))

    def test_selection_when_required_asset_missing(self) -> None:
        # Given
        for missing in (STATIC, "SHA256SUMS", "SHA256SUMS.sig"):
            names = (STATIC, *NAMES[2:])
            assets = tuple(Asset(n, BASE + n) for n in names if n != missing)
            # When / Then
            with self.subTest(missing=missing), self.assertRaises(FetchError):
                _ = select_asset(assets)

    def test_selection_when_architecture_unsupported(self) -> None:
        # Given
        assets = tuple(Asset(n, BASE + n) for n in NAMES)
        # When / Then
        with self.assertRaisesRegex(FetchError, "^SpareHash v1 supports x86_64 only$"):
            _ = select_asset(assets, "aarch64")

    def test_release_when_json_contract_invalid(self) -> None:
        # Given
        invalid = (b"not json", b"[]", b'{"tag_name":"../../evil","assets":[]}',
                   b'{"tag_name":"v6.26.0","assets":{}}',
                   b'{"tag_name":"v6.26.0","assets":[null]}',
                   b'{"tag_name":"v6.26.0","assets":[{"name":3}]}')
        for data in invalid:
            opener = FakeOpener(FakeResponse(data))
            # When / Then
            with self.subTest(data=data), self.assertRaises(FetchError):
                _ = fetch_latest_release(opener)

    def test_release_when_asset_url_or_name_invalid(self) -> None:
        # Given
        for url in ("http://github.com/a", "https://evil.test/a", BASE + "wrong",
                    BASE + STATIC + "?x=1", "https://github.com:bad/a"):
            data = json.dumps({"tag_name": "v6.26.0", "assets": [
                {"name": STATIC, "browser_download_url": url}]}).encode()
            # When / Then
            with self.subTest(url=url), self.assertRaises(FetchError):
                _ = fetch_latest_release(FakeOpener(FakeResponse(data)))
        with self.assertRaises(FetchError):
            _ = fetch_latest_release(FakeOpener(FakeResponse(metadata(("../evil",)))))
        with self.assertRaises(FetchError):
            _ = fetch_latest_release(FakeOpener(FakeResponse(metadata((STATIC, STATIC)))))

    def test_retry_when_dns_fails_is_bounded_with_hint(self) -> None:
        # Given
        opener = FakeOpener(*(URLError("name resolution failed") for _ in range(3)))
        sleeps: list[float] = []
        client = ReleaseClient(opener, lambda: 0, sleeps.append)
        # When
        with self.assertRaises(FetchError) as caught:
            _ = client.fetch_latest_release()
        # Then
        self.assertIn(DNS_HINT, str(caught.exception))
        self.assertEqual((len(opener.calls), sleeps), (3, [1, 2]))

    def test_retry_when_transient_status_then_success(self) -> None:
        # Given
        opener = FakeOpener(FakeResponse(b"", status=503), FakeResponse(metadata()))
        sleeps: list[float] = []
        # When
        result = ReleaseClient(opener, lambda: 0, sleeps.append).fetch_latest_release()
        # Then
        self.assertEqual((result.tag_name, sleeps), ("v6.26.0", [1]))

    def test_redirect_when_https_follows_manually(self) -> None:
        # Given
        redirect = FakeResponse(b"", status=302)
        redirect.headers["Location"] = "https://api.github.com/next"
        opener = FakeOpener(redirect, FakeResponse(metadata(), url="https://api.github.com/next"))
        # When
        _ = fetch_latest_release(opener)
        # Then
        self.assertEqual(opener.calls[1][0].full_url, "https://api.github.com/next")
        self.assertIs(ManualRedirects.http_response, ManualRedirects.https_response)

    def test_redirect_when_bad_target_never_requests_it(self) -> None:
        # Given
        for target in ("http://evil.test/a", "https://evil.test/a", "file:///etc/passwd", "https://user@evil.test/a", ""):
            redirect = FakeResponse(b"", status=302)
            redirect.headers["Location"] = target
            opener = FakeOpener(redirect)
            # When
            with self.subTest(target=target), self.assertRaises(FetchError):
                _ = fetch_latest_release(opener)
            # Then
            self.assertEqual(len(opener.calls), 1)

    def test_response_when_oversized_or_truncated_rejected(self) -> None:
        # Given
        for length, data in (("999999999", b""), ("bad", b""), (None, b"12345")):
            response = FakeResponse(data)
            if length is not None:
                response.headers["Content-Length"] = length
            client = ReleaseClient(FakeOpener(response), lambda: 0, lambda _: None)
            # When / Then
            with self.subTest(length=length), io.BytesIO() as output, self.assertRaises(FetchError):
                _ = client.transfer(LATEST_URL, output, 4)

    def test_download_when_streaming_failure_removes_partial(self) -> None:
        # Given
        responses = [FakeResponse(b"abcdef") for _ in range(3)]
        for response in responses:
            response.fail_after_first = True
        client = ReleaseClient(FakeOpener(*responses),
                               lambda: 0, lambda _: None)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "download"
            # When
            with self.assertRaises(FetchError):
                client.download(BASE + STATIC, dest)
            # Then
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_download_when_success_writes_exact_destination(self) -> None:
        # Given
        client = ReleaseClient(FakeOpener(FakeResponse(b"abcdef")))
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chosen-name"
            # When
            client.download(BASE + STATIC, dest, 6)
            # Then
            self.assertEqual(dest.read_bytes(), b"abcdef")
            self.assertEqual(list(Path(tmp).iterdir()), [dest])

    def test_download_when_existing_file_or_symlink_preserves_it(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            _ = target.write_bytes(b"original")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            for dest in (target, link):
                opener = FakeOpener()
                # When
                with self.subTest(dest=dest), self.assertRaises(FetchError):
                    ReleaseClient(opener).download(BASE + STATIC, dest)
                # Then
                self.assertEqual((target.read_bytes(), opener.calls), (b"original", []))

    def test_download_when_size_mismatch_removes_output(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "download"
            # When
            with self.assertRaises(FetchError):
                ReleaseClient(FakeOpener(FakeResponse(b"abc"))).download(BASE + STATIC, dest, 4)
            # Then
            self.assertFalse(dest.exists())

    def test_redirect_when_loop_exhausted_is_bounded(self) -> None:
        # Given
        responses = [FakeResponse(b"", status=302) for _ in range(6)]
        for response in responses:
            response.headers["Location"] = LATEST_URL
        opener = FakeOpener(*responses)
        # When
        with self.assertRaises(FetchError):
            _ = fetch_latest_release(opener)
        # Then
        self.assertEqual(len(opener.calls), 6)

    def test_response_when_truncated_retries_without_appending(self) -> None:
        # Given
        first = FakeResponse(b"partial")
        first.headers["Content-Length"] = "20"
        client = ReleaseClient(FakeOpener(first, FakeResponse(metadata())), lambda: 0, lambda _: None)
        # When
        release = client.fetch_latest_release()
        # Then
        self.assertEqual(release.tag_name, "v6.26.0")

    def test_request_when_budget_expired_never_opens(self) -> None:
        # Given
        ticks = iter((0.0, 121.0))
        opener = FakeOpener()
        # When
        with self.assertRaises(FetchError):
            _ = ReleaseClient(opener, lambda: next(ticks)).fetch_latest_release()
        # Then
        self.assertEqual(opener.calls, [])

    def test_download_when_initial_url_insecure_never_creates_file(self) -> None:
        # Given
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            # When
            with self.assertRaises(FetchError):
                ReleaseClient(opener).download("http://github.com/a", Path(tmp) / "output")
            # Then
            self.assertEqual((list(Path(tmp).iterdir()), opener.calls), ([], []))

    def test_response_when_opener_reports_insecure_final_url_rejected(self) -> None:
        # Given
        opener = FakeOpener(FakeResponse(metadata(), url="http://github.com/a"))
        # When / Then
        with self.assertRaises(FetchError):
            _ = fetch_latest_release(opener)

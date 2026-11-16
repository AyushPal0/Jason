import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from codebase_intel.github import (
    GitHubRedirects, ImportFailure, analyze_github, download_archive,
    extract_archive, parse_github_url,
)


def archive_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


class GitHubTests(unittest.TestCase):
    def test_url_validation(self):
        for suffix in ("", "/", ".git", ".git/"):
            self.assertEqual(parse_github_url("https://github.com/AyushPal0/Jason" + suffix), ("AyushPal0", "Jason"))
        for url in (
            "http://github.com/a/b", "https://evil.test/a/b", "git@github.com:a/b.git",
            "https://github.com@evil.test/a/b", "https://github.com:443/a/b",
            "https://github.com/a/b/tree/main", "https://github.com/a/b?x=1",
            "https://github.com/a/b#x", "https://github.com/a/..",
            "https://github.com/a/%2e%2e", "https://github.com/a/.git",
        ):
            with self.subTest(url=url), self.assertRaises(ImportFailure):
                parse_github_url(url)

    def test_redirect_destination_validation(self):
        handler = GitHubRedirects()
        request = Request("https://api.github.com/repos/a/b/zipball")
        allowed = handler.redirect_request(request, None, 302, "Found", {}, "https://codeload.github.com/a/b/zip/main")
        self.assertEqual(allowed.host, "codeload.github.com")
        for url in ("http://codeload.github.com/a", "https://evil.test/a", "https://api.github.com@evil.test/a"):
            with self.subTest(url=url), self.assertRaises(ImportFailure):
                handler.redirect_request(request, None, 302, "Found", {}, url)

    def test_download_limit_and_http_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "archive.zip"
            with patch("codebase_intel.github.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(b"123456")
                with self.assertRaisesRegex(ImportFailure, "download size"):
                    download_archive("a", "b", destination, max_bytes=5)
                self.assertLessEqual(destination.stat().st_size, 5)
                for code in (403, 404, 429, 500):
                    opener.return_value.open.side_effect = HTTPError("url", code, "error", {}, None)
                    with self.subTest(code=code), self.assertRaises(ImportFailure):
                        download_archive("a", "b", destination, max_bytes=5)
                opener.return_value.open.side_effect = URLError("offline")
                with self.assertRaisesRegex(ImportFailure, "connection"):
                    download_archive("a", "b", destination, max_bytes=5)

    def test_extraction_rejects_unsafe_paths(self):
        paths = (
            "../escape.py", "/root/app.py", "root/../escape.py", "root\\escape.py",
            "root/C:escape.py", "root/CON.py", "root/trailing./x.py", "root//x.py",
            "root/null\x00.py",
        )
        for name in paths:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "archive.zip"
                # Windows ZipInfo normalizes backslashes and truncates NULs
                # when writing. Patch both ZIP headers to exercise raw input.
                safe_name = name.replace("\\", "/").replace("\x00", "_")
                payload = archive_bytes([(safe_name, b"pass")])
                archive.write_bytes(payload.replace(safe_name.encode(), name.encode()))
                with self.assertRaises(ImportFailure):
                    extract_archive(archive, root / "source")

    def test_extraction_limits_roots_and_collisions(self):
        cases = [
            ([("root/a.py", b"12345")], {"max_member_bytes": 4}),
            ([("root/a.py", b"123"), ("root/b.py", b"456")], {"max_total_bytes": 5}),
            ([("root/a.py", b""), ("root/b.py", b"")], {"max_entries": 1}),
            ([("root/a.py", b""), ("other/b.py", b"")], {}),
            ([("root/A.py", b""), ("root/a.py", b"")], {}),
            ([], {}),
        ]
        for entries, options in cases:
            with self.subTest(entries=entries, options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "archive.zip"
                archive.write_bytes(archive_bytes(entries))
                with self.assertRaises(ImportFailure):
                    extract_archive(archive, root / "source", **options)

    def test_symlinks_are_not_created(self):
        link = zipfile.ZipInfo("snapshot/link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.zip"
            archive.write_bytes(archive_bytes([(link, "../../private.py"), ("snapshot/app.py", "pass")]))
            extracted = extract_archive(archive, root / "source")
            self.assertEqual([p.name for p in extracted.iterdir()], ["app.py"])

    def test_import_pipeline_statuses_and_cleanup(self):
        destinations = []
        stages = []

        def fake_download(owner, repo, destination, *, max_bytes):
            self.assertEqual((owner, repo), ("a", "b"))
            destinations.append(destination)
            destination.write_bytes(archive_bytes([
                ("snapshot/app.py", "def hello():\n    pass\n"),
                ("snapshot/.env.py", "secret = 'hidden'"),
            ]))

        with patch("codebase_intel.github.download_archive", side_effect=fake_download):
            result = analyze_github("https://github.com/a/b.git", include_structure=True, progress=stages.append)
        self.assertEqual(stages, ["downloading", "extracting", "analyzing", "complete"])
        self.assertEqual(result["repository"]["status"], "complete")
        self.assertEqual(result["repository"]["url"], "https://github.com/a/b")
        self.assertEqual(result["files"][0]["structure"]["symbols"][0]["name"], "hello")
        self.assertEqual(result["summary"]["file_count"], 1)
        self.assertFalse(destinations[0].parent.exists())

    def test_invalid_archive_cleanup_and_validation_before_network(self):
        destinations = []

        def invalid_download(owner, repo, destination, *, max_bytes):
            destinations.append(destination)
            destination.write_bytes(b"not a zip")

        with patch("codebase_intel.github.download_archive", side_effect=invalid_download) as download:
            with self.assertRaises(ImportFailure):
                analyze_github("https://github.com/a/b", max_download_bytes=0)
            download.assert_not_called()
            with self.assertRaisesRegex(ImportFailure, "invalid or unsupported"):
                analyze_github("https://github.com/a/b")
        self.assertFalse(destinations[0].parent.exists())


if __name__ == "__main__":
    unittest.main()

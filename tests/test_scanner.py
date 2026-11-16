import tempfile
import unittest
from pathlib import Path

from codebase_intel.scanner import scan_repository


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_inventory_and_exclusions(self):
        self.write("src/app.py", b"print('hello')\n# second line")
        self.write("client.ts", b"")
        for name in ("node_modules/lib.js", ".git/hook.py", ".env.py", "secrets.json", "bundle.min.js"):
            self.write(name, b"private")
        report = scan_repository(self.root)
        self.assertEqual([f["path"] for f in report["files"]], ["client.ts", "src/app.py"])
        self.assertEqual(report["summary"]["total_lines"], 2)
        self.assertEqual(report["summary"]["languages"], {"Python": 1, "TypeScript": 1})
        self.assertEqual(report, scan_repository(self.root))
        self.assertEqual((self.root / "src/app.py").read_bytes(), b"print('hello')\n# second line")

    def test_size_binary_and_encoding_limits(self):
        self.write("big.py", b"x" * 21)
        self.write("binary.py", b"\x00abc")
        self.write("encoding.py", b"\xff")
        self.write("ok.py", b"pass\n")
        report = scan_repository(self.root, max_file_bytes=20)
        self.assertEqual([f["path"] for f in report["files"]], ["ok.py"])
        self.assertEqual(report["summary"]["skipped"], {"binary": 1, "non_utf8": 1, "too_large": 1})

    def test_count_limit(self):
        self.write("a.py", b"pass")
        self.write("b.py", b"pass")
        report = scan_repository(self.root, max_files=1)
        self.assertEqual([f["path"] for f in report["files"]], ["a.py"])
        self.assertTrue(report["summary"]["file_limit_reached"])

    def test_reject_invalid_input(self):
        with self.assertRaises(ValueError):
            scan_repository(self.root, max_files=0)
        self.write("file.py", b"pass")
        with self.assertRaises(ValueError):
            scan_repository(self.root / "file.py")
        with self.assertRaises(FileNotFoundError):
            scan_repository(self.root / "missing")

    def test_skip_external_symlinks(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            (outside / "private.py").write_text("private", encoding="utf-8")
            try:
                (self.root / "linked.py").symlink_to(outside / "private.py")
                (self.root / "linked_dir").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Symlinks unavailable: {error}")
            self.assertEqual(scan_repository(self.root)["files"], [])


if __name__ == "__main__":
    unittest.main()

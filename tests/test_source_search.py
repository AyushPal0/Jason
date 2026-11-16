import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from codebase_intel.__main__ import main
from codebase_intel.github import analyze_github
from codebase_intel.scanner import scan_repository
from codebase_intel.search import LexicalIndex
from codebase_intel.source import source_lines


class SourceSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, source):
        path = self.root / name
        path.write_bytes(source.encode("utf-8"))
        return path

    def index(self, **options):
        return LexicalIndex(scan_repository(self.root, include_structure=True, include_source=True, **options))

    def test_body_only_query_prefers_smallest_matching_definition(self):
        self.write("service.py", (
            "class Store:\n"
            "    def save(self):\n"
            "        return database.persist(payload)\n"
            "    def clear(self):\n"
            "        pass\n"
        ))
        result = self.index().search("persist payload")["results"][0]
        self.assertEqual(result["name"], "Store.save")
        self.assertEqual(result["kind"], "function")
        self.assertEqual((result["start_line"], result["end_line"]), (2, 3))
        self.assertEqual(result["snippet"]["matched_tokens"], ["payload", "persist"])
        self.assertEqual(result["snippet"]["lines"][-1]["text"], "        return database.persist(payload)")

    def test_comments_docstrings_other_languages_and_broken_python(self):
        self.write("worker.py", 'def work():\n    """Retry transient errors."""\n    # exponential backoff\n    pass\n')
        self.write("client.ts", "export const mode = 'offlinequeue';\n")
        self.write("broken.py", "def broken(:\n    # recoverymarker\n")
        index = self.index()
        for query, path in (("exponential backoff", "worker.py"), ("transient errors", "worker.py"),
                            ("offlinequeue", "client.ts"), ("recoverymarker", "broken.py")):
            with self.subTest(query=query):
                result = index.search(query)["results"][0]
                self.assertEqual(result["path"], path)
                self.assertTrue(result["snippet"]["matched_tokens"])
        self.assertEqual(index.search("recoverymarker")["coverage"]["parse_errors"][0]["path"], "broken.py")

    def test_snippet_finds_late_match_and_preserves_line_numbers(self):
        source = "def process():\n" + "    value = 1\n" * 30 + "    return uniquemarker\n"
        self.write("worker.py", source)
        result = self.index().search("uniquemarker", limit=1)["results"][0]
        snippet = result["snippet"]
        self.assertLessEqual(len(snippet["lines"]), 12)
        self.assertTrue(snippet["truncated"])
        self.assertEqual(snippet["end_line"], 32)
        for line in snippet["lines"]:
            self.assertEqual(line["text"], source_lines(source)[line["line_number"] - 1])
        self.assertEqual(snippet["matched_tokens"], ["uniquemarker"])

    def test_clipping_is_explicit_and_visible_matches_are_truthful(self):
        source = "def process():\n    value = '" + "x" * 300 + " hiddenmarker'\n"
        self.write("long.py", source)
        result = self.index().search("hiddenmarker", limit=1)["results"][0]
        snippet = result["snippet"]
        self.assertEqual(result["matched_tokens"], ["hiddenmarker"])
        self.assertEqual(snippet["matched_tokens"], [])
        self.assertTrue(snippet["truncated"])
        self.assertTrue(any(line["truncated"] for line in snippet["lines"]))
        self.assertTrue(all(len(line["text"]) <= 160 for line in snippet["lines"]))
        self.assertLessEqual(sum(len(line["text"]) for line in snippet["lines"]), 2_000)

    def test_distributed_query_terms_need_not_fit_one_snippet(self):
        self.write("spread.py", "# firstmarker\n" + "value = 1\n" * 30 + "# lastmarker\n")
        result = self.index().search("firstmarker lastmarker")["results"][0]
        self.assertEqual(result["matched_tokens"], ["firstmarker", "lastmarker"])
        self.assertEqual(result["snippet"]["matched_tokens"], ["firstmarker"])

    def test_snapshot_and_hash_survive_local_file_changes_without_reopening(self):
        original = "def save():\n    return snapshotmarker\n"
        path = self.write("snapshot.py", original)
        report = scan_repository(self.root, include_structure=True, include_source=True)
        path.write_text("changed = True\n", encoding="utf-8")
        with patch("pathlib.Path.open", side_effect=AssertionError("Search must not reopen files")):
            result = LexicalIndex(report).search("snapshotmarker")["results"][0]
        self.assertEqual(result["source_sha256"], hashlib.sha256(original.encode()).hexdigest())
        self.assertIn("snapshotmarker", result["snippet"]["lines"][-1]["text"])

    def test_capture_budget_falls_back_to_metadata_and_default_scan_omits_source(self):
        self.write("large.py", "def omitted():\n    return bodymarker\n")
        self.write("small.py", "x=1\n")
        plain = scan_repository(self.root)
        self.assertNotIn("source_capture", plain["summary"])
        self.assertTrue(all("source" not in file for file in plain["files"]))
        report = scan_repository(self.root, include_structure=True, include_source=True, max_source_bytes=5)
        self.assertEqual(report["summary"]["source_capture"]["captured_bytes"], 4)
        self.assertEqual(report["summary"]["source_capture"]["files_without_source"], 1)
        index = LexicalIndex(report)
        result = index.search("omitted")["results"][0]
        self.assertIsNone(result["snippet"])
        self.assertIsNone(result["source_sha256"])
        self.assertEqual(index.search("bodymarker")["results"], [])
        self.assertEqual(index.coverage["files_with_source"], 1)
        self.assertEqual(index.coverage["files_without_source"], 1)
        with self.assertRaises(ValueError):
            scan_repository(self.root, include_source=True, max_source_bytes=0)

    def test_physical_lines_match_parser_for_crlf_bom_and_unicode_separators(self):
        source = "\ufefflabel = 'a\u2028b'\r\n\r\ndef save():\r\n    return linemarker\r\n"
        path = self.write("lines.py", source)
        report = scan_repository(self.root, include_structure=True, include_source=True)
        self.assertEqual(report["files"][0]["line_count"], 4)
        result = LexicalIndex(report).search("linemarker")["results"][0]
        self.assertEqual((result["start_line"], result["end_line"]), (3, 4))
        self.assertEqual(result["snippet"]["lines"][-1]["line_number"], 4)
        self.assertEqual(result["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(source_lines("a\rb\r"), ["a", "b"])
        self.assertEqual(source_lines(""), [])

    def test_exclusions_and_per_file_limit_apply_before_source_capture(self):
        self.write("good.py", "# allowedmarker\n")
        self.write(".env.py", "# secretmarker\n")
        self.write("big.py", "# oversizedmarker " + "x" * 100)
        (self.root / "binary.py").write_bytes(b"binarymarker\x00")
        index = self.index(max_file_bytes=30)
        self.assertEqual(index.coverage["files_with_source"], 1)
        for query in ("secretmarker", "oversizedmarker", "binarymarker"):
            self.assertEqual(index.search(query)["results"], [])

    def test_github_evidence_survives_temporary_download_cleanup(self):
        destinations = []

        def fake_download(owner, repo, destination, *, max_bytes):
            destinations.append(destination)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("snapshot/app.py", "def work():\n    return remotemarker\n")

        with patch("codebase_intel.github.download_archive", side_effect=fake_download):
            report = analyze_github("https://github.com/a/b", include_structure=True, include_source=True)
        self.assertFalse(destinations[0].parent.exists())
        result = LexicalIndex(report).search("remotemarker")["results"][0]
        self.assertEqual(result["name"], "work")
        self.assertEqual(result["snippet"]["matched_tokens"], ["remotemarker"])

    def test_cli_body_search_emits_snippet_and_limits_capture(self):
        self.write("worker.py", "def run():\n    return clibodymarker\n")
        output = io.StringIO()
        with patch("sys.argv", ["jason", str(self.root), "--search", "clibodymarker", "--limit", "1"]):
            with contextlib.redirect_stdout(output):
                main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["results"][0]["snippet"]["matched_tokens"], ["clibodymarker"])
        self.assertNotIn("source", result["results"][0])


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codebase_intel.__main__ import main
from codebase_intel.scanner import scan_repository
from codebase_intel.search import LexicalIndex, tokenize


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = {
            "auth.py": (
                "from .tokens import issue_token as mint\n"
                "import json as encoder\n\n"
                "class AuthService:\n"
                "    async def login_user(self):\n"
                "        def validate_token():\n"
                "            return True\n"
                "        return validate_token()\n"
                "    def logout_user(self):\n"
                "        pass\n"
            ),
            "network.py": "class HTTPClient:\n    pass\n\ndef loadUserProfile():\n    pass\n",
            "profile.ts": "export const name = 'profile';\n",
            "broken.py": "def broken(:",
            ".env.py": "def hidden_secret(): pass",
        }
        for name, source in self.sources.items():
            (self.root / name).write_text(source, encoding="utf-8")
        self.report = scan_repository(self.root, include_structure=True)
        self.index = LexicalIndex(self.report)

    def test_identifier_tokenization(self):
        self.assertEqual(tokenize("src/HTTPClient.py login_user loadUserProfile"), {
            "src", "http", "client", "httpclient", "py", "login", "user", "load", "profile", "loaduserprofile",
        })
        self.assertEqual(tokenize("café_42"), {"café", "42"})

    def test_top_result_evaluation(self):
        # A small fixed relevance set provides a baseline for future ranking changes.
        cases = [
            ("login", "auth.py", "AuthService.login_user", "function"),
            ("auth service", "auth.py", "AuthService", "class"),
            ("validate token", "auth.py", "AuthService.login_user.validate_token", "function"),
            ("mint", "auth.py", "from .tokens import issue_token as mint", "import"),
            ("http client", "network.py", "HTTPClient", "class"),
            ("httpclient", "network.py", "HTTPClient", "class"),
            ("load user profile", "network.py", "loadUserProfile", "function"),
            ("profile ts", "profile.ts", "profile.ts", "file"),
        ]
        for query, path, name, kind in cases:
            with self.subTest(query=query):
                top = self.index.search(query)["results"][0]
                self.assertEqual((top["path"], top["name"], top["kind"]), (path, name, kind))

    def test_source_locations_aliases_and_scope(self):
        function = self.index.search("login_user")["results"][0]
        self.assertEqual((function["start_line"], function["definition_line"], function["end_line"]), (5, 5, 8))
        imported = self.index.search("encoder")["results"][0]
        self.assertEqual((imported["start_line"], imported["end_line"]), (2, 2))
        nested = self.index.search("validate token")["results"][0]
        self.assertEqual((nested["start_line"], nested["end_line"]), (6, 7))

    def test_all_terms_required_no_match_and_exclusions(self):
        for query in ("login nonexistent", "hidden secret", "unfindable"):
            result = self.index.search(query)
            self.assertEqual(result["results"], [])
            self.assertEqual(result["total_matches"], 0)
        # Words can match across fields, e.g. a function name and its file path.
        self.assertEqual(self.index.search("login auth")["results"][0]["name"], "AuthService.login_user")

    def test_limits_repeated_terms_case_and_stable_order(self):
        full = self.index.search("user")
        limited = self.index.search("user", limit=1)
        self.assertGreater(full["total_matches"], 1)
        self.assertEqual(limited["total_matches"], full["total_matches"])
        self.assertEqual(limited["results"], full["results"][:1])
        self.assertEqual(self.index.search("USER")["results"], full["results"])
        self.assertEqual(self.index.search("user user")["results"], full["results"])
        self.assertEqual(self.index.search("login_user")["results"][0]["score"],
                         self.index.search("login user")["results"][0]["score"] + 10)
        reversed_report = {**self.report, "files": list(reversed(self.report["files"]))}
        self.assertEqual(LexicalIndex(reversed_report).search("py"), self.index.search("py"))

    def test_invalid_queries_and_limits(self):
        for query in ("", "   ", "..._-", "a" * 501):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.index.search(query)
        for limit in (0, -1, 101):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.index.search("user", limit=limit)

    def test_coverage_and_empty_file_locations(self):
        coverage = self.index.search("login")["coverage"]
        self.assertEqual(coverage["parsed_python_files"], 2)
        self.assertEqual(coverage["parse_errors"][0]["path"], "broken.py")
        self.assertEqual(self.index.search("broken py")["results"][0]["kind"], "file")
        (self.root / "empty.py").write_text("", encoding="utf-8")
        plain = scan_repository(self.root)
        result = LexicalIndex(plain).search("empty py")
        self.assertIsNone(result["results"][0]["start_line"])
        self.assertIsNone(result["results"][0]["end_line"])
        self.assertEqual(result["coverage"]["python_files_without_structure"], 4)
        limited = LexicalIndex(scan_repository(self.root, max_files=1)).search("auth")
        self.assertTrue(limited["coverage"]["file_limit_reached"])
        empty = LexicalIndex({"files": [], "summary": {"file_limit_reached": False}})
        self.assertEqual(empty.search("anything")["results"], [])

    def test_cli_local_search_automatically_parses_structure(self):
        output = io.StringIO()
        with patch("sys.argv", ["jason", str(self.root), "--search", "login", "--limit", "1"]):
            with contextlib.redirect_stdout(output):
                main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["results"][0]["name"], "AuthService.login_user")
        self.assertNotIn("files", result)

    def test_cli_github_search_preserves_metadata(self):
        report = {**self.report, "repository": {"url": "https://github.com/a/b", "status": "complete"}}
        output = io.StringIO()
        with patch("sys.argv", ["jason", "--github", "https://github.com/a/b", "--search", "mint"]):
            with patch("codebase_intel.__main__.analyze_github", return_value=report) as analyze:
                with contextlib.redirect_stdout(output):
                    main()
        self.assertTrue(analyze.call_args.kwargs["include_structure"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["repository"], report["repository"])
        self.assertEqual(result["results"][0]["kind"], "import")

    def test_cli_rejects_invalid_query_before_network(self):
        for flags in (["--search", "..."], ["--search", "login", "--limit", "0"]):
            with patch("sys.argv", ["jason", "--github", "https://github.com/a/b", *flags]):
                with patch("codebase_intel.__main__.analyze_github") as analyze:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        main()
                self.assertEqual(error.exception.code, 2)
                analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()

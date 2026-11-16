import tempfile
import unittest
from pathlib import Path

from codebase_intel.parser import parse_python
from codebase_intel.scanner import scan_repository


class ParserTests(unittest.TestCase):
    def test_nested_decorated_async_definitions_and_ranges(self):
        source = (
            "@decorate\n"
            "class Service:\n"
            "    @trace\n"
            "    async def run(self):\n"
            "        def helper():\n"
            "            return 1\n"
            "        return helper()\n"
            "\n"
            "def standalone():\n"
            "    pass\n"
        )
        result = parse_python(source)
        symbols = result["symbols"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual([s["qualified_name"] for s in symbols], [
            "Service", "Service.run", "Service.run.helper", "standalone",
        ])
        self.assertEqual([s["kind"] for s in symbols], ["class", "function", "function", "function"])
        self.assertEqual([(s["start_line"], s["definition_line"], s["end_line"]) for s in symbols], [
            (1, 2, 7), (3, 4, 7), (5, 5, 6), (9, 9, 10),
        ])
        self.assertTrue(symbols[1]["is_async"])
        self.assertFalse(symbols[2]["is_async"])

    def test_import_aliases_relative_imports_and_scopes(self):
        result = parse_python(
            "import os, pathlib as paths\n"
            "from ..tools import (\n"
            "    build as make,\n"
            "    check,\n"
            ")\n"
            "def run():\n"
            "    from . import local\n"
            "if False:\n"
            "    from pkg import *\n"
        )
        imports = result["imports"]
        self.assertEqual(imports[0]["names"], [
            {"name": "os", "alias": None}, {"name": "pathlib", "alias": "paths"},
        ])
        self.assertEqual(imports[0]["kind"], "import")
        self.assertEqual(imports[1]["module"], "tools")
        self.assertEqual(imports[1]["level"], 2)
        self.assertEqual((imports[1]["start_line"], imports[1]["end_line"]), (2, 5))
        self.assertEqual(imports[2]["scope"], "run")
        self.assertIsNone(imports[2]["module"])
        self.assertEqual(imports[2]["level"], 1)
        self.assertIsNone(imports[3]["scope"])
        self.assertEqual(imports[3]["names"], [{"name": "*", "alias": None}])

    def test_invalid_syntax_reports_location(self):
        result = parse_python("def broken(:\n    pass\n", filename="bad.py")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["kind"], "syntax_error")
        self.assertEqual(result["error"]["line"], 1)
        self.assertEqual(result["symbols"], [])

    def test_source_is_never_executed(self):
        result = parse_python("raise RuntimeError('must not execute')\nimport nonexistent_package\n")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["imports"]), 1)
        self.assertEqual(parse_python("")["symbols"], [])

    def test_scan_integration_preserves_inventory_and_continues_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in {
                "bad.py": "def broken(:", "good.py": "def hello():\n    pass\n",
                "client.ts": "export const x = 1;", ".env.py": "secret = 1",
            }.items():
                (root / name).write_text(source, encoding="utf-8")
            plain = scan_repository(root)
            report = scan_repository(root, include_structure=True)
            self.assertEqual(report["summary"], plain["summary"])
            files = {f["path"]: f for f in report["files"]}
            self.assertEqual(files["bad.py"]["structure"]["status"], "error")
            self.assertEqual(files["good.py"]["structure"]["symbols"][0]["name"], "hello")
            self.assertNotIn("structure", files["client.ts"])
            self.assertNotIn(".env.py", files)
            self.assertTrue(all("structure" not in f for f in plain["files"]))


if __name__ == "__main__":
    unittest.main()

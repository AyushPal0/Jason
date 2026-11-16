"""Inventory a local folder or import a public GitHub repository."""

import argparse
import json
import sys

from .github import analyze_github
from .scanner import scan_repository
from .search import LexicalIndex, tokenize


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze source files in a local folder or public GitHub repository")
    parser.add_argument("path", nargs="?", help="Repository folder to scan")
    parser.add_argument("--github", metavar="URL", help="Import a public GitHub repository")
    parser.add_argument("--max-download-bytes", type=int, default=25_000_000)
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--structure", action="store_true", help="Extract Python definitions and imports")
    parser.add_argument("--search", metavar="QUERY", help="Search names, imports, and file paths")
    parser.add_argument("--limit", type=int, default=10, help="Maximum search results (1-100; default 10)")
    args = parser.parse_args()
    if bool(args.path) == bool(args.github):
        parser.error("Provide either a local path or --github URL")
    if not 1 <= args.limit <= 100:
        parser.error("Search limit must be between 1 and 100")
    if args.search is not None and (len(args.search) > 500 or not tokenize(args.search)):
        parser.error("Search query must contain a letter or number and be at most 500 characters")
    try:
        options = dict(max_file_bytes=args.max_file_bytes, max_files=args.max_files,
                       include_structure=args.structure or args.search is not None)
        if args.github:
            report = analyze_github(
                args.github, **options, max_download_bytes=args.max_download_bytes,
                progress=lambda stage: print(f"Import: {stage}", file=sys.stderr),
            )
        else:
            report = scan_repository(args.path, **options)
        if args.search is not None:
            output = LexicalIndex(report).search(args.search, limit=args.limit)
            if "repository" in report:
                output["repository"] = report["repository"]
            report = output
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

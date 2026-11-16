"""Inventory a local folder or import a public GitHub repository."""

import argparse
import json
import sys

from .github import analyze_github
from .scanner import scan_repository


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze source files in a local folder or public GitHub repository")
    parser.add_argument("path", nargs="?", help="Repository folder to scan")
    parser.add_argument("--github", metavar="URL", help="Import a public GitHub repository")
    parser.add_argument("--max-download-bytes", type=int, default=25_000_000)
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--structure", action="store_true", help="Extract Python definitions and imports")
    args = parser.parse_args()
    if bool(args.path) == bool(args.github):
        parser.error("Provide either a local path or --github URL")
    try:
        options = dict(max_file_bytes=args.max_file_bytes, max_files=args.max_files,
                       include_structure=args.structure)
        if args.github:
            report = analyze_github(
                args.github, **options, max_download_bytes=args.max_download_bytes,
                progress=lambda stage: print(f"Import: {stage}", file=sys.stderr),
            )
        else:
            report = scan_repository(args.path, **options)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

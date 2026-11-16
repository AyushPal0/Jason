"""Run with: python -m codebase_intel PATH."""

import argparse
import json

from .scanner import scan_repository


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory source files in a local repository")
    parser.add_argument("path", help="Repository folder to scan")
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--structure", action="store_true", help="Extract Python definitions and imports")
    args = parser.parse_args()
    try:
        report = scan_repository(
            args.path, max_file_bytes=args.max_file_bytes, max_files=args.max_files,
            include_structure=args.structure,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

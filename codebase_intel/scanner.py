"""Read source files without importing or executing repository code."""

import hashlib
import os
from collections import Counter
from pathlib import Path

from .parser import parse_python
from .source import source_lines


LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
    ".go": "Go", ".rs": "Rust", ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
    ".rb": "Ruby", ".php": "PHP", ".sh": "Shell",
    ".html": "HTML", ".css": "CSS", ".sql": "SQL",
}
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".next",
    "build", "dist", "coverage", "vendor", "secrets", ".aws", ".ssh",
}
SECRET_NAMES = {"credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}


def scan_repository(
    root: str | Path, *, max_file_bytes: int = 1_000_000, max_files: int = 10_000,
    include_structure: bool = False,
    include_source: bool = False, max_source_bytes: int = 20_000_000,
) -> dict:
    """Return a deterministic inventory of supported UTF-8 source files.

    Symlinks and Windows junctions are skipped. Size and count limits bound
    source content reads. This is an initial static-folder scanner, not a
    security boundary for folders being modified concurrently.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository path must be a directory")
    if max_file_bytes < 1 or max_files < 1 or max_source_bytes < 1:
        raise ValueError("File size, count, and source capture limits must be positive")

    files = []
    skipped = Counter()
    limit_reached = False
    captured_bytes = 0
    files_without_source = 0

    def is_link(path: Path) -> bool:
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)()

    def walk_error(error: OSError) -> None:
        skipped["unreadable_directory"] += 1

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        kept_dirs = []
        for name in sorted(dirs):
            if name.lower() in EXCLUDED_DIRS or is_link(parent / name):
                skipped["excluded_directory"] += 1
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs

        for name in sorted(names):
            path = parent / name
            lower_name = name.lower()
            if lower_name.startswith(".env") or lower_name in SECRET_NAMES:
                skipped["secret_filename"] += 1
                continue
            if is_link(path):
                skipped["symlink"] += 1
                continue
            language = LANGUAGES.get(path.suffix.lower())
            if language is None or lower_name.endswith((".min.js", ".min.css", ".generated.ts")):
                skipped["unsupported_or_generated"] += 1
                continue
            if len(files) >= max_files:
                limit_reached = True
                break
            try:
                if not path.is_file():
                    skipped["non_regular_file"] += 1
                    continue
                with path.open("rb") as source:
                    content = source.read(max_file_bytes + 1)
                if len(content) > max_file_bytes:
                    skipped["too_large"] += 1
                    continue
                if b"\x00" in content:
                    skipped["binary"] += 1
                    continue
                decoded = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                skipped["non_utf8"] += 1
                continue
            except OSError:
                skipped["unreadable_file"] += 1
                continue
            record = {
                "path": path.relative_to(root).as_posix(),
                "language": language,
                "size_bytes": len(content),
                "line_count": len(source_lines(decoded)),
            }
            if include_structure and language == "Python":
                record["structure"] = parse_python(decoded, filename=record["path"])
            if include_source:
                if captured_bytes + len(content) <= max_source_bytes:
                    record["source"] = decoded
                    record["source_sha256"] = hashlib.sha256(content).hexdigest()
                    captured_bytes += len(content)
                else:
                    files_without_source += 1
            files.append(record)
        if limit_reached:
            break

    files.sort(key=lambda item: item["path"])
    report = {
        "files": files,
        "summary": {
            "file_count": len(files),
            "total_bytes": sum(item["size_bytes"] for item in files),
            "total_lines": sum(item["line_count"] for item in files),
            "languages": dict(sorted(Counter(item["language"] for item in files).items())),
            "skipped": dict(sorted(skipped.items())),
            "file_limit_reached": limit_reached,
        },
    }
    if include_source:
        report["summary"]["source_capture"] = {
            "max_bytes": max_source_bytes, "captured_bytes": captured_bytes,
            "files_without_source": files_without_source,
        }
    return report

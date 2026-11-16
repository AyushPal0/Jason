"""Download public GitHub source snapshots and analyze them without execution."""

import re
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .scanner import scan_repository


class ImportFailure(ValueError):
    """A repository could not be downloaded or safely unpacked."""


def parse_github_url(url: str) -> tuple[str, str]:
    """Accept only HTTPS github.com/owner/repo URLs, optionally ending in .git."""
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})/?",
        url,
    )
    if not match:
        raise ImportFailure("Use a repository URL such as https://github.com/owner/repo")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if not repo or repo in {".", ".."}:
        raise ImportFailure("Repository name is invalid")
    return owner, repo


class GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or target.netloc not in {"api.github.com", "codeload.github.com"}):
            raise ImportFailure("GitHub returned an unexpected download destination")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(owner: str, repo: str, destination: Path, *, max_bytes: int) -> None:
    """Stream an unauthenticated default-branch snapshot with bounded storage."""
    request = Request(
        f"https://api.github.com/repos/{owner}/{repo}/zipball",
        headers={"User-Agent": "Jason-Codebase-Intelligence", "Accept": "application/vnd.github+json"},
    )
    deadline = time.monotonic() + 60
    try:
        with build_opener(GitHubRedirects()).open(request, timeout=15) as response:
            size = 0
            with destination.open("xb") as output:
                while True:
                    if time.monotonic() > deadline:
                        raise ImportFailure("Download exceeded the 60-second time budget")
                    chunk = response.read(min(64 * 1024, max_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ImportFailure("Repository archive exceeds the download size limit")
                    output.write(chunk)
    except HTTPError as error:
        messages = {
            404: "Repository is missing, private, or empty",
            403: "GitHub denied the request; its anonymous API rate limit may be exhausted",
            429: "GitHub rate limit reached; try again later",
        }
        raise ImportFailure(messages.get(error.code, f"GitHub returned HTTP {error.code}")) from error
    except (URLError, TimeoutError) as error:
        raise ImportFailure("Cannot download repository; check your connection and try again") from error


def extract_archive(
    archive: Path, destination: Path, *, max_entries: int = 20_000,
    max_total_bytes: int = 100_000_000, max_member_bytes: int = 10_000_000,
) -> Path:
    """Unpack regular files beneath one archive root into a fresh private folder.

    Do not use extractall: validate paths explicitly for both POSIX and Windows.
    Symlinks and other special entries are skipped, never materialized.
    """
    destination.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if not members or len(members) > max_entries:
            raise ImportFailure("Archive is empty or exceeds the entry count limit")
        roots = set()
        seen = set()
        total = 0
        for member in members:
            original = member.orig_filename
            name = original.rstrip("/")
            parts = name.split("/")
            if (not name or original.startswith("/") or "\x00" in original
                    or any(part in {"", ".", ".."} for part in parts)
                    or any(re.search(r'[\\:*?"<>|\x00-\x1f]', part) or part.endswith((".", " ")) for part in parts)
                    or any(part.split(".")[0].upper() in {
                        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10)),
                    } for part in parts)):
                raise ImportFailure("Archive contains an unsafe or nonportable path")
            roots.add(parts[0])
            if len(roots) != 1 or (len(parts) == 1 and not member.is_dir()):
                raise ImportFailure("Archive must contain one repository root folder")
            key = name.casefold()
            if key in seen:
                raise ImportFailure("Archive contains duplicate or case-colliding paths")
            seen.add(key)
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                continue
            if member.is_dir():
                continue
            if member.file_size > max_member_bytes:
                raise ImportFailure("Archive contains a file exceeding the extraction size limit")
            total += member.file_size
            if total > max_total_bytes:
                raise ImportFailure("Archive exceeds the total extraction size limit")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("xb") as output:
                written = 0
                while chunk := source.read(min(64 * 1024, max_member_bytes - written + 1)):
                    written += len(chunk)
                    if written > max_member_bytes or written > member.file_size:
                        raise ImportFailure("Archive file exceeded its declared size")
                    output.write(chunk)
        root = destination / next(iter(roots))
        root.mkdir(exist_ok=True)
        return root


def analyze_github(
    url: str, *, include_structure: bool = False, max_file_bytes: int = 1_000_000,
    max_files: int = 10_000, max_download_bytes: int = 25_000_000, progress=None,
    include_source: bool = False, max_source_bytes: int = 20_000_000,
) -> dict:
    """Import, scan, and automatically remove the temporary source snapshot."""
    owner, repo = parse_github_url(url)
    if min(max_file_bytes, max_files, max_download_bytes, max_source_bytes) < 1:
        raise ImportFailure("File size, file count, download, and source capture limits must be positive")

    def notify(stage: str) -> None:
        if progress is not None:
            progress(stage)

    try:
        with tempfile.TemporaryDirectory(prefix="jason-import-") as directory:
            temporary = Path(directory)
            archive = temporary / "source.zip"
            notify("downloading")
            download_archive(owner, repo, archive, max_bytes=max_download_bytes)
            notify("extracting")
            root = extract_archive(archive, temporary / "source")
            notify("analyzing")
            report = scan_repository(
                root, include_structure=include_structure,
                max_file_bytes=max_file_bytes, max_files=max_files,
                include_source=include_source, max_source_bytes=max_source_bytes,
            )
            report["repository"] = {
                "url": f"https://github.com/{owner}/{repo}",
                "ref": "default_branch", "archive_root": root.name, "status": "complete",
            }
        notify("complete")
        return report
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        raise ImportFailure("GitHub returned an invalid or unsupported repository archive") from error

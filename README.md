# Codebase Intelligence Platform

A developer tool for understanding repositories and investigating bugs with evidence from source code.

We are building this incrementally. See [PROJECT.md](PROJECT.md) for the architecture and milestones.

## Requirements

Python 3.11 or newer. The initial scanner uses only the Python standard library.

## Development workflow

Each completed task gets a descriptive Git commit and a push to the linked GitHub repository after its checks pass. Never commit credentials, local environments, or generated output.

## Scan a local repository

From this project's directory:

```powershell
python -m codebase_intel .
python -m codebase_intel "D:\path\to\another\repository" --max-files 500
```

The JSON output lists source files with language, byte size, and line count, plus totals and skip counts. The scanner never imports or executes the scanned code. It skips symlinks, Windows junctions, common secret filenames, dependency directories, selected generated filenames, binary files, and files that are not UTF-8. The default limits are 1 MB per source file and 10,000 included files. A flag reports when another candidate is encountered after the file limit.

Exclusions are currently built in; `.gitignore` rules are not yet interpreted. Filename exclusions cannot detect secrets embedded in ordinary source files. This scanner is intended for static local folders; it does not protect against concurrent filesystem changes or bound the total number of directory entries visited.

## Inspect Python code structure

```powershell
python -m codebase_intel . --structure
```

Each Python file gains a `structure` object containing `symbols` and `imports`.
For example, a class named `Service` with an async method `run` produces
symbols named `Service` and `Service.run`. Each symbol records its kind,
whether it is async, and its source range. `start_line` includes decorators;
`definition_line` points to the `class` or `def` statement. All line numbers
start at 1 and end lines are inclusive.

Imports preserve module names, aliases, relative import levels, source ranges,
and the enclosing class/function scope. Conditional imports are included even
if their branch would not execute. They are not yet resolved into dependencies.

The parser uses Python's built-in `ast` module: it turns source text into a
tree of syntax nodes, then visits definition and import nodes. No imported
packages are loaded and no repository code is executed. It parses the same
bounded text read by the scanner, avoiding a second file read.

Files with syntax errors report `structure.status: "error"` with an error
message and line number; other files continue to be analyzed. The supported
Python grammar follows the interpreter you run. Other languages remain in
the inventory but do not receive structure information yet. This is not a
call graph or a sandbox for hostile inputs. Tree-sitter remains a future
option for extending parsing across languages.

## Import a public GitHub repository

```powershell
python -m codebase_intel --github https://github.com/AyushPal0/Jason --structure
```

Use a repository's HTTPS URL, optionally ending in `.git`. Branch/file URLs,
SSH URLs, other hosts, query strings, and embedded credentials are rejected.
Supply either a local folder or `--github`, not both.

The importer requests a ZIP snapshot of the default branch using GitHub's
[repository archive API](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip).
It makes unauthenticated requests for public repositories; it does not use
your Git credentials or run Git hooks, install dependencies, or execute code.
Private repositories, empty repositories, and missing repositories are not
supported. Anonymous GitHub API rate limits apply.

Progress (`downloading`, `extracting`, `analyzing`, `complete`) is printed to
stderr. The final report goes to stdout and includes repository metadata.
Errors exit nonzero with a message on stderr. Status is synchronous and is
not persisted as a background job. The snapshot and extracted files are
removed after success or failure.

Default import limits:

- 25 MB downloaded (`--max-download-bytes` can change this).
- 20,000 archive entries, 100 MB total extracted data, and 10 MB per extracted file.
- 15-second network operation timeout and a 60-second download budget checked between reads.
- Existing scanner limits still apply: 1 MB per analyzed file and 10,000 included files.

The extractor rejects traversal paths, nonportable Windows filenames,
multiple archive roots, and case-colliding paths. Symlinks and special files
are skipped. Extraction uses a fresh temporary directory and never restores
archive permissions. Large repositories can exceed these limits. Submodule
contents and Git history are not fetched. This CLI is not a sandbox or a
hard CPU/memory limit; stricter process isolation is needed before exposing
imports as a public service. The report identifies the archive root but
does not yet resolve or verify a full commit SHA.

## Search code and repository metadata

```powershell
python -m codebase_intel . --search "scan_repository" --limit 5
python -m codebase_intel . --search "github url"
python -m codebase_intel . --search "monotonic deadline" --limit 1
python -m codebase_intel --github https://github.com/AyushPal0/Jason --search "parse python"
```

Search automatically enables Python parsing and source capture. It returns ranked results rather
than the complete inventory. Each result includes a path, kind, name, source
line range, matched tokens, a score, a source hash, and a bounded code snippet.
Empty files have null line ranges and snippets.
Repository metadata is preserved for GitHub imports. Source ranges refer to
the scanned snapshot; local edits or later GitHub commits can move the lines.

The implementation builds an in-memory **inverted index**: each token points
to the file, definition, and import records containing it. For example, `login`
points directly to records containing that token instead of requiring a scan
of every record for every query. Identifier splitting lets `login user` match
`login_user`, and `http client` match `HTTPClient`. Whole words are retained so
`httpclient` also matches `HTTPClient`.

Every query token must match within the same record, possibly across its name,
enclosing scope, file path, and source text. Symbol records include their full
source range (including nested definitions), while file records cover the
whole file. Ranking adds these weights per matching token:

| Field | Weight |
| --- | ---: |
| Symbol name or import label | 4 |
| Qualified symbol name or enclosing import scope | 2 |
| File path | 1 |
| Source text in the record's range | 1 |

An exact name match adds 10 points. Fields can contribute together; repeated
query words do not increase scores. Ties prefer shorter source ranges, then
functions/imports, classes, and files, followed by path, source line, kind,
and name for stable ordering. This helps return the smallest relevant piece
of code before an enclosing class or file. Scores are ranking heuristics,
not confidence estimates.
The default is 10 results; `--limit` accepts 1–100. Queries must contain a
letter or number and be no longer than 500 characters. No matches returns an
empty results list successfully.

`coverage` reports parser errors, Python files without structure, files with
and without captured source, and whether the scanner's file limit was reached.
All included languages support file-level text search. Python additionally
supports definition and import records. Invalid Python still supports text
search through its file record. Bodies, comments, and docstrings are searched
as literal text; their presence does not prove a behavior executes at runtime.
Semantic meaning, fuzzy matching, and natural-language question answering are
not implemented yet. Each command scans again and rebuilds the index; GitHub
searches download a fresh snapshot. No database or AI API is needed.

### Source snippets and capture limits

The scanner optionally retains the same decoded source it already reads.
Ordinary inventory and `--structure` commands do not retain or print full
source. Search enables capture automatically, with a default 20 MB aggregate
file-byte budget. Set `--max-source-bytes` to change it. Per-file size limits,
filename exclusions, and binary/encoding checks still apply before capture.
Files that do not fit the remaining capture budget stay searchable through
their metadata; their body text is absent and snippets/hashes are null. Later
smaller files may still fit. The coverage counts expose this partial search.
This byte budget limits captured file bytes, not total index memory usage.

Each `snippet` has its own inclusive source range and numbered `lines`.
The selected contiguous window maximizes distinct query words visible in up
to 12 lines. Text is clipped to 160 characters per line and at most 2,000
characters overall, excluding JSON metadata. Per-line and overall `truncated`
flags indicate clipping or omitted source lines. `snippet.matched_tokens`
lists only words visible in the displayed text. A record's `matched_tokens`
can be broader: terms may occur far apart, in metadata, or beyond a clipped
line. An excerpt is therefore not guaranteed to display every query word.

Snippets are built from captured text without reopening files. They remain
consistent with the scanned snapshot after local edits or temporary GitHub
files are removed. `source_sha256` identifies the original raw file bytes,
including a UTF-8 BOM or CRLF endings if present; displayed text omits the BOM
and splits physical CR/LF lines. The hash is not a Git commit identifier and
does not independently verify the repository or a snippet's meaning.

The search tests include eight fixed relevance queries against a small
synthetic repository, checking the expected first result. This is a regression
baseline for future ranking work, not a production retrieval benchmark.
Additional tests cover body-only matches, comments/docstrings, clipping,
source budget gaps, physical line numbers, exclusions, and snapshot evidence
after local edits or GitHub temporary-directory cleanup.

## Run checks

```powershell
python -m unittest discover -s tests -v
```

## How the first feature works

1. `__main__.py` reads command-line arguments.
2. `scanner.py` traverses the selected folder and filters out excluded entries.
3. It reads each supported source file within the byte limit and computes metadata.
4. The CLI prints the resulting inventory as JSON. Later, our parser and API will reuse `scan_repository`.

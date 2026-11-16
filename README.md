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

## Run checks

```powershell
python -m unittest discover -s tests -v
```

## How the first feature works

1. `__main__.py` reads command-line arguments.
2. `scanner.py` traverses the selected folder and filters out excluded entries.
3. It reads each supported source file within the byte limit and computes metadata.
4. The CLI prints the resulting inventory as JSON. Later, our parser and API will reuse `scan_repository`.

"""Deterministic lexical search with bounded evidence from captured source."""

import re
from collections import Counter, defaultdict

from .source import source_lines


def make_snippet(lines: list[str], start: int | None, end: int | None, tokens: set[str]) -> dict | None:
    """Choose a contiguous 12-line window with the most distinct query terms.

    Text is clipped to 160 characters per line and 2,000 overall. Explicit
    clipping flags distinguish excerpts from complete source lines.
    """
    if start is None or end is None or not lines:
        return None
    first, last = start - 1, min(end, len(lines))
    width = min(12, last - first)
    if width <= 0:
        return None
    # Score only text that can actually be shown, not the hidden suffix of a long line.
    line_terms = [tokenize(line[:160]) & tokens for line in lines[first:last]]
    counts = Counter(term for terms in line_terms[:width] for term in terms)
    best_offset, best_score = 0, len(counts)
    for offset in range(1, len(line_terms) - width + 1):
        for term in line_terms[offset - 1]:
            counts[term] -= 1
            if counts[term] == 0:
                del counts[term]
        counts.update(line_terms[offset + width - 1])
        if len(counts) > best_score:
            best_offset, best_score = offset, len(counts)
    selected = []
    remaining = 2_000
    for index in range(first + best_offset, first + best_offset + width):
        if remaining <= 0:
            break
        text = lines[index][:min(160, remaining)]
        selected.append({"line_number": index + 1, "text": text, "truncated": len(text) < len(lines[index])})
        remaining -= len(text)
    visible_tokens = tokenize("\n".join(line["text"] for line in selected))
    return {
        "start_line": selected[0]["line_number"], "end_line": selected[-1]["line_number"],
        "lines": selected,
        "truncated": (selected[0]["line_number"] > start or selected[-1]["line_number"] < end
                      or any(line["truncated"] for line in selected)),
        "matched_tokens": sorted(visible_tokens & tokens),
    }


def tokenize(text: str) -> set[str]:
    """Split paths, snake_case, camelCase, and acronym prefixes into words."""
    whole_words = set(re.findall(r"[^\W_]+", text.casefold()))
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return whole_words | set(re.findall(r"[^\W_]+", text.casefold()))


def import_label(entry: dict) -> str:
    names = ", ".join(
        alias["name"] + (f" as {alias['alias']}" if alias["alias"] else "")
        for alias in entry["names"]
    )
    if entry["kind"] == "from":
        module = "." * entry["level"] + (entry["module"] or "")
        return f"from {module} import {names}"
    return f"import {names}"


class LexicalIndex:
    """Map each token to documents and weighted fields containing that token.

    Queries use AND matching: every token must occur in a document. Repeated
    words do not boost scores. Captured source is optional; the index never
    reopens files, including when building result snippets.
    """

    def __init__(self, report: dict) -> None:
        self.documents: list[dict] = []
        self.sources: dict[str, list[str]] = {}
        self.source_hashes: dict[str, str] = {}
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.coverage = {
            "file_count": len(report["files"]),
            "parsed_python_files": 0,
            "python_files_without_structure": 0,
            "parse_errors": [],
            "file_limit_reached": report["summary"]["file_limit_reached"],
            "files_with_source": 0,
            "files_without_source": 0,
        }
        for file in sorted(report["files"], key=lambda item: item["path"]):
            path = file["path"]
            if "source" in file:
                self.sources[path] = source_lines(file["source"])
                self.coverage["files_with_source"] += 1
                if "source_sha256" in file:
                    self.source_hashes[path] = file["source_sha256"]
            else:
                self.coverage["files_without_source"] += 1
            self.add_document(
                {"path": path, "kind": "file", "name": path,
                 "start_line": 1 if file["line_count"] else None,
                 "end_line": file["line_count"] or None},
                [(path, 1)], exact_names=[path],
            )
            structure = file.get("structure")
            if structure is None:
                if file["language"] == "Python":
                    self.coverage["python_files_without_structure"] += 1
                continue
            if structure["status"] != "ok":
                self.coverage["parse_errors"].append({"path": path, **structure["error"]})
                continue
            self.coverage["parsed_python_files"] += 1
            for symbol in structure["symbols"]:
                self.add_document(
                    {"path": path, "kind": symbol["kind"],
                     "name": symbol["qualified_name"], "start_line": symbol["start_line"],
                     "end_line": symbol["end_line"], "definition_line": symbol["definition_line"]},
                    [(symbol["name"], 4), (symbol["qualified_name"], 2), (path, 1)],
                    exact_names=[symbol["name"], symbol["qualified_name"]],
                )
            for entry in structure["imports"]:
                label = import_label(entry)
                self.add_document(
                    {"path": path, "kind": "import", "name": label,
                     "scope": entry["scope"], "start_line": entry["start_line"],
                     "end_line": entry["end_line"]},
                    [(label, 4), (entry["scope"] or "", 2), (path, 1)],
                    exact_names=[label],
                )

    def add_document(self, document: dict, fields: list[tuple[str, int]], *, exact_names: list[str]) -> None:
        document_id = len(self.documents)
        self.documents.append({**document, "_exact_names": {name.casefold() for name in exact_names}})
        for value, weight in fields:
            for token in tokenize(value):
                postings = self.postings[token]
                postings[document_id] = postings.get(document_id, 0) + weight
        lines = self.sources.get(document["path"])
        if lines is not None and document["start_line"] is not None:
            source = "\n".join(lines[document["start_line"] - 1:document["end_line"]])
            for token in tokenize(source):
                postings = self.postings[token]
                postings[document_id] = postings.get(document_id, 0) + 1

    def search(self, query: str, *, limit: int = 10) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        if len(query) > 500:
            raise ValueError("Search query must be at most 500 characters")
        tokens = tokenize(query)
        if not tokens:
            raise ValueError("Search query must contain a letter or number")
        # Intersect the shortest posting list first to reduce intermediate work.
        lists = sorted((self.postings.get(token, {}) for token in tokens), key=len)
        candidates = set(lists[0])
        for postings in lists[1:]:
            candidates.intersection_update(postings)
            if not candidates:
                break
        results = []
        for document_id in candidates:
            document = self.documents[document_id]
            score = sum(self.postings[token][document_id] for token in tokens)
            if query.strip().casefold() in document["_exact_names"]:
                score += 10
            results.append({
                **{key: value for key, value in document.items() if not key.startswith("_")},
                "score": score, "matched_tokens": sorted(tokens),
            })
        results.sort(key=lambda item: (
            -item["score"], (item["end_line"] or 0) - (item["start_line"] or 0),
            {"function": 0, "import": 0, "class": 1, "file": 2}[item["kind"]],
            item["path"], item["start_line"] or 0, item["kind"], item["name"],
        ))
        selected = results[:limit]
        for result in selected:
            lines = self.sources.get(result["path"])
            result["snippet"] = (make_snippet(lines, result["start_line"], result["end_line"], tokens)
                                 if lines is not None else None)
            result["source_sha256"] = self.source_hashes.get(result["path"])
        return {
            "query": query, "total_matches": len(results), "results": selected,
            "coverage": self.coverage,
        }

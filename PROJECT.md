# Codebase Intelligence Platform

## Purpose

Help a developer understand an unfamiliar GitHub repository, locate relevant code, and investigate bugs using evidence from the source. Later milestones add proposed patches and isolated test execution.

## Working approach

Build one small, working feature at a time. Explain its purpose, implementation, and tradeoffs before moving to the next feature.

## Proposed stack

- Python and FastAPI for repository analysis and the API.
- Python's built-in AST for initial Python parsing; evaluate Tree-sitter when adding other languages.
- PostgreSQL and pgvector for persistent metadata and vector retrieval.
- React and TypeScript for the interface.
- Docker Compose for local services once persistence is needed.

Start with a modular application. Introduce queues and additional services when the workload requires them. Choose the model provider when we reach embeddings and question answering.

## Milestones

1. **Local repository inventory (implemented):** accept a local folder, ignore generated and sensitive files, and report source files, languages, sizes, and line counts. No AI service is required.
2. **Code structure (implemented):** extract Python definitions and imports, preserving file paths and source line ranges. Use `--structure` to include this analysis in the inventory.
3. **Public GitHub import:** validate repository URLs, fetch source with resource limits, and track analysis status. Never execute imported code during ingestion.
4. **Search:** implement lexical retrieval first, then embeddings and hybrid retrieval. Evaluate against a small collection of repository questions.
5. **Grounded answers:** answer questions with source citations, show supporting excerpts, and say when evidence is insufficient.
6. **Interface:** provide import status, repository browsing, search, and question answering.
7. **Dependency exploration:** show supported dependency relationships and distinguish static evidence from inferred relationships.
8. **Debugging workflow:** investigate an issue, propose a reviewable patch, and run explicitly selected checks in an isolated environment with resource and network limits. Report verification results accurately.

## First milestone acceptance criteria

- Analyze a local sample repository without changing its files.
- Exclude version-control metadata, virtual environments, dependency folders, generated files, and common secret files.
- Skip binary files and enforce file-size and file-count limits.
- Do not follow symlinks outside the selected repository.
- Produce a deterministic report of included files and summary totals.
- Cover file selection, exclusions, and limits with meaningful tests.

## Portfolio evidence

Keep architecture decisions, retrieval evaluation results, measured performance, and a reproducible demo. Describe supported behavior and limitations honestly.

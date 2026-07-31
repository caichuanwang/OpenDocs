# Repository Guidelines

## Project Structure & Module Organization

OpenDocs is a Python 3.11+ SDK using a `src` layout. Public APIs live in
`src/opendocs/__init__.py` and `src/opendocs/api.py`; source normalization, detection, Markdown
rendering, options, and typed errors are kept in focused sibling modules. Parser implementations
belong in `src/opendocs/parsers/` and should remain private until intentionally exported.

Tests mirror the source modules under `tests/test_<module>.py`. Architecture notes and milestone
plans live in `docs/`. Local acceptance documents belong in `tests/corpus/`; this directory and
`tests/corpus.local.toml` are private, ignored inputs and must never be committed. Files in
`tests/corpus/` are the baseline fixtures for acceptance and parser regression tests. Keep their
names and contents stable; intentional replacements require updating the hashes in
`tests/corpus.example.toml`.

## Upstream Parsing Reference

OpenDocs is derived from the document-parsing capabilities in the local `43x-agent` checkout at
`/Users/caichuanwang/Documents/liao/43x/43x-agent`. When implementing or reviewing parsers, use that
project as a primary reference for proven behavior, architecture, format strategies, edge cases,
model prompts, and test ideas instead of rediscovering them from scratch. Trace the relevant
`43x-agent` code paths and compare observable behavior before designing an OpenDocs equivalent.

Keep the provenance and redistribution boundary explicit: reference and adapt behavior, but write
OpenDocs source and tests independently unless permission to redistribute verbatim code has been
recorded. Never copy private documents, credentials, model payloads, generated outputs, deployment
configuration, or application-specific storage/logging/agent dependencies into this repository.

## Build, Test, and Development Commands

```bash
uv sync --all-groups --frozen          # Install locked runtime and development dependencies
uv run --frozen pytest -q              # Run the public test suite
uv run --frozen ruff check .           # Lint imports, style, and common Python defects
uv run --frozen ruff format --check .  # Verify formatting without rewriting files
uv run --frozen ty check src tests     # Type-check production and test code
uv build                               # Build the wheel and source distribution in dist/
```

Run the optional private corpus gate with
`uv run --frozen pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local`.

## Coding Style & Naming Conventions

Use four-space indentation, double quotes, type annotations, and a 100-character line limit.
Ruff enforces `E`, `F`, `I`, `UP`, `B`, `SIM`, and `RUF` rules. Name modules and functions in
`snake_case`, classes in `PascalCase`, and constants in `UPPER_SNAKE_CASE`. Keep `parse()` and
`aparse()` behavior aligned. User-facing parse results must be Markdown; the core SDK must not
download HTTP, OSS, or S3 URLs.

## Testing Guidelines

Use pytest and pytest-asyncio. Name tests `test_<behavior>` and lock regressions with a failing test
before changing production code. Cover equivalent path, bytes, and binary-stream inputs where an
API contract applies. Run targeted tests first, then the complete verification commands above.

Private acceptance checklists may be tool-assisted, but their initial baseline must be reviewed and
explicitly approved by a maintainer against the source documents. After approval, regression checks
must be automated. Never regenerate or accept a baseline merely to make a failing gate pass. Commit
only the checklist schema, validator, and synthetic examples; real content anchors, expectation
hashes, model output, and completed checklists must remain in ignored local files.

## Commit & Pull Request Guidelines

Follow the repository's Lore convention: start with an intent line explaining why the change
exists, then add useful git trailers such as `Constraint:`, `Rejected:`, `Confidence:`,
`Scope-risk:`, `Tested:`, and `Not-tested:`. Pull requests should describe scope and behavior,
link relevant issues or milestones, list exact verification performed, and call out dependency,
format-support, corpus, or runtime gaps. Never include credentials, private documents, model
payloads, or generated parse output.

# lanauage
你的第一工作语言是简体中文，所有报告文档等输出中文

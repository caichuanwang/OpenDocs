# Contributing to OpenDocs

## Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git

## Setup

```bash
git clone https://github.com/caichuanwang/OpenDocs.git
cd OpenDocs
uv sync --all-groups --frozen
```

## Public verification gate

Run the same public checks used by contributors and CI:

```bash
uv sync --all-groups --frozen
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests
uv build
git diff --check
```

Expected public result:

- `pytest` passes with five private-corpus skips when `--corpus-dir` is not provided
- Ruff, formatting, ty, and build exit `0`
- `dist/` contains a wheel and source distribution and remains ignored

## Optional local acceptance corpus

The private acceptance corpus is a local addendum, not part of public CI:

```bash
uv run --frozen pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local
```

Keep the corpus outside Git. `tests/corpus.local.toml` is a local manifest only and must remain
ignored.

## Test-first workflow

1. Add or update a failing test that locks the intended behavior.
2. Implement the smallest change that makes the targeted test pass.
3. Re-run the targeted test, then the full public verification gate.
4. Run the optional local corpus addendum when the change affects acceptance behavior.
5. Commit only after the verification evidence is fresh.

## Provenance and redistribution boundary

OpenDocs adapts behavior and contracts proven in `43x-agent`, but source and tests in this
repository must be independently written unless redistribution permission for verbatim copied code
has been explicitly recorded. Do not copy private files, credentials, model payloads, generated
outputs, or user corpus documents into this repository.

## Commit format

Use the Lore commit protocol. The first line explains why the change exists, followed by a short
body when useful, then git-native trailers after a blank line.

```text
<intent line: why this change is needed>

<optional body>

Constraint: <external force or boundary>
Rejected: <alternative> | <reason>
Confidence: <low|medium|high>
Scope-risk: <narrow|moderate|broad>
Directive: <forward-looking warning>
Tested: <verification performed>
Not-tested: <known gap>
```

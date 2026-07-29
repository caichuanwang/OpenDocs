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
rm -rf dist && uv build
git diff --check
```

Expected public result:

- `pytest` passes with the private-corpus and M1 replay/live gates skipped when their explicit
  options are not provided
- Ruff, formatting, ty, and build exit `0`
- `dist/` contains a wheel and source distribution and remains ignored

## Isolated wheel verification

After `uv build`, verify the built wheel in a fresh task-scoped virtual environment. The following
recipe targets a POSIX shell, matching the Linux CI runner; on Windows, use the corresponding
`venv\\Scripts` executables.

```bash
opendocs_wheel_check_dir="$(mktemp -d)"
trap 'rm -rf "$opendocs_wheel_check_dir"' EXIT
opendocs_wheel_check_python="$(
  uv run --frozen python -c 'import sys; print(sys.executable)'
)"
"$opendocs_wheel_check_python" -m venv "$opendocs_wheel_check_dir/venv"
opendocs_wheel_path="$(
  "$opendocs_wheel_check_python" -c 'from pathlib import Path; wheels = sorted(Path("dist").glob("opendocs_sdk-*.whl"), key=lambda path: path.stat().st_mtime, reverse=True); print(wheels[0] if wheels else "")'
)"
test -n "$opendocs_wheel_path" && test -f "$opendocs_wheel_path"
"$opendocs_wheel_check_dir/venv/bin/pip" install "$opendocs_wheel_path"
"$opendocs_wheel_check_dir/venv/bin/python" -c 'import asyncio; from opendocs import ParseOptions, VisionConfig, aparse, parse; assert parse(b"hello") == "hello\n"; assert asyncio.run(aparse(b"hello")) == "hello\n"; ParseOptions(); VisionConfig(model="openai/vision-model")'
```

Expected result:

- `opendocs_wheel_check_python` resolves to a Python 3.11+ interpreter from the synced project
  environment
- the newest built `dist/opendocs_sdk-*.whl` resolves into `opendocs_wheel_path`
- the shell cleanup trap removes only the `mktemp -d` directory created for this verification
- `parse`, `aparse`, `ParseOptions`, and `VisionConfig` import from the installed wheel
- sync and async hello smoke assertions both exit `0`

## Architectural boundary scans

Run these scans before committing changes that might blur milestone boundaries:

```bash
! rg -n "python-docx|python-pptx|PyMuPDF|PyZeroX" pyproject.toml src tests
rg -n "litellm|pdfplumber|PIL" pyproject.toml src/opendocs
rg -n "_REMOTE_SCHEMES" src/opendocs/source.py
! rg -n "^(from|import) (requests|httpx|boto3|oss2|urllib\.request)" src/opendocs
! rg -n "from opendocs\._models|from opendocs\.source|from opendocs\.parsers" \
  src/opendocs/__init__.py
```

Expected result:

- only M1 Pillow/pdfplumber/LiteLLM dependencies are present; future Office/PyMuPDF dependencies
  remain absent
- `_REMOTE_SCHEMES` is present in `src/opendocs/source.py`
- the forbidden network-import scan returns no matches
- `src/opendocs/__init__.py` does not re-export private modules

## Optional local acceptance corpus

The private acceptance corpus is a local addendum, not part of public CI:

```bash
uv run --frozen pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local
```

The deterministic M1 replay gate additionally consumes a local `results.json` fixture. It must use
schema version 1, contain exactly the three public-manifest M1 filenames under `results`, and map
each filename to a non-empty `elements` list. Elements use the public test protocol's tagged
`text`/`table`, `source_index`, optional normalized `bbox`, and table `grid`/`header_rows` fields.
Run it with:

```bash
uv run --frozen pytest tests/test_m1_acceptance.py -q \
  --corpus-dir=@local --m1-replay-dir=tests/m1-replay.local
```

The live gate is doubly opt-in and is never run by public CI:

```bash
OPENDOCS_VISION_MODEL=<provider/model> \
OPENDOCS_VISION_API_KEY=<secret> \
uv run --frozen pytest tests/test_m1_acceptance.py -q --corpus-dir=@local --m1-live
```

`OPENDOCS_VISION_API_BASE` is optional. Keep the corpus, replay output, credentials, and model output
outside Git. `tests/corpus.local.toml` and `tests/m1-replay.local/` are local-only inputs. The M1
fixture validates all three private file paths and hashes before exposing any path to parsing tests;
it never prints document content or model payloads.

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

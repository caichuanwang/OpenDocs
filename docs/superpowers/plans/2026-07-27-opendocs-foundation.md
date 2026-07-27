# OpenDocs M0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an installable Python 3.11+ SDK whose `parse()` and `aparse()` APIs convert TXT and Markdown inputs from local paths, bytes, and binary streams into deterministic Markdown.

**Architecture:** Keep the public API thin: normalize caller-owned sources into a scoped local representation, detect the document type independently, dispatch through a static parser registry, produce private ordered blocks, and render through one Markdown path. M0 exposes the final public contracts and implements TXT/Markdown only; PDF, image, Office, Poppler, Pillow, pdfplumber, python-docx, python-pptx, and LiteLLM enter in their owning milestones.

**Tech Stack:** Python 3.11+, uv, hatchling, standard-library dataclasses/asyncio/tempfile/zipfile, pytest, pytest-asyncio, Ruff, ty, GitHub Actions

---

## Scope Guardrails

- The accepted design is `docs/superpowers/specs/2026-07-27-opendocs-foundation-and-roadmap-design.md`.
- `str` always means a local filesystem path. It never means document contents or a downloadable URL.
- Public calls return only `str` Markdown. Private dataclasses may change before the Node.js milestone.
- M0 detects planned formats but registers parsers only for TXT and Markdown. A detected PDF, image,
  DOCX, or PPTX therefore raises a typed unsupported-format error until its milestone lands.
- `VisionConfig` is part of the stable public signature in M0, but no LiteLLM package is installed and
  no model call is made. M1 will consume the configuration through the internal vision adapter.
- The five acceptance files remain outside Git. M0 verifies their filenames and SHA-256 values only
  when `pytest` receives an explicit `--corpus-dir`.
- Source behavior and tests are independently written from the accepted design. Do not copy source
  text verbatim from `43x-agent` unless redistribution permission has been recorded.

## Target Tree After M0

```text
OpenDocs/
|-- .github/workflows/ci.yml
|-- .gitignore
|-- CONTRIBUTING.md
|-- LICENSE
|-- README.md
|-- docs/
|   |-- roadmap.md
|   `-- superpowers/
|       |-- plans/2026-07-27-opendocs-foundation.md
|       `-- specs/2026-07-27-opendocs-foundation-and-roadmap-design.md
|-- pyproject.toml
|-- src/opendocs/
|   |-- __init__.py
|   |-- _models.py
|   |-- api.py
|   |-- detection.py
|   |-- errors.py
|   |-- markdown.py
|   |-- options.py
|   |-- source.py
|   `-- parsers/
|       |-- __init__.py
|       |-- base.py
|       |-- registry.py
|       `-- text.py
|-- tests/
|   |-- conftest.py
|   |-- corpus.example.toml
|   |-- test_acceptance_corpus.py
|   |-- test_api.py
|   |-- test_detection.py
|   |-- test_errors.py
|   |-- test_markdown.py
|   |-- test_models.py
|   |-- test_options.py
|   |-- test_package.py
|   |-- test_registry.py
|   |-- test_source.py
|   `-- test_text_parser.py
`-- uv.lock
```

## Public Contract Locked by M0

- `Source` is `str | os.PathLike[str] | bytes | BinaryIO`.
- `parse(source, *, options=None, vision=None) -> str` is the synchronous entry point.
- `aparse(source, *, options=None, vision=None) -> str` is the asynchronous entry point.
- Both entry points return the same Markdown for equivalent input and never return private models.

## Verification Commands Used Throughout

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check src tests
uv build
```

Expected final result: all commands exit `0`, pytest reports no failures, and `dist/` contains one
source distribution and one wheel.

## Task 1: Bootstrap Packaging and Quality Tooling

**Files:**

- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/opendocs/__init__.py`
- Modify: `.gitignore`
- Create: `tests/test_package.py`
- Generate: `uv.lock`

- [ ] **Step 1: Write the failing package smoke test**

```python
# tests/test_package.py
import opendocs


def test_package_exposes_a_version() -> None:
    assert opendocs.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the test and confirm the package does not exist yet**

Run:

```bash
uvx pytest tests/test_package.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'opendocs'`.

- [ ] **Step 3: Add exact project and tool configuration**

```toml
# pyproject.toml
[build-system]
requires = ["hatchling>=1.31"]
build-backend = "hatchling.build"

[project]
name = "opendocs"
version = "0.1.0"
description = "Convert local documents to Markdown"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "OpenDocs contributors" }]
classifiers = [
  "Development Status :: 2 - Pre-Alpha",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
]
dependencies = []

[dependency-groups]
dev = [
  "pytest>=9.1",
  "pytest-asyncio>=1.4",
  "ruff>=0.16",
  "ty>=0.0.63",
]

[tool.hatch.build.targets.wheel]
packages = ["src/opendocs"]

[tool.pytest.ini_options]
addopts = "--strict-config --strict-markers"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.ruff.format]
quote-style = "double"

```

Create the README needed by package metadata before the editable install is built:

```markdown
# OpenDocs

OpenDocs converts local documents to Markdown. The M0 foundation is under implementation.
```

```python
# src/opendocs/__init__.py
__version__ = "0.1.0"

__all__ = ["__version__"]
```

Append these entries to `.gitignore` while preserving the existing license-sensitive exclusions:

```gitignore
.venv/
.pytest_cache/
.ruff_cache/
.ty/
__pycache__/
*.py[cod]
build/
dist/
tests/corpus.local.toml
```

- [ ] **Step 4: Resolve dependencies and run the package smoke test**

Run:

```bash
uv lock
uv sync --all-groups
uv run pytest tests/test_package.py -q
uv run ruff check pyproject.toml src/opendocs tests/test_package.py
```

Expected: the test passes and Ruff exits `0`.

- [ ] **Step 5: Commit the packaging boundary**

```bash
git add pyproject.toml uv.lock .gitignore README.md src/opendocs/__init__.py tests/test_package.py
git commit -m "Make the SDK installable before adding parser behavior" \
  -m "Establish the Python 3.11+ src layout and one reproducible development toolchain." \
  -m "Constraint: M0 has no runtime parsing dependencies" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: package smoke test and scoped Ruff"
```

## Task 2: Define Options and Stable Error Semantics

**Files:**

- Create: `src/opendocs/options.py`
- Create: `src/opendocs/errors.py`
- Modify: `src/opendocs/__init__.py`
- Create: `tests/test_options.py`
- Create: `tests/test_errors.py`

- [ ] **Step 1: Write option validation tests**

```python
# tests/test_options.py
from collections.abc import Callable

import pytest

from opendocs import ParseOptions, VisionConfig


def test_parse_options_defaults_match_the_public_contract() -> None:
    assert ParseOptions() == ParseOptions(
        timeout=900,
        max_pages=300,
        max_output_chars=400_000,
        vision_concurrency=4,
    )


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: ParseOptions(timeout=0), "timeout"),
        (lambda: ParseOptions(max_pages=0), "max_pages"),
        (lambda: ParseOptions(max_output_chars=0), "max_output_chars"),
        (lambda: ParseOptions(vision_concurrency=0), "vision_concurrency"),
    ],
)
def test_parse_options_reject_non_positive_values(
    factory: Callable[[], ParseOptions],
    field: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        factory()


def test_vision_config_requires_a_model() -> None:
    with pytest.raises(ValueError, match="model"):
        VisionConfig(model="  ")


def test_vision_config_defaults_are_explicit() -> None:
    assert VisionConfig(model="openai/vision-model") == VisionConfig(
        model="openai/vision-model",
        api_key=None,
        api_base=None,
        timeout=120,
        max_retries=2,
    )
```

- [ ] **Step 2: Write exception contract tests**

```python
# tests/test_errors.py
from opendocs import InvalidSourceError, OpenDocsError, OpenDocsErrorCode


def test_error_exposes_stable_machine_fields() -> None:
    error = InvalidSourceError("missing file")

    assert isinstance(error, OpenDocsError)
    assert error.code is OpenDocsErrorCode.INVALID_SOURCE
    assert error.retryable is False
    assert str(error) == "missing file"


def test_error_repr_contains_the_code() -> None:
    error = InvalidSourceError("missing file")

    assert "invalid_source" in repr(error)
```

- [ ] **Step 3: Run both files and confirm import failures**

Run:

```bash
uv run pytest tests/test_options.py tests/test_errors.py -q
```

Expected: collection fails because the public classes do not exist.

- [ ] **Step 4: Implement frozen validated configuration**

```python
# src/opendocs/options.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParseOptions:
    timeout: float = 900
    max_pages: int = 300
    max_output_chars: int = 400_000
    vision_concurrency: int = 4

    def __post_init__(self) -> None:
        for name in ("timeout", "max_pages", "max_output_chars", "vision_concurrency"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class VisionConfig:
    model: str
    api_key: str | None = None
    api_base: str | None = None
    timeout: float = 120
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
```

- [ ] **Step 5: Implement the public error hierarchy**

```python
# src/opendocs/errors.py
from enum import StrEnum


class OpenDocsErrorCode(StrEnum):
    INVALID_SOURCE = "invalid_source"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    DOCUMENT_TYPE_MISMATCH = "document_type_mismatch"
    CORRUPT_DOCUMENT = "corrupt_document"
    LIMIT_EXCEEDED = "limit_exceeded"
    TIMEOUT = "timeout"
    VISION_REQUIRED = "vision_required"
    MODEL_AUTHENTICATION = "model_authentication"
    MODEL_PERMISSION = "model_permission"
    MODEL_INVALID_REQUEST = "model_invalid_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    NO_USABLE_CONTENT = "no_usable_content"
    SYNC_IN_ASYNC_CONTEXT = "sync_in_async_context"


class OpenDocsError(Exception):
    code: OpenDocsErrorCode
    retryable: bool

    def __init__(
        self,
        message: str,
        *,
        code: OpenDocsErrorCode,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code.value!r}, "
            f"retryable={self.retryable!r}, message={str(self)!r})"
        )


class InvalidSourceError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.INVALID_SOURCE)


class UnsupportedDocumentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.UNSUPPORTED_DOCUMENT)


class DocumentTypeMismatchError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.DOCUMENT_TYPE_MISMATCH)


class CorruptDocumentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.CORRUPT_DOCUMENT)


class LimitExceededError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.LIMIT_EXCEEDED)


class DocumentTimeoutError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.TIMEOUT, retryable=True)


class VisionRequiredError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.VISION_REQUIRED)


class NoUsableContentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.NO_USABLE_CONTENT)


class SyncInAsyncContextError(OpenDocsError):
    def __init__(self) -> None:
        super().__init__(
            "parse() cannot run inside an active event loop; use await aparse() instead",
            code=OpenDocsErrorCode.SYNC_IN_ASYNC_CONTEXT,
        )


class OpenDocsWarning(UserWarning):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
```

- [ ] **Step 6: Re-export only the stable public surface**

Update `src/opendocs/__init__.py` to import and list `ParseOptions`, `VisionConfig`,
`OpenDocsErrorCode`, `OpenDocsError`, every concrete public error, and `OpenDocsWarning` in
`__all__`. Do not export private model classes.

- [ ] **Step 7: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_options.py tests/test_errors.py -q
uv run ruff check src/opendocs/options.py src/opendocs/errors.py tests/test_options.py tests/test_errors.py
uv run ty check src/opendocs/options.py src/opendocs/errors.py
```

Expected: all checks pass.

- [ ] **Step 8: Commit the public configuration and error contract**

```bash
git add src/opendocs/__init__.py src/opendocs/options.py src/opendocs/errors.py \
  tests/test_options.py tests/test_errors.py
git commit -m "Give callers stable configuration and failure semantics" \
  -m "Freeze the M0 defaults and machine-readable error codes before parser dispatch is added." \
  -m "Constraint: public exceptions must remain usable by a future Node.js SDK" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: option and error unit tests, Ruff, and ty"
```

## Task 3: Add Private Blocks and the Only Markdown Renderer

**Files:**

- Create: `src/opendocs/_models.py`
- Create: `src/opendocs/markdown.py`
- Create: `tests/test_models.py`
- Create: `tests/test_markdown.py`

- [ ] **Step 1: Write immutable-model tests**

```python
# tests/test_models.py
from dataclasses import FrozenInstanceError

import pytest

from opendocs._models import DocumentType, ParsedDocument, TextBlock


def test_parsed_document_uses_ordered_immutable_blocks() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock(text="first"), TextBlock(text="second")),
    )

    assert document.blocks == (TextBlock(text="first"), TextBlock(text="second"))
    with pytest.raises(FrozenInstanceError):
        document.blocks = ()
```

- [ ] **Step 2: Write renderer contract tests**

```python
# tests/test_markdown.py
import pytest

from opendocs import LimitExceededError
from opendocs._models import (
    DocumentType,
    MarkdownBlock,
    ParsedDocument,
    TextBlock,
    WarningRecord,
)
from opendocs.markdown import render_markdown


def test_renderer_preserves_markdown_and_escapes_plain_text() -> None:
    document = ParsedDocument(
        document_type=DocumentType.MARKDOWN,
        blocks=(MarkdownBlock(markdown="# Heading"), TextBlock(text="*literal*")),
    )

    result = render_markdown(document, max_output_chars=100)

    assert result.markdown == "# Heading\n\n\\*literal\\*\n"


def test_renderer_keeps_one_canonical_representation() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock(text="alpha"), TextBlock(text="beta")),
    )

    result = render_markdown(document, max_output_chars=100)

    assert result.markdown == "alpha\n\nbeta\n"
    assert result.markdown.count("alpha") == 1


def test_renderer_truncates_only_before_a_block() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock(text="alpha"), TextBlock(text="a much longer second block")),
    )

    result = render_markdown(document, max_output_chars=10)

    assert result.markdown == "alpha\n"
    assert result.warnings == (
        WarningRecord(code="output_truncated", message="output stopped before block 2"),
    )


def test_renderer_rejects_when_no_block_fits() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock(text="too long"),),
    )

    with pytest.raises(LimitExceededError, match="no complete block"):
        render_markdown(document, max_output_chars=3)
```

- [ ] **Step 3: Run tests and confirm missing internal modules**

Run:

```bash
uv run pytest tests/test_models.py tests/test_markdown.py -q
```

Expected: collection fails because `_models.py` and `markdown.py` do not exist.

- [ ] **Step 4: Implement the private immutable representation**

```python
# src/opendocs/_models.py
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


class DocumentType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    PPTX = "pptx"


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    markdown: str


Block: TypeAlias = TextBlock | MarkdownBlock


@dataclass(frozen=True, slots=True)
class WarningRecord:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    document_type: DocumentType
    blocks: tuple[Block, ...]
    warnings: tuple[WarningRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown: str
    warnings: tuple[WarningRecord, ...] = ()
```

- [ ] **Step 5: Implement one deterministic renderer**

`src/opendocs/markdown.py` must:

1. Render `MarkdownBlock.markdown` unchanged except for outer trailing newlines.
2. Escape backslash, backtick, asterisk, underscore, square brackets, and angle brackets in
   `TextBlock.text`. Escape line-start `#` and `>` conservatively even without a following space.
   Escape `-` and `+` only when followed by whitespace, and for ordered markers escape only the
   `.` or `)` punctuation rather than the leading digits.
3. Join blocks with exactly one blank line and end non-empty output with one newline.
4. Admit whole blocks only. If a later block would exceed `max_output_chars`, stop before it and
   add one `output_truncated` warning.
5. Raise `LimitExceededError` if no complete non-empty block fits.

Use this exact escape helper and rendering algorithm:

```python
# src/opendocs/markdown.py
import re

from opendocs._models import MarkdownBlock, ParsedDocument, RenderResult, TextBlock, WarningRecord
from opendocs.errors import LimitExceededError, NoUsableContentError

_INLINE_MARKDOWN = re.compile(r"([\\`*_\[\]<>])")
_BLOCK_MARKDOWN = re.compile(r"(?m)^([ \t]{0,3})([#>]|[-+](?=\s)|(\d+)([.)])(?=\s))")


def _escape_plain_text(value: str) -> str:
    escaped = _INLINE_MARKDOWN.sub(r"\\\1", value)
    return _BLOCK_MARKDOWN.sub(_escape_block_marker, escaped)


def _escape_block_marker(match: re.Match[str]) -> str:
    indent = match.group(1)
    marker = match.group(2)
    ordered_prefix = match.group(3)
    ordered_delimiter = match.group(4)

    if ordered_prefix is not None and ordered_delimiter is not None:
        return f"{indent}{ordered_prefix}\\{ordered_delimiter}"
    return f"{indent}\\{marker}"


def _render_block(block: TextBlock | MarkdownBlock) -> str:
    if isinstance(block, MarkdownBlock):
        return block.markdown.rstrip("\n")
    return _escape_plain_text(block.text.rstrip("\n"))


def render_markdown(document: ParsedDocument, *, max_output_chars: int) -> RenderResult:
    rendered: list[str] = []
    warnings = list(document.warnings)

    for index, block in enumerate(document.blocks, start=1):
        value = _render_block(block)
        if not value:
            continue
        candidate = "\n\n".join([*rendered, value]) + "\n"
        if len(candidate) > max_output_chars:
            if not rendered:
                raise LimitExceededError("no complete block fits within max_output_chars")
            warnings.append(
                WarningRecord(
                    code="output_truncated",
                    message=f"output stopped before block {index}",
                )
            )
            break
        rendered.append(value)

    if not rendered:
        raise NoUsableContentError("document produced no usable content")

    return RenderResult(markdown="\n\n".join(rendered) + "\n", warnings=tuple(warnings))
```

- [ ] **Step 6: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_models.py tests/test_markdown.py -q
uv run ruff check src/opendocs/_models.py src/opendocs/markdown.py \
  tests/test_models.py tests/test_markdown.py
uv run ty check src/opendocs/_models.py src/opendocs/markdown.py
```

Expected: all checks pass.

- [ ] **Step 7: Commit the private representation and canonical renderer**

```bash
git add src/opendocs/_models.py src/opendocs/markdown.py \
  tests/test_models.py tests/test_markdown.py
git commit -m "Keep every user result on one Markdown rendering path" \
  -m "Introduce immutable private blocks and deterministic block-boundary output limits." \
  -m "Constraint: raw intermediate JSON must never be appended beside rendered content" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: model and renderer unit tests, Ruff, and ty"
```

## Task 4: Normalize Paths, Bytes, and Binary Streams Safely

**Files:**

- Create: `src/opendocs/source.py`
- Create: `tests/test_source.py`

- [ ] **Step 1: Write ownership and cleanup tests**

```python
# tests/test_source.py
import io
from pathlib import Path

import pytest

from opendocs import InvalidSourceError
from opendocs.source import materialize_source


@pytest.mark.asyncio
async def test_path_stays_caller_owned(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    async with materialize_source(path) as resolved:
        assert resolved.path == path.resolve()
        assert resolved.original_name == "notes.txt"
        assert resolved.owned is False

    assert path.exists()


@pytest.mark.asyncio
async def test_bytes_are_removed_after_success() -> None:
    async with materialize_source(b"hello") as resolved:
        temporary_path = resolved.path
        assert temporary_path.read_bytes() == b"hello"
        assert resolved.original_name is None
        assert resolved.owned is True

    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_owned_file_is_removed_after_failure() -> None:
    temporary_path: Path | None = None

    with pytest.raises(RuntimeError, match="parser failed"):
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            raise RuntimeError("parser failed")

    assert temporary_path is not None
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_binary_stream_is_consumed_but_not_closed() -> None:
    stream = io.BytesIO(b"# title")

    async with materialize_source(stream) as resolved:
        assert resolved.path.read_bytes() == b"# title"
        assert resolved.owned is True

    assert stream.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["https://example.com/a.pdf", "s3://bucket/a.pdf", "oss://b/a"])
async def test_remote_urls_are_rejected(source: str) -> None:
    with pytest.raises(InvalidSourceError, match="local paths"):
        async with materialize_source(source):
            raise AssertionError("remote URL unexpectedly materialized")


@pytest.mark.asyncio
async def test_missing_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="does not exist"):
        async with materialize_source(tmp_path / "missing.txt"):
            raise AssertionError("missing path unexpectedly materialized")
```

- [ ] **Step 2: Run tests and confirm the source module is missing**

Run:

```bash
uv run pytest tests/test_source.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'opendocs.source'`.

- [ ] **Step 3: Implement scoped source materialization**

`src/opendocs/source.py` must define:

```python
from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeAlias
from urllib.parse import urlparse

from opendocs.errors import InvalidSourceError, LimitExceededError

Source: TypeAlias = str | os.PathLike[str] | bytes | BinaryIO
_MAX_INPUT_BYTES = 100_000_000
_REMOTE_SCHEMES = frozenset({"http", "https", "s3", "oss"})


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    path: Path
    original_name: str | None
    owned: bool


def _validated_path(source: str | os.PathLike[str]) -> Path:
    value = os.fspath(source)
    if isinstance(value, bytes):
        raise InvalidSourceError("byte paths are not supported; pass file bytes instead")
    if urlparse(value).scheme.lower() in _REMOTE_SCHEMES:
        raise InvalidSourceError("OpenDocs accepts local paths and does not download remote URLs")
    path = Path(value).expanduser()
    if not path.exists():
        raise InvalidSourceError(f"source path does not exist: {path}")
    if not path.is_file():
        raise InvalidSourceError(f"source path is not a regular file: {path}")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.read(0)
    except OSError as error:
        raise InvalidSourceError(f"source path is not readable: {path}") from error
    if size > _MAX_INPUT_BYTES:
        raise LimitExceededError(f"source exceeds {_MAX_INPUT_BYTES} bytes")
    return path.resolve()


def _read_stream(stream: BinaryIO) -> bytes:
    try:
        value = stream.read(_MAX_INPUT_BYTES + 1)
    except (OSError, ValueError) as error:
        raise InvalidSourceError("binary file object could not be read") from error
    if not isinstance(value, bytes):
        raise InvalidSourceError("binary file object read() must return bytes")
    return value


def _create_temporary_path(data: bytes) -> Path:
    if len(data) > _MAX_INPUT_BYTES:
        raise LimitExceededError(f"source exceeds {_MAX_INPUT_BYTES} bytes")
    descriptor, raw_path = tempfile.mkstemp(prefix="opendocs-")
    path = Path(raw_path)
    try:
        os.close(descriptor)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _write_temporary(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)


def _cleanup_finished_write(task: asyncio.Task[None], path: Path) -> None:
    if not task.cancelled():
        task.exception()
    path.unlink(missing_ok=True)


async def _write_owned(data: bytes) -> Path:
    path = _create_temporary_path(data)
    write_task = asyncio.create_task(asyncio.to_thread(_write_temporary, path, data))
    try:
        await asyncio.shield(write_task)
    except asyncio.CancelledError:
        write_task.add_done_callback(lambda completed: _cleanup_finished_write(completed, path))
        raise
    except BaseException:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    return path


@asynccontextmanager
async def materialize_source(source: Source) -> AsyncIterator[ResolvedSource]:
    if isinstance(source, (str, os.PathLike)):
        path = await asyncio.to_thread(_validated_path, source)
        yield ResolvedSource(path=path, original_name=path.name, owned=False)
        return

    if isinstance(source, bytes):
        data = source
        original_name = None
    elif hasattr(source, "read"):
        data = await asyncio.to_thread(_read_stream, source)
        stream_name = getattr(source, "name", None)
        original_name = Path(stream_name).name if isinstance(stream_name, str) else None
    else:
        raise InvalidSourceError("source must be a local path, bytes, or a binary file object")

    path = await _write_owned(data)
    try:
        yield ResolvedSource(path=path, original_name=original_name, owned=True)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)
```

Implementation note: keep the hard 100 MB input limit private in M0. A public option can be added
only when callers demonstrate a need to tune it.

- [ ] **Step 4: Add the cancellation cleanup case**

Append this test and do not swallow cancellation in the implementation:

```python
@pytest.mark.asyncio
async def test_owned_file_is_removed_after_cancellation() -> None:
    entered = asyncio.Event()
    temporary_path: Path | None = None

    async def hold_source() -> None:
        nonlocal temporary_path
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_source())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert temporary_path is not None
    assert not temporary_path.exists()
```

Add `import asyncio` to `tests/test_source.py`.

Append a second test that forces cancellation while the background write is still running. This
locks the `_write_owned()` cleanup path rather than only the context manager's post-yield cleanup:

```python
@pytest.mark.asyncio
async def test_cancellation_during_write_returns_promptly_and_cleans_eventually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    temporary_path: Path | None = None

    def delayed_write(path: Path, data: bytes) -> None:
        nonlocal temporary_path
        temporary_path = path
        started.set()
        assert release.wait(timeout=5), "test did not release the background write"
        path.write_bytes(data)

    monkeypatch.setattr("opendocs.source._write_temporary", delayed_write)

    async def materialize() -> None:
        async with materialize_source(b"hello"):
            raise AssertionError("cancelled write unexpectedly reached yield")

    task = asyncio.create_task(materialize())
    assert await asyncio.to_thread(started.wait, 1), "background write did not start"
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
    finally:
        release.set()

    assert temporary_path is not None
    for _ in range(100):
        if not temporary_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not temporary_path.exists()
```

Add `import threading` to `tests/test_source.py`.

- [ ] **Step 5: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_source.py -q
uv run ruff check src/opendocs/source.py tests/test_source.py
uv run ty check src/opendocs/source.py tests/test_source.py
```

Expected: all checks pass, including cleanup after success, failure, and cancellation.

- [ ] **Step 6: Commit the source ownership boundary**

```bash
git add src/opendocs/source.py tests/test_source.py
git commit -m "Protect caller data while normalizing parser inputs" \
  -m "Materialize bytes and streams into owned temporary files with cleanup on every exit path." \
  -m "Constraint: the core never downloads HTTP, OSS, or S3 URLs" \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: never delete a path supplied by the caller" \
  -m "Tested: path, bytes, stream, failure, cancellation, and URL rejection tests"
```

## Task 5: Detect Planned Formats Without Importing Parsers

**Files:**

- Create: `src/opendocs/detection.py`
- Create: `tests/test_detection.py`

- [ ] **Step 1: Write signature, suffix, and container tests**

```python
# tests/test_detection.py
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from opendocs import DocumentTypeMismatchError, UnsupportedDocumentError
from opendocs._models import DocumentType
from opendocs.detection import detect_document_type
from opendocs.source import ResolvedSource


def _resolved(path: Path, name: str | None = None) -> ResolvedSource:
    return ResolvedSource(path=path, original_name=name, owned=False)


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.txt", b"hello", DocumentType.TEXT),
        ("notes.md", b"# hello", DocumentType.MARKDOWN),
        ("paper.pdf", b"%PDF-1.7\n", DocumentType.PDF),
        ("image.png", b"\x89PNG\r\n\x1a\n", DocumentType.IMAGE),
        ("image.jpg", b"\xff\xd8\xff\xe0", DocumentType.IMAGE),
        ("image.webp", b"RIFF\x00\x00\x00\x00WEBP", DocumentType.IMAGE),
    ],
)
def test_detects_simple_formats(
    tmp_path: Path,
    name: str,
    content: bytes,
    expected: DocumentType,
) -> None:
    path = tmp_path / "source"
    path.write_bytes(content)

    assert detect_document_type(_resolved(path, name)) is expected


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("word/document.xml", DocumentType.DOCX),
        ("ppt/presentation.xml", DocumentType.PPTX),
    ],
)
def test_detects_office_containers(
    tmp_path: Path,
    member: str,
    expected: DocumentType,
) -> None:
    path = tmp_path / "office"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")

    assert detect_document_type(_resolved(path)) is expected


def test_unnamed_utf8_bytes_default_to_text(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes("中文内容".encode())

    assert detect_document_type(_resolved(path)) is DocumentType.TEXT


def test_extension_cannot_override_signature(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(DocumentTypeMismatchError, match="txt"):
        detect_document_type(_resolved(path, "wrong.txt"))


def test_named_unknown_extension_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(UnsupportedDocumentError, match=".unknown"):
        detect_document_type(_resolved(path, "file.unknown"))
```

- [ ] **Step 2: Run tests and confirm the detector is missing**

Run:

```bash
uv run pytest tests/test_detection.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'opendocs.detection'`.

- [ ] **Step 3: Implement independent type detection**

`src/opendocs/detection.py` must use only the standard library, `_models`, `errors`, and `source`.
It must not import `opendocs.parsers`.

Implement these exact rules in order:

1. Read at most 16 signature bytes.
2. Detect PDF, PNG, JPEG, and WebP by magic bytes.
3. For ZIP signatures, inspect member names and return DOCX or PPTX; an invalid ZIP raises
   `CorruptDocumentError`, and an unrelated ZIP raises `UnsupportedDocumentError`.
4. Map `.txt`, `.md`, `.markdown`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.docx`, and `.pptx`
   to declared types.
5. If a declared known extension conflicts with a detected signature/container type, raise
   `DocumentTypeMismatchError`.
6. For `.txt`, `.md`, and `.markdown`, require UTF-8 decodability.
7. If there is no original filename and no binary/container signature, return TEXT only when the
   entire file decodes as UTF-8.
8. Reject unknown named suffixes and undecodable unnamed bytes with `UnsupportedDocumentError`.

Keep signature helpers private and implement the complete detector without parser dispatch:

```python
# src/opendocs/detection.py
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from opendocs._models import DocumentType
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTypeMismatchError,
    UnsupportedDocumentError,
)
from opendocs.source import ResolvedSource

_SUFFIX_TYPES = {
    ".txt": DocumentType.TEXT,
    ".md": DocumentType.MARKDOWN,
    ".markdown": DocumentType.MARKDOWN,
    ".pdf": DocumentType.PDF,
    ".png": DocumentType.IMAGE,
    ".jpg": DocumentType.IMAGE,
    ".jpeg": DocumentType.IMAGE,
    ".webp": DocumentType.IMAGE,
    ".docx": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
}
_ZIP_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _signature_type(signature: bytes) -> DocumentType | None:
    if signature.startswith(b"%PDF-"):
        return DocumentType.PDF
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return DocumentType.IMAGE
    if signature.startswith(b"\xff\xd8\xff"):
        return DocumentType.IMAGE
    if signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
        return DocumentType.IMAGE
    return None


def _container_type(path: Path) -> DocumentType:
    try:
        with ZipFile(path) as archive:
            members = frozenset(archive.namelist())
    except BadZipFile as error:
        raise CorruptDocumentError("ZIP-based document is corrupt") from error

    if "word/document.xml" in members:
        return DocumentType.DOCX
    if "ppt/presentation.xml" in members:
        return DocumentType.PPTX
    raise UnsupportedDocumentError("ZIP container is neither DOCX nor PPTX")


def _require_utf8(path: Path) -> None:
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptDocumentError("text document is not valid UTF-8") from error


def _mismatch(suffix: str, detected: DocumentType | None) -> DocumentTypeMismatchError:
    actual = detected.value if detected is not None else "unknown content"
    return DocumentTypeMismatchError(f"declared extension {suffix} is incompatible with {actual}")


def detect_document_type(source: ResolvedSource) -> DocumentType:
    with source.path.open("rb") as handle:
        signature = handle.read(16)
    suffix = Path(source.original_name).suffix.lower() if source.original_name else ""
    if suffix and suffix not in _SUFFIX_TYPES:
        raise UnsupportedDocumentError(f"unsupported document extension: {suffix}")

    detected = _signature_type(signature)
    if detected is None and signature.startswith(_ZIP_PREFIXES):
        detected = _container_type(source.path)

    declared = _SUFFIX_TYPES.get(suffix)
    if declared in {DocumentType.TEXT, DocumentType.MARKDOWN}:
        if detected is not None:
            raise _mismatch(suffix, detected)
        _require_utf8(source.path)
        return declared

    if declared is not None:
        if detected is not declared:
            raise _mismatch(suffix, detected)
        return declared

    if detected is not None:
        return detected

    try:
        _require_utf8(source.path)
    except CorruptDocumentError as error:
        raise UnsupportedDocumentError("unnamed bytes have no supported signature") from error
    return DocumentType.TEXT
```

- [ ] **Step 4: Add corrupt ZIP and false-PDF tests**

Extend `tests/test_detection.py` with these exact cases and add `CorruptDocumentError` to the
existing error imports:

```python
def test_corrupt_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"PK\x03\x04not-a-zip")

    with pytest.raises(CorruptDocumentError, match="corrupt"):
        detect_document_type(_resolved(path))


def test_pdf_extension_requires_pdf_signature(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(DocumentTypeMismatchError, match=".pdf"):
        detect_document_type(_resolved(path, "false.pdf"))


def test_unrelated_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("data.json", "{}")

    with pytest.raises(UnsupportedDocumentError, match="neither DOCX nor PPTX"):
        detect_document_type(_resolved(path))
```

- [ ] **Step 5: Run focused tests and dependency checks**

Run:

```bash
uv run pytest tests/test_detection.py -q
uv run ruff check src/opendocs/detection.py tests/test_detection.py
uv run ty check src/opendocs/detection.py tests/test_detection.py
! rg -n "opendocs\.parsers" src/opendocs/detection.py
```

Expected: tests and static checks pass; the negated `rg` assertion finds no parser import and exits
`0`.

- [ ] **Step 6: Commit the format boundary**

```bash
git add src/opendocs/detection.py tests/test_detection.py
git commit -m "Detect document identity before parser dispatch" \
  -m "Use signatures and Office container members so extensions cannot silently lie." \
  -m "Constraint: detection must remain independent of parser implementations" \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Tested: text, Markdown, PDF, image, Office, mismatch, corrupt, and unsupported cases"
```

## Task 6: Establish the Static Parser Registry

**Files:**

- Create: `src/opendocs/parsers/__init__.py`
- Create: `src/opendocs/parsers/base.py`
- Create: `src/opendocs/parsers/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write registry behavior tests**

```python
# tests/test_registry.py
from opendocs import ParseOptions, UnsupportedDocumentError
from opendocs._models import DocumentType, ParsedDocument, TextBlock
from opendocs.parsers.base import DocumentParser
from opendocs.parsers.registry import ParserRegistry
from opendocs.source import ResolvedSource


class StubParser:
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        return ParsedDocument(
            document_type=DocumentType.TEXT,
            blocks=(TextBlock(text=source.path.name),),
        )


def test_registry_returns_the_exact_registered_parser() -> None:
    parser = StubParser()
    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, parser)

    assert registry.get(DocumentType.TEXT) is parser


def test_registry_rejects_duplicate_registration() -> None:
    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, StubParser())

    try:
        registry.register(DocumentType.TEXT, StubParser())
    except ValueError as error:
        assert "text" in str(error)
    else:
        raise AssertionError("duplicate registration must fail")


def test_registry_raises_typed_error_for_unimplemented_format() -> None:
    registry = ParserRegistry()

    try:
        registry.get(DocumentType.PDF)
    except UnsupportedDocumentError as error:
        assert "pdf" in str(error)
    else:
        raise AssertionError("missing parser must fail")


def test_stub_satisfies_parser_protocol() -> None:
    parser: DocumentParser = StubParser()
    assert isinstance(parser, StubParser)
```

- [ ] **Step 2: Run tests and confirm parser modules are missing**

Run:

```bash
uv run pytest tests/test_registry.py -q
```

Expected: collection fails because `opendocs.parsers.base` does not exist.

- [ ] **Step 3: Implement the protocol and registry**

```python
# src/opendocs/parsers/base.py
from typing import Protocol

from opendocs._models import ParsedDocument
from opendocs.options import ParseOptions
from opendocs.source import ResolvedSource


class DocumentParser(Protocol):
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        raise AssertionError("protocol methods are implemented by concrete parsers")
```

```python
# src/opendocs/parsers/registry.py
from opendocs._models import DocumentType
from opendocs.errors import UnsupportedDocumentError
from opendocs.parsers.base import DocumentParser


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[DocumentType, DocumentParser] = {}

    def register(self, document_type: DocumentType, parser: DocumentParser) -> None:
        if document_type in self._parsers:
            raise ValueError(f"parser already registered for {document_type.value}")
        self._parsers[document_type] = parser

    def get(self, document_type: DocumentType) -> DocumentParser:
        try:
            return self._parsers[document_type]
        except KeyError as error:
            raise UnsupportedDocumentError(
                f"support for {document_type.value} is not installed in this release"
            ) from error
```

`src/opendocs/parsers/__init__.py` stays empty so internal parser classes do not become public by
accident.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_registry.py -q
uv run ruff check src/opendocs/parsers tests/test_registry.py
uv run ty check src/opendocs/parsers tests/test_registry.py
```

Expected: all checks pass.

- [ ] **Step 5: Commit the explicit dispatch mechanism**

```bash
git add src/opendocs/parsers tests/test_registry.py
git commit -m "Make parser selection explicit and inspectable" \
  -m "Add one static registry instead of introducing dynamic plugin discovery before it is needed." \
  -m "Rejected: entry-point plugin discovery | no external parser ecosystem exists yet" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: registry selection, duplicate rejection, unsupported format, Ruff, and ty"
```

## Task 7: Implement TXT and Markdown Parsing End to End Internally

**Files:**

- Create: `src/opendocs/parsers/text.py`
- Modify: `src/opendocs/parsers/registry.py`
- Create: `tests/test_text_parser.py`

- [ ] **Step 1: Write parser tests for preservation and escaping boundaries**

```python
# tests/test_text_parser.py
from pathlib import Path

import pytest

from opendocs import CorruptDocumentError, ParseOptions
from opendocs._models import DocumentType, MarkdownBlock, TextBlock
from opendocs.parsers.registry import build_default_registry
from opendocs.source import ResolvedSource


@pytest.mark.asyncio
async def test_markdown_parser_preserves_source_markdown(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_bytes(b"# Heading\r\n\r\n- one\r\n")
    parser = build_default_registry().get(DocumentType.MARKDOWN)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (MarkdownBlock(markdown="# Heading\n\n- one\n"),)


@pytest.mark.asyncio
async def test_text_parser_splits_paragraphs_without_markdown_interpretation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"first\r\n\r\n*literal*\r\n")
    parser = build_default_registry().get(DocumentType.TEXT)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (TextBlock(text="first"), TextBlock(text="*literal*\n"))


@pytest.mark.asyncio
async def test_text_parser_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe")
    parser = build_default_registry().get(DocumentType.TEXT)

    with pytest.raises(CorruptDocumentError, match="UTF-8"):
        await parser.parse(
            ResolvedSource(path=path, original_name=path.name, owned=False),
            options=ParseOptions(),
        )
```

- [ ] **Step 2: Run tests and confirm the text parser is missing**

Run:

```bash
uv run pytest tests/test_text_parser.py -q
```

Expected: collection fails because `build_default_registry` and `text.py` do not exist.

- [ ] **Step 3: Implement bounded UTF-8 decoding and line normalization**

```python
# src/opendocs/parsers/text.py
import asyncio
import re

from opendocs._models import DocumentType, MarkdownBlock, ParsedDocument, TextBlock
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.options import ParseOptions
from opendocs.source import ResolvedSource

_MAX_TEXT_BYTES = 20_000_000
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _read_utf8(source: ResolvedSource) -> str:
    data = source.path.read_bytes()
    if len(data) > _MAX_TEXT_BYTES:
        raise LimitExceededError(f"text source exceeds {_MAX_TEXT_BYTES} bytes")
    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptDocumentError("text source is not valid UTF-8") from error
    return value.replace("\r\n", "\n").replace("\r", "\n")


class TextParser:
    def __init__(self, document_type: DocumentType) -> None:
        if document_type not in {DocumentType.TEXT, DocumentType.MARKDOWN}:
            raise ValueError("TextParser supports only text and markdown")
        self._document_type = document_type

    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        del options
        value = await asyncio.to_thread(_read_utf8, source)
        if self._document_type is DocumentType.MARKDOWN:
            blocks = (MarkdownBlock(markdown=value),)
        else:
            blocks = tuple(
                TextBlock(text=paragraph)
                for paragraph in _PARAGRAPH_BREAK.split(value)
                if paragraph.strip()
            )
        return ParsedDocument(document_type=self._document_type, blocks=blocks)
```

- [ ] **Step 4: Register exactly the two M0 parsers**

Append this factory to `src/opendocs/parsers/registry.py`:

```python
def build_default_registry() -> ParserRegistry:
    from opendocs.parsers.text import TextParser

    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, TextParser(DocumentType.TEXT))
    registry.register(DocumentType.MARKDOWN, TextParser(DocumentType.MARKDOWN))
    return registry
```

Keep the local import inside the factory to avoid an import cycle and to keep registry construction
explicit.

- [ ] **Step 5: Run parser and registry tests**

Run:

```bash
uv run pytest tests/test_text_parser.py tests/test_registry.py -q
uv run ruff check src/opendocs/parsers tests/test_text_parser.py tests/test_registry.py
uv run ty check src/opendocs/parsers tests/test_text_parser.py tests/test_registry.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit the first usable format parsers**

```bash
git add src/opendocs/parsers/text.py src/opendocs/parsers/registry.py tests/test_text_parser.py
git commit -m "Deliver real parsing behavior in the foundation milestone" \
  -m "Parse UTF-8 TXT and Markdown through private blocks while preserving Markdown source syntax." \
  -m "Constraint: M0 must remain free of PDF, Office, image, and model dependencies" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: TXT and Markdown parser tests plus registry regression tests"
```

## Task 8: Wire Public Synchronous and Asynchronous APIs

**Files:**

- Create: `src/opendocs/api.py`
- Modify: `src/opendocs/__init__.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write input-equivalence and API contract tests**

```python
# tests/test_api.py
import asyncio
import io
import threading
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from opendocs import (
    DocumentTimeoutError,
    OpenDocsWarning,
    ParseOptions,
    SyncInAsyncContextError,
    UnsupportedDocumentError,
    aparse,
    parse,
)
from opendocs._models import DocumentType
from opendocs.source import ResolvedSource


def _office_bytes(member: str) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("notes.txt", b"*literal*\n", "\\*literal\\*\n"),
        ("notes.md", b"# Heading\n", "# Heading\n"),
    ],
)
def test_parse_and_aparse_match_for_paths(
    tmp_path: Path,
    name: str,
    data: bytes,
    expected: str,
) -> None:
    path = tmp_path / name
    path.write_bytes(data)

    assert parse(path) == expected
    assert parse(str(path)) == expected
    assert asyncio.run(aparse(path)) == expected


def test_parse_and_aparse_match_for_unnamed_text_bytes_and_streams() -> None:
    data = b"*literal*\n"
    expected = "\\*literal\\*\n"

    assert parse(data) == expected
    assert asyncio.run(aparse(data)) == expected
    assert parse(io.BytesIO(data)) == expected
    assert asyncio.run(aparse(io.BytesIO(data))) == expected


@pytest.mark.asyncio
async def test_parse_rejects_a_running_event_loop() -> None:
    with pytest.raises(SyncInAsyncContextError, match="await aparse"):
        parse(b"hello")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "document_type"),
    [
        (b"%PDF-1.7\n", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "image"),
        (_office_bytes("word/document.xml"), "docx"),
        (_office_bytes("ppt/presentation.xml"), "pptx"),
    ],
)
async def test_detected_future_formats_are_typed_unsupported(
    content: bytes,
    document_type: str,
) -> None:
    with pytest.raises(UnsupportedDocumentError, match=document_type):
        await aparse(content)


@pytest.mark.asyncio
async def test_output_truncation_emits_a_capturable_warning() -> None:
    with pytest.warns(OpenDocsWarning, match="block 2") as captured:
        result = await aparse(
            b"alpha\n\na much longer second block",
            options=ParseOptions(max_output_chars=10),
        )

    assert result == "alpha\n"
    assert isinstance(captured[0].message, OpenDocsWarning)
    assert captured[0].message.code == "output_truncated"


@pytest.mark.asyncio
async def test_timeout_is_translated_and_temp_file_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_path: Path | None = None

    async def slow_detect(source: ResolvedSource) -> DocumentType:
        nonlocal materialized_path
        materialized_path = source.path
        await asyncio.sleep(1)
        return DocumentType.TEXT

    monkeypatch.setattr("opendocs.api._detect", slow_detect)

    with pytest.raises(DocumentTimeoutError):
        await aparse(b"hello", options=ParseOptions(timeout=0.01))

    assert materialized_path is not None
    assert not materialized_path.exists()


@pytest.mark.asyncio
async def test_timeout_during_temp_write_returns_promptly_and_cleans_eventually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    temporary_path: Path | None = None

    def delayed_write(path: Path, data: bytes) -> None:
        nonlocal temporary_path
        temporary_path = path
        started.set()
        assert release.wait(timeout=5), "test did not release the background write"
        path.write_bytes(data)

    monkeypatch.setattr("opendocs.source._write_temporary", delayed_write)
    parse_task = asyncio.create_task(aparse(b"hello", options=ParseOptions(timeout=0.05)))
    assert await asyncio.to_thread(started.wait, 1), "background write did not start"
    started_at = asyncio.get_running_loop().time()

    try:
        with pytest.raises(DocumentTimeoutError):
            await asyncio.wait_for(parse_task, timeout=0.3)
    finally:
        release.set()

    assert asyncio.get_running_loop().time() - started_at < 0.3
    assert temporary_path is not None
    for _ in range(100):
        if not temporary_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not temporary_path.exists()
```

- [ ] **Step 2: Run tests and confirm public functions are missing**

Run:

```bash
uv run pytest tests/test_api.py -q
```

Expected: collection fails because `parse` and `aparse` are not exported.

- [ ] **Step 3: Implement async-core orchestration**

`src/opendocs/api.py` must keep format-specific logic out of the public API. Use one private async
detector seam so timeout tests can pause without patching the synchronous detector:

```python
# src/opendocs/api.py
import asyncio
import warnings

from opendocs._models import DocumentType
from opendocs.detection import detect_document_type
from opendocs.errors import (
    DocumentTimeoutError,
    OpenDocsError,
    OpenDocsWarning,
    SyncInAsyncContextError,
)
from opendocs.markdown import render_markdown
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.registry import build_default_registry
from opendocs.source import ResolvedSource, Source, materialize_source


async def _detect(source: ResolvedSource) -> DocumentType:
    return await asyncio.to_thread(detect_document_type, source)


async def _parse_with_timeout(
    source: Source,
    *,
    options: ParseOptions,
    vision: VisionConfig | None,
) -> str:
    del vision
    async with asyncio.timeout(options.timeout):
        async with materialize_source(source) as resolved:
            document_type = await _detect(resolved)
            parser = build_default_registry().get(document_type)
            document = await parser.parse(resolved, options=options)
            result = render_markdown(document, max_output_chars=options.max_output_chars)

    for warning in result.warnings:
        warnings.warn(
            OpenDocsWarning(warning.message, code=warning.code),
            stacklevel=3,
        )
    return result.markdown


async def aparse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str:
    resolved_options = options or ParseOptions()
    try:
        return await _parse_with_timeout(source, options=resolved_options, vision=vision)
    except TimeoutError as error:
        raise DocumentTimeoutError(
            f"document parsing exceeded {resolved_options.timeout} seconds"
        ) from error
    except OpenDocsError:
        raise


def parse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aparse(source, options=options, vision=vision))
    raise SyncInAsyncContextError()
```

The `del vision` line is deliberate in M0: it keeps the accepted public signature stable without
pretending that vision support exists before M1.

- [ ] **Step 4: Export the final public API**

Replace `src/opendocs/__init__.py` with the complete public surface below. `Source`,
`ResolvedSource`, `DocumentType`, block classes, parser classes, registry classes, and rendering
helpers remain private because they are absent from `__all__`.

```python
# src/opendocs/__init__.py
from opendocs.api import aparse, parse
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    DocumentTypeMismatchError,
    InvalidSourceError,
    LimitExceededError,
    NoUsableContentError,
    OpenDocsError,
    OpenDocsErrorCode,
    OpenDocsWarning,
    SyncInAsyncContextError,
    UnsupportedDocumentError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig

__version__ = "0.1.0"

__all__ = [
    "CorruptDocumentError",
    "DocumentTimeoutError",
    "DocumentTypeMismatchError",
    "InvalidSourceError",
    "LimitExceededError",
    "NoUsableContentError",
    "OpenDocsError",
    "OpenDocsErrorCode",
    "OpenDocsWarning",
    "ParseOptions",
    "SyncInAsyncContextError",
    "UnsupportedDocumentError",
    "VisionConfig",
    "VisionRequiredError",
    "__version__",
    "aparse",
    "parse",
]
```

- [ ] **Step 5: Run API tests and full unit suite**

Run:

```bash
uv run pytest tests/test_api.py -q
uv run pytest -q
uv run ruff check src tests
uv run ty check src tests
```

Expected: all tests and static checks pass; path, bytes, and stream results are identical.

- [ ] **Step 6: Commit the usable SDK API**

```bash
git add src/opendocs/api.py src/opendocs/__init__.py tests/test_api.py
git commit -m "Expose one async core to synchronous and asynchronous callers" \
  -m "Compose normalization, detection, dispatch, parsing, rendering, warnings, and timeouts behind the public API." \
  -m "Constraint: parse() must reject nested event loops instead of starting one" \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: keep format-specific extraction out of api.py" \
  -m "Tested: public contract, source equivalence, warning, timeout, cleanup, Ruff, and ty"
```

## Task 9: Add the Opt-In Real Acceptance Corpus Gate

**Files:**

- Create: `tests/conftest.py`
- Create: `tests/corpus.example.toml`
- Create locally but do not commit: `tests/corpus.local.toml`
- Create: `tests/test_acceptance_corpus.py`

- [ ] **Step 1: Commit the exact corpus metadata without the source files**

```toml
# tests/corpus.example.toml
schema_version = 1

[[files]]
name = "Levabp.pptx"
sha256 = "caf47c6f95243d3bb8b709db5ff21583d112b8591f233cd0e4ffc435f4e638d2"
milestone = "M2"

[[files]]
name = "Tacore.aiBP.pdf"
sha256 = "6a2523ef5a71069d542d1721bcaace4c04eb1d4b06d3425a97c151a92c40bfcc"
milestone = "M1"

[[files]]
name = "明星肖像权授权合作协议.docx"
sha256 = "0fe01f7f7f23a0ae9f4fc2e06c1ac58a6228258a7414d8db9bc322ac03110d34"
milestone = "M2"

[[files]]
name = "外贸商业计划书_BP.pdf"
sha256 = "c1c42e60865eceb20661b14615e0ca404e8532c4d92c362e01fc324d02ab54d5"
milestone = "M1"

[[files]]
name = "a7097535a29f73941600af8d818a163a.png"
sha256 = "1f74d2381f05a41478b9e246d4b31b932fa129a79696c41482a32dfbe6b88c71"
milestone = "M1"
```

- [ ] **Step 2: Add an explicit pytest option**

```python
# tests/conftest.py
import tomllib
from pathlib import Path

import pytest

_LOCAL_MANIFEST = Path(__file__).with_name("corpus.local.toml")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--corpus-dir",
        dest="corpus_dir",
        action="store",
        type=str,
        default=None,
        help="private corpus directory, or @local to read tests/corpus.local.toml",
    )


@pytest.fixture(scope="session")
def corpus_dir(pytestconfig: pytest.Config) -> Path | None:
    value = pytestconfig.getoption("corpus_dir")
    if value == "@local":
        if not _LOCAL_MANIFEST.is_file():
            raise pytest.UsageError(f"local corpus manifest not found: {_LOCAL_MANIFEST}")
        with _LOCAL_MANIFEST.open("rb") as handle:
            payload = tomllib.load(handle)
        value = payload.get("corpus_dir")
        if not isinstance(value, str) or not value:
            raise pytest.UsageError("corpus.local.toml must define a non-empty corpus_dir")
    return Path(value).expanduser() if value is not None else None
```

- [ ] **Step 3: Write the hash-first corpus gate**

```python
# tests/test_acceptance_corpus.py
import hashlib
import tomllib
from pathlib import Path
from typing import TypedDict

import pytest


class CorpusFile(TypedDict):
    name: str
    sha256: str
    milestone: str


def _entries() -> list[CorpusFile]:
    manifest = Path(__file__).with_name("corpus.example.toml")
    with manifest.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload["files"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("entry", _entries(), ids=lambda entry: entry["name"])
def test_private_corpus_matches_manifest(
    entry: CorpusFile,
    corpus_dir: Path | None,
) -> None:
    if corpus_dir is None:
        pytest.skip("pass --corpus-dir to verify the private acceptance corpus")

    path = corpus_dir / entry["name"]
    assert path.is_file(), f"missing acceptance file: {path}"
    assert _sha256(path) == entry["sha256"], f"hash mismatch: {path}"
```

- [ ] **Step 4: Verify default tests skip and explicit tests validate all five files**

Run:

```bash
uv run pytest tests/test_acceptance_corpus.py -q
export OPENDOCS_CORPUS_DIR=/Users/caichuanwang/Downloads
uv run pytest tests/test_acceptance_corpus.py -q --corpus-dir "$OPENDOCS_CORPUS_DIR"
```

Expected: the first command reports five skips; the second reports five passes. Hash validation
happens before any parser-specific acceptance assertion.

- [ ] **Step 5: Create the ignored local manifest for developer convenience**

Create `tests/corpus.local.toml` with:

```toml
corpus_dir = "/Users/caichuanwang/Downloads"
```

Confirm it is ignored and is actually consumed by the explicit local option:

Use the equals form for `@local`; with pytest 9, a standalone `@...` token is reserved for
argument-file expansion before `conftest.py` is loaded.

```bash
git check-ignore -v tests/corpus.local.toml
uv run pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local
git status --short
```

Expected: `git check-ignore` points to the `.gitignore` rule, all five hash checks pass through the
manifest-resolved directory, and the local manifest does not appear in `git status`.

- [ ] **Step 6: Commit only the corpus harness and public metadata**

```bash
git add tests/conftest.py tests/corpus.example.toml tests/test_acceptance_corpus.py
git commit -m "Make private acceptance evidence reproducible without publishing files" \
  -m "Record exact filenames and hashes behind an explicit local corpus directory gate." \
  -m "Constraint: user-provided acceptance documents must remain outside Git" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: verify hashes before parser assertions or release claims" \
  -m "Tested: default skip behavior and five-file local hash validation"
```

## Task 10: Document Usage, Roadmap, Contribution, CI, and Build Proof

**Files:**

- Modify: `README.md`
- Create: `CONTRIBUTING.md`
- Create: `docs/roadmap.md`
- Create: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` only if final tool execution proves a configuration correction is needed

- [ ] **Step 1: Write README contract examples**

`README.md` must include:

- one-sentence purpose and M0 status;
- installation from the repository with `uv add` and `pip install` examples;
- synchronous path example;
- asynchronous bytes example;
- explicit supported-input list;
- explicit statement that HTTP/OSS/S3 download is the caller's responsibility;
- current format matrix showing TXT/Markdown available and PDF/image/DOCX/PPTX planned;
- warning/error handling example;
- architecture link, roadmap link, contribution link, and MIT license link;
- a note that `VisionConfig` is reserved for M1 and makes no model call in M0.

The two executable examples must use these exact calls:

```python
from opendocs import parse

markdown = parse("notes.md")
```

```python
from opendocs import aparse

markdown = await aparse(b"plain text")
```

- [ ] **Step 2: Write contribution and roadmap docs**

`CONTRIBUTING.md` must define:

- Python 3.11+ and uv prerequisites;
- environment setup and all verification commands;
- test-first contribution flow;
- dependency/provenance boundary for `43x-agent` adaptations;
- no private corpus files, credentials, model payloads, or generated output in commits;
- Lore commit message format used by this repository.

`docs/roadmap.md` must summarize M0 through M4 from the accepted design, list exit criteria for
each milestone, and link back to the full design rather than duplicating its internal algorithms.

- [ ] **Step 3: Add the CI matrix**

Use the current official uv action and test exactly Python 3.11, 3.12, and 3.13:

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
  push:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v7
      - name: Install uv
        uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          python-version: ${{ matrix.python-version }}
          enable-cache: true
      - name: Sync locked dependencies
        run: uv sync --all-groups --frozen
      - name: Run tests
        run: uv run --frozen pytest -q
      - name: Run Ruff
        run: uv run --frozen ruff check .
      - name: Check formatting
        run: uv run --frozen ruff format --check .
      - name: Run ty
        run: uv run --frozen ty check src tests
      - name: Build distributions
        run: uv build
```

- [ ] **Step 4: Verify README examples against the built package**

Run:

```bash
uv build
python3 -m venv /tmp/opendocs-m0-wheel-check
/tmp/opendocs-m0-wheel-check/bin/pip install dist/opendocs-0.1.0-py3-none-any.whl
/tmp/opendocs-m0-wheel-check/bin/python -c \
  'from opendocs import parse; assert parse(b"hello") == "hello\n"'
```

Expected: wheel installation succeeds and the isolated smoke command exits `0`.

- [ ] **Step 5: Run the public quality gate, then the explicit local corpus addendum**

Run the portable gate used by contributors and CI:

```bash
uv sync --all-groups --frozen
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests
uv build
git diff --check
```

Expected: public tests pass with exactly five private-corpus skips, static checks exit `0`, both
distribution artifacts are created, and `git diff --check` prints nothing.

Then run the local acceptance addendum. It is required for this implementation workspace but is
not part of public CI:

```bash
uv run --frozen pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local
```

Expected:

- all five private-corpus hash cases pass;
- no corpus file is copied into the repository or appears in `git status`.

- [ ] **Step 6: Run architectural boundary scans**

```bash
! rg -n "litellm|pdfplumber|python-docx|python-pptx|Pillow|PyMuPDF|PyZeroX" \
  pyproject.toml src tests
rg -n "_REMOTE_SCHEMES" src/opendocs/source.py
! rg -n "^(from|import) (requests|httpx|boto3|oss2|urllib\.request)" src/opendocs
! rg -n "from opendocs\._models|from opendocs\.source|from opendocs\.parsers" \
  src/opendocs/__init__.py
```

Expected:

- the dependency scan returns no matches;
- the remote-scheme set exists and no network client is imported;
- the public-export scan returns no matches.

- [ ] **Step 7: Commit documentation and continuous verification**

```bash
git add README.md CONTRIBUTING.md docs/roadmap.md .github/workflows/ci.yml
git commit -m "Make the M0 contract verifiable for users and contributors" \
  -m "Document the usable API, milestone boundaries, contribution rules, and a three-version CI gate." \
  -m "Constraint: public CI cannot access the private acceptance corpus" \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: full pytest, local corpus hashes, Ruff, formatting, ty, wheel build, and isolated install"
```

## Final M0 Acceptance Checklist

- [ ] `from opendocs import parse, aparse, ParseOptions, VisionConfig` works from an installed wheel.
- [ ] TXT and Markdown parse from `Path`, `str`, `bytes`, and `BinaryIO`.
- [ ] `parse()` and `aparse()` return byte-for-byte identical Markdown for equivalent sources.
- [ ] `parse()` raises `SyncInAsyncContextError` inside a running event loop.
- [ ] HTTP, HTTPS, OSS, and S3 strings raise `InvalidSourceError` without network activity.
- [ ] Caller paths and streams remain caller-owned; SDK-created temporary files are always deleted.
- [ ] Detection distinguishes PDF, image, DOCX, PPTX, TXT, and Markdown without importing parsers.
- [ ] PDF, image, DOCX, and PPTX are typed unsupported formats in M0, not silent text fallbacks.
- [ ] Only the Markdown renderer produces user-visible content.
- [ ] Output truncation occurs only before a block and emits `OpenDocsWarning`.
- [ ] Default tests never require credentials, model calls, Poppler, or private files.
- [ ] Explicit private-corpus verification passes all five SHA-256 checks.
- [ ] Python 3.11, 3.12, and 3.13 CI jobs run pytest, Ruff, formatting, ty, and wheel build checks.
- [ ] No M1/M2 runtime dependencies appear in `pyproject.toml` or `uv.lock`.
- [ ] `git status --short` shows no untracked generated output or private corpus metadata.

## Spec Coverage Matrix

| Accepted design area | Plan tasks |
| --- | --- |
| Packaging and Python 3.11+ | 1, 10 |
| Public options and error codes | 2, 8 |
| Private blocks and Markdown-only output | 3, 7, 8 |
| Local source inputs and ownership | 4, 8 |
| Signature/container detection | 5 |
| Static parser registry | 6, 7 |
| TXT and Markdown end to end | 7, 8 |
| Sync/async equivalence and timeout | 8 |
| Private acceptance corpus | 9, 10 |
| README, contribution, roadmap, CI | 10 |
| Deferred LiteLLM, PDF, image, Office work | Scope Guardrails, 5, 8, 10 |

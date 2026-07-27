# OpenDocs Foundation and Roadmap Design

**Status:** Accepted
**Date:** 2026-07-27

## Purpose

OpenDocs is an open-source Python SDK that converts common document formats into Markdown. It
adapts the proven document-parsing behavior in `43x-agent` while removing application-specific
configuration, storage, logging, and agent dependencies.

The first delivery establishes a usable framework rather than an empty scaffold. It implements the
public API, input normalization, parser registry, typed errors, deterministic Markdown rendering,
tests, and end-to-end TXT/Markdown parsing. Later milestones port PDF, image, DOCX, and PPTX
behavior into these boundaries.

## Goals

- Provide a small Python API that accepts a local path, bytes, or a binary file object and returns
  Markdown.
- Support synchronous and asynchronous callers with equivalent behavior.
- Support PDF, PNG/JPEG/WebP, DOCX, PPTX, TXT, and Markdown by the end of the initial roadmap.
- Use native parsing first and call a vision model only when native extraction is unavailable or
  unreliable.
- Use the LiteLLM Python SDK for vision-model calls without requiring a LiteLLM Gateway.
- Keep format parsers, model transport, intermediate models, and rendering independently testable.
- Preserve a path toward a future Node.js SDK by keeping input semantics, error codes, and Markdown
  behavior explicit.

## Non-goals

- HTTP, OSS, S3, or other remote downloads.
- A service, REST API, CLI, worker system, or persistent job store.
- Legacy binary DOC/PPT, Excel, HTML, email, or archive parsing in the initial roadmap.
- A dynamic plugin discovery system in the first release.
- A stable public JSON document schema in the first release.
- A bundled LiteLLM Gateway, local OCR engine, or provider-specific model gateway.

## Key Decisions

1. OpenDocs is a pure Python SDK. A Node.js SDK is a later milestone.
2. `parse()` and `aparse()` return Markdown strings. JSON/Pydantic models are private intermediate
   representations.
3. Inputs are limited to `Path`/`str` local paths, `bytes`, and binary file objects.
4. The core never downloads remote URLs. Callers download objects before invoking OpenDocs.
5. Format dispatch uses a static parser registry. There is no entry-point-based plugin system yet.
6. Vision calls use `litellm.acompletion()` behind one internal adapter.
7. All native and vision output passes through one Markdown renderer.
8. User-provided acceptance files stay outside Git and are selected through an explicit corpus
   directory.

## Architecture

```text
src/opendocs/
|-- __init__.py          # stable public exports
|-- api.py               # parse / aparse orchestration
|-- options.py           # ParseOptions and VisionConfig
|-- source.py            # source normalization and temporary ownership
|-- detection.py         # suffix, signature, and container detection
|-- errors.py            # stable public exception hierarchy
|-- _models.py           # private intermediate blocks and warnings
|-- markdown.py          # only final rendering path
|-- parsers/
|   |-- base.py          # parser protocol
|   |-- registry.py      # explicit document-type registry
|   |-- text.py
|   |-- image.py
|   |-- pdf/
|   `-- office/
`-- vision/
    |-- base.py          # internal vision request/result boundary
    `-- litellm.py       # LiteLLM call and response normalization
```

Each unit has one responsibility:

- `source.py` owns caller-input normalization and only deletes temporary files it created.
- `detection.py` determines a document type without importing individual parsers.
- `registry.py` maps a detected type to exactly one parser.
- A parser produces private ordered blocks and warnings, never final Markdown.
- `vision/litellm.py` is the only module that imports or exposes LiteLLM internally.
- `markdown.py` is the only module that converts blocks to user-facing output.
- `api.py` applies timeouts, dispatches, renders, and translates fatal failures.

No parser imports another parser, `api.py` contains no format-specific extraction logic, and no
LiteLLM type crosses the vision adapter boundary.

## Public API

```python
from opendocs import ParseOptions, VisionConfig, aparse, parse

vision = VisionConfig(
    model="openai/vision-model",
    api_key="...",
    api_base="https://example.com/v1",
)

markdown = parse(
    source,
    options=ParseOptions(),
    vision=vision,
)

markdown = await aparse(source, options=ParseOptions(), vision=vision)
```

The public signatures accept:

```python
Source = str | os.PathLike[str] | bytes | BinaryIO

def parse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str: ...

async def aparse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str: ...
```

`parse()` is the default ergonomic entry point. If it is called from a running event loop, it raises
a clear error instructing the caller to use `aparse()` rather than attempting a nested event loop.

## Source Normalization and Detection

- A path is validated as a readable regular file and remains caller-owned.
- Bytes and binary streams are copied to an owned temporary file only when a parser requires a
  filesystem path. Caller-owned streams are not closed.
- Owned temporary files are removed after success, parser failure, cancellation, or timeout.
- PDF and common image types are detected by file signature.
- DOCX and PPTX are distinguished by inspecting ZIP container members.
- UTF-8 text defaults to TXT semantics when no filename is available. This does not alter the final
  Markdown contract because TXT and Markdown both produce Markdown output.
- A declared extension never overrides an incompatible signature or container structure.

The core does not interpret `http://`, `https://`, `oss://`, or `s3://` as downloadable sources.

## Parsing Data Flow

```text
Path / bytes / BinaryIO
  -> source normalization
  -> type detection
  -> parser registry
  -> native parsing and optional vision work
  -> private ordered blocks and warnings
  -> deterministic Markdown renderer
  -> str
```

Asynchronous visual work may run concurrently, but its results are merged in source page/slide and
region order. Resource budgets are admitted in source order so faster later pages cannot consume
capacity ahead of earlier pages.

## Format Strategies

### TXT and Markdown

- Decode with an explicit, bounded strategy and normalize line endings.
- Preserve Markdown source content rather than re-parsing and regenerating it.
- Escape or normalize TXT only where needed to produce deterministic Markdown.

### Images

- Support PNG, JPEG, and WebP.
- Normalize orientation and dimensions while enforcing a hard pixel budget.
- Send base64 image data through LiteLLM.
- Treat ultra-wide images as table candidates and use a compact table-specific prompt.
- Require a vision configuration because there is no native text fallback.

### PDF

- Use one `pdfplumber + selective Poppler + vision` path.
- Analyze native text, tables, images, and drawings page by page.
- Route each page as native, hybrid, full vision, or blank.
- Render only unresolved pages or regions with Poppler.
- Preserve page order and replace visual-owned native content through a single merge path.
- Represent a table once using a rectangular `grid + header_rows` contract; never append both raw
  table JSON and rendered table text.
- Do not add PyZeroX, local OCR, or a second PDF fallback pipeline.

### DOCX and PPTX

- Use `python-docx` and `python-pptx` for native structure.
- Preserve document paragraph/table order and PPTX shape enumeration order.
- Normalize soft line breaks without reordering source content.
- Extract embedded images as visual regions and call LiteLLM only when a vision configuration is
  present.
- Sort by geometry only when combining native content with successful visual blocks; native-only
  PPTX output retains source order.

## Vision Integration

The M1 vision implementation depends on the LiteLLM Python SDK, not a running LiteLLM Gateway.

`vision/litellm.py` calls `litellm.acompletion()` and owns:

- model, API key, API base, timeout, retry, token, and optional header configuration;
- document-specific prompts and base64 image messages;
- JSON-mode requests when the selected model supports them;
- conservative parsing of JSON text when structured output is unavailable;
- Markdown fallback blocks for models that only return reliable Markdown;
- token-usage extraction and normalized provider errors.

The first supported configuration is an OpenAI-compatible model. LiteLLM model identifiers allow
later Anthropic, Gemini, Bedrock, OpenRouter, or other providers without changing parsers. OpenDocs
still keeps the adapter boundary so LiteLLM calls are mockable and LiteLLM response objects do not
become public API.

No API key, full prompt, source document, base64 image, or raw model response is logged.

## Configuration

Initial defaults are explicit values rather than OpenDocs-specific environment variables:

```python
ParseOptions(
    timeout=900,
    max_pages=300,
    max_output_chars=400_000,
    vision_concurrency=4,
)

VisionConfig(
    model="openai/vision-model",
    api_key=None,
    api_base=None,
    timeout=120,
    max_retries=2,
)
```

LiteLLM's provider environment variables remain usable. Explicit `VisionConfig` fields take
precedence. Later options are added only when callers need to control behavior, not merely because
an internal parser has a tuning constant.

## Errors and Degradation

All fatal exceptions derive from `OpenDocsError` and expose stable `code` and `retryable`
attributes. Initial fatal categories are:

- invalid or missing source;
- unsupported or mismatched file type;
- corrupt or encrypted document;
- file, page, output, or timeout limit exceeded;
- vision configuration required;
- model authentication, permission, or invalid-request failure;
- no usable content.

No vision configuration produces native-only behavior:

- TXT and Markdown parse normally.
- PDF, DOCX, and PPTX return available native Markdown and emit a capturable Python warning for
  skipped visual regions.
- Standalone images and fully scanned PDFs raise `VisionRequiredError` instead of returning an
  empty string.

A failed page or visual region is recoverable when other usable content remains. OpenDocs preserves
successful content, emits a warning, and returns Markdown. It retries only rate limits, timeouts,
and server failures. Authentication, permission, and invalid-request failures are not retried. An
invalid model response receives at most one repair attempt.

## Markdown Contract

Markdown is the only stable result contract in the initial releases.

- Pages/slides appear in source order with deterministic separators.
- Headings use Markdown heading markers when native structure or reliable inference supplies a
  level.
- Tables render from one rectangular grid. Cells are escaped once.
- Images render as concise captions/descriptions without leaking base64 data.
- Model-returned Markdown is normalized through the same renderer instead of being independently
  appended beside structured JSON.
- Output limits truncate only at block boundaries and emit a warning.

The internal JSON/Pydantic representation may change before the Node.js milestone.

## Test Strategy

### Automated layers

1. Unit tests cover source ownership, detection, registry dispatch, options, errors, budgets,
   intermediate validation, and rendering.
2. Contract tests prove `parse()` and `aparse()` match and prove equivalent behavior for paths,
   bytes, and binary streams.
3. Parser fixture tests cover native, scanned, blank, corrupt, table, embedded-image, and ordering
   cases.
4. Vision tests mock `litellm.acompletion()`; ordinary test runs never require credentials or spend
   model tokens.
5. Opt-in integration tests exercise a configured real model.
6. Quality gates run Ruff, ty, pytest, and wheel build checks. CI targets Python 3.11, 3.12, and
   3.13.

### Real acceptance corpus

The following user-provided files are local acceptance assets and must not be copied into Git:

| File | SHA-256 | Observed structure | Acceptance purpose |
| --- | --- | --- | --- |
| `Levabp.pptx` | `caf47c6f95243d3bb8b709db5ff21583d112b8591f233cd0e4ffc435f4e638d2` | 12 slides, 13 media files | source-order and embedded-image coverage |
| `Tacore.aiBP.pdf` | `6a2523ef5a71069d542d1721bcaace4c04eb1d4b06d3425a97c151a92c40bfcc` | 17 pages, almost no native text | full-vision PDF completeness |
| `明星肖像权授权合作协议.docx` | `0fe01f7f7f23a0ae9f4fc2e06c1ac58a6228258a7414d8db9bc322ac03110d34` | 10 pages, no tables/media | paragraph and heading order |
| `外贸商业计划书_BP.pdf` | `c1c42e60865eceb20661b14615e0ca404e8532c4d92c362e01fc324d02ab54d5` | 22 pages, native text present | native/hybrid routing and tables |
| `a7097535a29f73941600af8d818a163a.png` | `1f74d2381f05a41478b9e246d4b31b932fa129a79696c41482a32dfbe6b88c71` | 2108 x 232, 20 columns, 4 body rows | ultra-wide multi-row table extraction |

M0 adds a gitignored local corpus manifest and a committed example manifest containing filenames,
hashes, and expected milestone gates. Corpus tests run only when an explicit `--corpus-dir` is
provided. A hash mismatch fails before parsing so test evidence cannot silently refer to a changed
file.

The real corpus is a development and acceptance gate, not a replacement for small deterministic
fixtures. Release claims require page/slide completeness, ordering, table coverage, and Markdown
checks appropriate to each asset rather than merely asserting a non-empty result.

## Milestones

### M0 - Foundation

- Add Python packaging with a `src` layout and Python 3.11+ metadata.
- Implement `parse()`, `aparse()`, source normalization, detection, registry, typed errors,
  options, private blocks, and Markdown rendering.
- Implement TXT and Markdown end to end.
- Add synthetic fixtures, local corpus manifest support, tests, Ruff, ty, wheel checks, and GitHub
  Actions.
- Add README usage, architecture, contribution, and roadmap documentation.

Exit criteria: a clean install can parse TXT/Markdown from a path, bytes, and `BinaryIO`; sync and
async results match; static checks, tests, and wheel build pass.

### M1 - PDF and Images

- Add Pillow, pdfplumber, Poppler integration, and LiteLLM vision support.
- Implement standalone PNG/JPEG/WebP parsing.
- Implement native, hybrid, full-vision, and blank PDF routes.
- Implement structured table normalization and deterministic merging.
- Activate both PDF files and the ultra-wide PNG as acceptance gates.

Exit criteria: every expected page appears in order; the table image preserves its multi-row header,
20 columns, and 4 body rows; native PDF content does not trigger unnecessary full-page model calls.

### M2 - Office

- Add DOCX and PPTX native extractors.
- Preserve paragraph, table, slide, and shape order.
- Parse embedded images through the existing vision path.
- Activate the DOCX and PPTX files as acceptance gates.

Exit criteria: native text is complete and source ordered, embedded visual work is merged without
reordering native-only output, and all acceptance assets produce deterministic Markdown.

### M3 - Quality and Public Release

- Add a sanitized tuning/holdout benchmark process without committing private corpus files.
- Add resource, concurrency, cancellation, timeout, and dependency-boundary checks.
- Document Poppler/runtime requirements and model-cost controls.
- Complete PyPI packaging, release notes, and an external integration example.

Exit criteria: all local and CI gates pass, the holdout remains separate from tuning, and release
evidence includes both correctness and resource results.

### M4 - Additional Formats and Node.js

- Add Excel, HTML, email, and other formats based on demonstrated demand.
- Decide whether to stabilize a cross-language intermediate schema or duplicate only the public
  Markdown behavior in Node.js.
- Publish a Node.js SDK with matching input semantics, configuration names, error codes, and
  Markdown fixtures.

## Dependency and Provenance Boundaries

- Add dependencies only in the milestone that uses them.
- Poppler remains an explicit system dependency for PDF rendering; absence produces a typed error
  only when a visual PDF route needs it.
- OpenDocs is MIT-licensed. The inspected `43x-agent` checkout has no repository license file.
  Before publishing code copied verbatim from that repository, maintainers must verify redistribution
  rights and add any required notice. Unless that permission is recorded, implementation work adapts
  behavior, contracts, and independently written tests rather than copying source text verbatim.
- Generated outputs, credentials, local manifests, and user corpus files remain untracked.

## Remaining Risks

- OpenAI-compatible services vary in vision and structured-output behavior despite sharing request
  shapes. The adapter needs provider capability tests and Markdown fallback.
- LiteLLM has a broad and changing provider surface. Keeping it behind one module and pinning a
  tested compatible range limits churn.
- PDF routing quality depends on real documents and Poppler availability; synthetic fixtures alone
  cannot establish release readiness.
- Returning only Markdown makes non-fatal diagnostics out-of-band. Python warnings are sufficient
  initially, but structured diagnostics may need a separate opt-in API before Node.js work.
- Private acceptance files cannot run in public CI. Release evidence must clearly separate public CI
  results from local corpus results.

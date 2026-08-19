# OpenDocs

[![PyPI version](https://img.shields.io/pypi/v/opendocs-sdk.svg)](https://pypi.org/project/opendocs-sdk/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dm/opendocs-sdk.svg)](https://pypistats.org/packages/opendocs-sdk)

> Language: **English** | [简体中文](README.zh-CN.md)

OpenDocs is a Python SDK that converts local documents into clean Markdown —
TXT, Markdown, images, PDF (native / hybrid / vision), DOCX, PPTX, and XLSX — through a unified
sync/async API.

> **Package name**: `opendocs-sdk` &nbsp;|&nbsp; **Current Alpha**: `opendocs-sdk==0.2.0` &nbsp;|&nbsp; **Import name**: `opendocs` &nbsp;|&nbsp; **Python**: 3.11+

## Install

```bash
pip install opendocs-sdk
```

If you need to work from a local checkout (for development or unreleased changes):

```bash
uv add ../OpenDocs       # or: pip install ../OpenDocs
```

## Quick start

Synchronous local-path example:

```python
from opendocs import parse

markdown = parse("notes.md")
```

Asynchronous bytes example:

```python
import asyncio

from opendocs import aparse


async def main() -> str:
    markdown = await aparse(b"plain text")
    return markdown


markdown = asyncio.run(main())
```

Accepted inputs:

- local filesystem path as `str`
- local filesystem path as `os.PathLike[str]`
- `bytes`
- binary file object with `read() -> bytes`

Callers own all remote downloads. `http://`, `https://`, `oss://`, and `s3://` sources must be
downloaded before calling OpenDocs.

## Supported formats

| Format | Status | Notes |
| --- | --- | --- |
| TXT | ✅ | Parsed end to end into deterministic Markdown |
| Markdown (`.md`, `.markdown`) | ✅ | Preserved for named Markdown paths/streams; unnamed UTF-8 bytes detect as TXT |
| PDF | ✅ | Per-page native, hybrid, full-vision, or blank routing; source-ordered page boundaries and tables |
| PNG / JPEG / WebP | ✅ | Static images only; sanitized before the configured vision model sees them |
| DOCX | ✅ | Continuous authored body flow with structured text, lists, links, tables, explicit breaks, and inline images |
| PPTX | ✅ | Slide and shape-tree order with text, tables, accessible charts, groups, and inline images |
| XLSX (`.xlsx`) | ✅ | All sheet-like entries in source order, saved values, tables/regions, merges, standard text objects, native chart facts, and optional visual interpretation |

Only standard `.xlsx` workbooks are supported. Legacy `.xls`, macro-enabled `.xlsm`, binary
`.xlsb`, and other spreadsheet formats are not accepted.

## Vision parsing

Image and visual PDF work uses the provider-neutral [LiteLLM](https://github.com/BerriAI/litellm)
adapter when a `VisionConfig` is supplied. Native and blank PDF pages do not call a model.

**Poppler** (`pdftoppm`) is required only for visual/hybrid PDF parsing — install it with your
platform package manager:

```bash
# macOS
brew install poppler

# Debian / Ubuntu
apt-get install poppler-utils
```

Standalone images require `VisionConfig`. PDFs and Office documents without vision configuration
preserve usable native content and emit deterministic warnings for visual regions; XLSX always
keeps its native sheet and chart facts when visual enrichment is unavailable. A document with no
usable native content raises `VisionRequiredError` where that format requires vision.

```python
from opendocs import ParseOptions, VisionConfig, parse

markdown = parse(
    "scan.pdf",
    options=ParseOptions(timeout=300, max_pages=100, vision_concurrency=4),
    vision=VisionConfig(
        model="openai/gpt-4o-mini",
        api_key="...",  # Prefer an environment-backed secret in production.
    ),
)
```

`ParseOptions` controls document timeout, PPTX/PDF page count, output size, and visual concurrency.
Model failures — authentication, permission, invalid request, temporary unavailability, invalid
response — use distinct typed exceptions for precise error handling.

`ParseOptions.vision_concurrency` limits visual requests within one parse. Applications control
cross-document concurrency themselves (e.g. with an `asyncio.Semaphore`); see the
[independent consumer example](examples/basic_consumer/README.md).

### DOCX, PPTX & XLSX details

DOCX extraction preserves authored body paragraphs, headings, lists, safe links, tables, merged
cells, explicit page breaks, and inline raster-image positions. A DOCX remains one continuous
logical flow; `max_pages` does not infer physical Word pages.

PPTX extraction emits every slide boundary and traverses each slide's shape tree in source order,
including recursive groups, text, tables, accessible chart data, and raster pictures. Exact
duplicate embedded images are analyzed once per parse and replayed at every authored slot.

XLSX extraction emits every worksheet and chartsheet in workbook order, including visible, hidden,
very hidden, and empty sheets. It preserves non-empty regions, Excel tables, merged-cell spans,
standard comments/text boxes/links/header-footer text, and common saved display semantics such as
`$`, `€`, `£`, and `¥` currency, grouping, decimals, percentages, dates, and times. Saved formula
caches are preferred; when a cache is missing, the formula text is returned with a warning. OpenDocs
does not recalculate formulas or fetch linked workbooks, data connections, or URLs—the reference is
preserved as text only.

Chart titles, labels, series, categories, and accessible values come from native workbook data.
When vision is configured, normalized chart fact cards and embedded images may add trend, label,
relationship, or meaning interpretation. This enrichment is fail-open and never replaces native
facts. XLSX output does not promise Excel pixel appearance, fonts, colors, borders, dimensions, or
other visual styling fidelity.

### How OpenDocs compares

| Feature | OpenDocs | marker | docling | unstructured | pypdf |
| --- | :---: | :---: | :---: | :---: | :---: |
| PDF → Markdown | ✅ | ✅ | ✅ | ✅ | ❌ |
| DOCX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| PPTX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| XLSX → Markdown | ✅ | ❌ | ✅ | ✅ | N/A |
| LLM vision integration | ✅ | ❌ | ❌ | ❌ | ❌ |
| Sync + Async API | ✅ | ❌ | ❌ | ❌ | ❌ |
| No external service required | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| Pure Python (no system deps) | ✅ | ❌ | ❌ | ❌ | ✅ |
| Typed errors & warnings | ✅ | ❌ | ❌ | ❌ | ❌ |
| Image → Markdown | ✅ | ❌ | ❌ | ✅ | ❌ |
| Provider-neutral vision (LiteLLM) | ✅ | N/A | N/A | ❌ | N/A |

> **Key differentiator**: OpenDocs is the only library that combines native Office/PDF extraction
> with optional LLM-powered visual understanding, all through a clean sync/async API with typed errors.

**Platforms**: Ubuntu and macOS on Python 3.11, 3.12, and 3.13 (Poppler required for visual PDF).
Windows is unverified for `0.2.0`.

**Privacy**: OpenDocs never downloads HTTP, OSS, or S3 URLs. Model calls send sanitized images to
the provider selected by `VisionConfig` — review that provider's privacy and cost terms before
enabling vision.

## Warnings and errors

OpenDocs uses Python warnings for recoverable degradation and typed exceptions for fatal failures.

```python
import warnings

from opendocs import OpenDocsError, OpenDocsWarning, ParseOptions, parse

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always", OpenDocsWarning)
    markdown = parse(
        b"first paragraph\n\nsecond paragraph\n",
        options=ParseOptions(max_output_chars=16),
    )

assert markdown == "first paragraph\n"
assert caught[0].message.code == "output_truncated"

try:
    parse("slides.pdf")
except OpenDocsError as error:
    print(error.code, error.retryable)
```

## Project docs

- [Documentation](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Contributing](CONTRIBUTING.md)
- [License](LICENSE)

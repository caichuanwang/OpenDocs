# OpenDocs

OpenDocs is a Python SDK that converts caller-provided local documents into Markdown. M2 supports
TXT, Markdown, standalone images, native/hybrid/visual PDF parsing, and native DOCX/PPTX extraction
through stable sync/async APIs.

The Python distribution name is `opendocs-sdk`, while the import package remains `opendocs`.
OpenDocs has not been published to PyPI yet; install from a checkout until a release is announced.

## Install from an existing checkout

From a separate consuming project, point the dependency at a local OpenDocs checkout that contains
`pyproject.toml`. The examples below assume your consumer project and the OpenDocs checkout are
sibling directories, so `../OpenDocs` resolves to this checkout. These commands were verified
against a local checkout flow; do not assume the current remote default branch is installable.

```bash
uv add ../OpenDocs
```

```bash
pip install ../OpenDocs
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

## Current behavior

`parse()` and `aparse()` return Markdown strings only. Image and visual PDF work uses the
provider-neutral LiteLLM adapter when a `VisionConfig` is supplied. Native and blank PDF pages do
not call a model or invoke Poppler. Poppler's `pdftoppm` executable is checked lazily only when a PDF
page must be rasterized for hybrid or full-vision parsing.

Install Poppler with your platform package manager before parsing visual PDFs (for example,
`brew install poppler` on macOS or `apt-get install poppler-utils` on Debian/Ubuntu). Pillow,
pdfplumber, LiteLLM, python-docx, and python-pptx are installed as Python dependencies of
`opendocs-sdk`.

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

Standalone images require `VisionConfig`. PDFs and Office documents without vision configuration
preserve usable native content and emit deterministic warnings for visual regions; a document with
no usable native content raises `VisionRequiredError`. Model authentication, permission, invalid
request, temporary unavailability, and invalid response failures use distinct typed exceptions.
`ParseOptions` bounds the document timeout, PPTX/PDF page count, complete-block output size, and
visual concurrency.

DOCX extraction preserves authored body paragraphs, headings, lists, safe links, tables, merged
cells, explicit page breaks, and inline raster-image positions. A DOCX remains one continuous
logical flow; `max_pages` does not infer physical Word pages. PPTX extraction emits every slide
boundary and traverses each slide's shape tree in source order, including recursive groups, text,
tables, accessible chart data, and raster pictures. Exact duplicate embedded images are analyzed
once per parse and replayed at every authored slot.

### Current format matrix

| Format | Status in M2 | Notes |
| --- | --- | --- |
| TXT | Available | Parsed end to end into deterministic Markdown |
| Markdown (`.md`, `.markdown`) | Available | Preserved for named Markdown paths/streams; unnamed UTF-8 bytes intentionally detect as TXT |
| PDF | Available | Per-page native, hybrid, full-vision, or blank routing; source-ordered page boundaries and tables |
| PNG / JPEG / WebP | Available | Static images only; sanitized before the configured vision model sees them |
| DOCX | Available | Continuous authored body flow with structured text, lists, links, tables, explicit breaks, and inline images |
| PPTX | Available | Slide and shape-tree order with text, tables, accessible charts, groups, and inline images |

OpenDocs never downloads HTTP, OSS, or S3 URLs, including Office hyperlink targets and external
relationships. Model calls may send sanitized images to the provider selected by `VisionConfig`;
review that provider's privacy and cost terms before enabling vision.

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

- [Architecture design](docs/superpowers/specs/2026-07-27-opendocs-foundation-and-roadmap-design.md)
- [Roadmap](docs/roadmap.md)
- [Contributing](CONTRIBUTING.md)
- [License](LICENSE)

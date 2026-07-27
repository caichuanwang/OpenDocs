# OpenDocs

OpenDocs is a Python SDK that converts caller-provided local documents into Markdown, and the M0
foundation is complete with stable sync/async APIs plus TXT and Markdown support.

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

## M0 behavior

`parse()` and `aparse()` return Markdown strings only. `VisionConfig` is already part of the public
signature so callers can adopt the stable API now, but it is reserved for M1 and makes no model
call in M0.

### Current format matrix

| Format | Status in M0 | Notes |
| --- | --- | --- |
| TXT | Available | Parsed end to end into deterministic Markdown |
| Markdown (`.md`, `.markdown`) | Available | Preserved as Markdown only for `.md`/`.markdown` local paths or named binary streams; unnamed UTF-8 bytes/streams intentionally detect as TXT |
| PDF | Planned for M1 | Detected now, raises a typed unsupported-format error in M0 |
| PNG / JPEG / WebP | Planned for M1 | Caller still provides local files; vision path lands in M1 |
| DOCX | Planned for M2 | Detected now, raises a typed unsupported-format error in M0 |
| PPTX | Planned for M2 | Detected now, raises a typed unsupported-format error in M0 |

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

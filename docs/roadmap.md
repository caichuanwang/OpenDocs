# OpenDocs Roadmap

The accepted milestone design lives in
[OpenDocs Foundation and Roadmap Design](archive/m0/2026-07-27-opendocs-foundation-and-roadmap-design.md).
This roadmap summarizes the delivery sequence and exit criteria without repeating the internal
architecture algorithms from that design.

## M0 - Foundation

Summary:

- package the SDK for Python 3.11+
- ship `parse()` and `aparse()` with stable options, errors, detection, registry, and rendering
- support TXT from all accepted inputs, and preserve Markdown for `.md`/`.markdown` local paths or
  named binary streams while unnamed UTF-8 bytes/streams intentionally detect as TXT
- add tests, Ruff, ty, wheel checks, corpus manifest support, README, CONTRIBUTING, and CI

Exit criteria:

- a clean install parses TXT from every accepted input shape and preserves Markdown for
  `.md`/`.markdown` local paths or named binary streams
- sync and async calls produce identical Markdown for equivalent inputs
- static checks, tests, and wheel build pass

## M1 - PDF and Images

Status: implemented; public verification and opt-in private replay/live gates are available.

Implementation architecture:
[M1 PDF and Images Architecture Plan](archive/m1/2026-07-28-m1-pdf-images-architecture.md).

Summary:

- add Pillow, pdfplumber, Poppler, and LiteLLM-backed vision integration
- support standalone PNG, JPEG, and WebP parsing
- support native, hybrid, full-vision, and blank PDF routing
- activate both PDF files and the ultra-wide PNG through hash-first, opt-in replay/live acceptance
  gates

Exit criteria:

- every expected PDF page appears in order
- the table image preserves its multi-row header, 20 columns, and 4 body rows
- native PDF content avoids unnecessary full-page model calls

## M2 - Office

Status: implementation complete; public verification is available, and milestone acceptance is
pending a maintainer-approved local checklist.

Implementation architecture:
[M2 Office Architecture and Implementation Plan](archive/m2/2026-07-29-m2-office-architecture.md).

Detailed execution plan:
[M2 Office Detailed Implementation Plan](archive/m2/2026-07-29-m2-office-implementation.md).

Summary:

- ship DOCX and PPTX native extractors as core formats
- preserve paragraph, table, slide, and shape order
- merge embedded-image vision output through the existing vision path
- provide an opt-in, hash-first gate for the DOCX and PPTX acceptance assets

Exit criteria:

- native text is complete and source ordered
- embedded visual output merges without reordering native-only output
- all acceptance assets produce deterministic Markdown

## M3 - Quality and Public Release

Status: accepted plan; implementation not started.

Release architecture:
[M3 Alpha Release Architecture](plans/2026-07-31-m3-alpha-release-architecture.md).

Detailed execution plan:
[M3 Alpha Release Detailed Implementation Plan](plans/2026-07-31-m3-alpha-release-implementation.md).

Summary:

- close the maintainer-approved M2 acceptance gate before release
- add a 30/30 PDF/image tuning and holdout process plus one unseen real DOCX and PPTX holdout
- expand cancellation, timeout, per-parse concurrency, dependency-boundary, and resource evidence
- verify CPython 3.11-3.13 on Ubuntu and macOS with Poppler
- publish `opendocs-sdk==0.1.0` as the first public Alpha through Trusted Publishing
- provide release notes, safe aggregate evidence, and an independent PyPI consumer example

Exit criteria:

- M2 approval and all public, tuning, independent holdout, and resource gates pass
- tuning and holdout remain source-isolated; inspected holdout is replaced before release
- wheel and source distribution pass isolated installation checks on Ubuntu and macOS
- public PyPI installation of `opendocs-sdk==0.1.0` passes on both supported operating systems
- GitHub tag and Release `v0.1.0` identify the exact published source and artifacts

Explicit exclusions:

- no formal Windows support, new formats, Node.js SDK, CLI, service, or public JSON schema
- no dependency-extras redesign or global cross-document concurrency control
- no model-call/token/currency limit and no public performance SLA

## M4 - Additional Formats and Node.js

Summary:

- add more formats based on demonstrated demand, such as Excel, HTML, or email
- decide whether to stabilize a cross-language intermediate schema
- publish a Node.js SDK with matching input semantics, configuration names, error codes, and
  Markdown fixtures

Exit criteria:

- new formats are prioritized by demonstrated demand rather than speculation
- the Node.js SDK matches the Python SDK's public contract for supported milestones

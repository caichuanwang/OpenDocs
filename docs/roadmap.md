# OpenDocs Roadmap

The accepted milestone design lives in
[OpenDocs Foundation and Roadmap Design](superpowers/specs/2026-07-27-opendocs-foundation-and-roadmap-design.md).
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

Summary:
- add Pillow, pdfplumber, Poppler, and LiteLLM-backed vision integration
- support standalone PNG, JPEG, and WebP parsing
- support native, hybrid, full-vision, and blank PDF routing
- activate both PDF files and the ultra-wide PNG as acceptance gates

Exit criteria:
- every expected PDF page appears in order
- the table image preserves its multi-row header, 20 columns, and 4 body rows
- native PDF content avoids unnecessary full-page model calls

## M2 - Office

Summary:
- add DOCX and PPTX native extractors
- preserve paragraph, table, slide, and shape order
- merge embedded-image vision output through the existing vision path
- activate the DOCX and PPTX acceptance assets

Exit criteria:
- native text is complete and source ordered
- embedded visual output merges without reordering native-only output
- all acceptance assets produce deterministic Markdown

## M3 - Quality and Public Release

Summary:
- add sanitized benchmark and holdout process
- expand cancellation, timeout, concurrency, dependency-boundary, and resource checks
- document Poppler/runtime requirements and model-cost controls
- finish PyPI packaging, release notes, and an external integration example

Exit criteria:
- all local and CI gates pass
- the holdout remains separate from tuning
- release evidence covers correctness and resource behavior

## M4 - Additional Formats and Node.js

Summary:
- add more formats based on demonstrated demand, such as Excel, HTML, or email
- decide whether to stabilize a cross-language intermediate schema
- publish a Node.js SDK with matching input semantics, configuration names, error codes, and
  Markdown fixtures

Exit criteria:
- new formats are prioritized by demonstrated demand rather than speculation
- the Node.js SDK matches the Python SDK's public contract for supported milestones

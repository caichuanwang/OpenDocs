# M2 Office Architecture and Implementation Plan

Status: implementation complete; milestone acceptance pending maintainer approval
Date: 2026-07-29
Scope: native DOCX/PPTX extraction, source-order Markdown, and embedded-image vision reuse

## Objective

M2 makes DOCX and PPTX core OpenDocs formats without changing the public parsing API. A default
installation must extract the common authored body of both formats, preserve its deterministic
source order, and place embedded-image vision results back at the images' authored positions.

The implementation uses the local `43x-agent` checkout at
`/Users/caichuanwang/Documents/liao/43x/43x-agent` as the primary behavioral reference. In
particular, trace its DOCX/PPTX extractors, Office parser, shared Office models, and tests before
implementing each slice. Reimplement the behavior within OpenDocs contracts; do not copy private
documents, application dependencies, prompts, model payloads, generated output, or source text
whose redistribution has not been approved.

## Accepted Product Contract

M2 supports the following common-body semantics.

DOCX:

- body paragraphs and authored heading levels;
- bullet and ordered lists, including nesting and numbering restarts;
- hyperlink labels and safe targets;
- top-level tables, explicit header rows, and horizontal/vertical merged cells;
- embedded raster images in their exact paragraph/run positions;
- explicit hard page breaks as structural boundaries;
- continuous document flow rather than inferred physical pages.

PPTX:

- every slide in deck order, including blank slides as structural boundaries;
- text frames, paragraphs, soft breaks, lists, and hyperlinks;
- placeholder title/subtitle levels and the upstream largest-text title fallback;
- simple and merged-cell tables;
- chart titles and accessible underlying category/series data;
- group shapes, recursively traversed in child source order;
- embedded raster pictures at their source shape positions.

The renderer preserves semantic structure, not Office appearance. M2 does not preserve fonts,
colors, alignment, borders, backgrounds, spacing, animations, transitions, or run-level bold,
italic, and underline styling.

## Explicit Non-Goals

The following are outside M2 and must be skipped with stable warnings when they contain potentially
meaningful content:

- legacy binary `.doc` and `.ppt` formats;
- DOCX headers, footers, footnotes, endnotes, comments, tracked changes, equations, text boxes,
  embedded objects, and full nested-table topology;
- PPTX speaker notes, SmartArt semantics, OLE objects, audio/video, animation, and transition data;
- visual reflow, Office-compatible physical pagination, or pixel-equivalent rendering;
- fetching external images, linked media, remote documents, or hyperlink targets;
- new public parse options, structured-result APIs, or user-configurable Office thresholds.

If a library exposes an otherwise ordinary hidden slide through the presentation's slide sequence,
parse it in that sequence. M2 does not promise to preserve or expose the hidden-state flag.

## Compatibility Invariants

1. `parse()`, `aparse()`, `ParseOptions`, `VisionConfig`, and the public error hierarchy do not
   change.
2. TXT, Markdown, PDF, and standalone-image Markdown and error behavior remain byte-for-byte
   compatible with M1.
3. `source.py` remains the sole owner of input normalization, temporary sources, and the parse
   workspace.
4. Detection does not import Office parser dependencies. Registry construction remains the only
   format-to-parser binding point.
5. Native Office extraction runs in the existing spawned per-parse native worker. No cancelled
   thread or process may retain access to the source after the API returns.
6. Extractors return validated OpenDocs-owned data. They do not emit Markdown.
7. `markdown.py` remains the sole user-facing Markdown renderer and applies the output budget only
   between complete logical blocks.
8. Native and visual work may complete concurrently, but emitted blocks and warnings are determined
   only by source positions.
9. A caller-owned path is never modified or deleted. Extracted media lives only below the owned
   parse workspace and is removed after all parser resources close.
10. Private corpus files, completed acceptance checklists, content anchors, model payloads, and
    generated outputs never enter Git.

## Dependencies and Installation

Add `python-docx>=1.1.2,<2` and `python-pptx>=1.0.2,<2` as core runtime dependencies and lock tested
versions through uv. They are not an optional extra. Do not add LibreOffice, Microsoft Office, or a
new system dependency.

Use the libraries' public APIs where they express the required semantics. Targeted access to their
OOXML element wrappers is allowed for authored order, numbering, hyperlinks, page breaks, merged
cells, and metadata that the public APIs do not expose. Keep such access inside the Office extractor
modules and cover it with focused compatibility tests.

Import Office libraries only from Office-native modules. If a core dependency is missing or broken
at runtime, map that installation failure to `RuntimeDependencyError`; do not mislabel it as an
unsupported document.

## Target Module Layout

```text
src/opendocs/
|-- _models.py                    # add semantic text/list/span-table blocks
|-- markdown.py                   # render semantic blocks and merged tables
|-- parsers/
|   |-- image.py                  # expose private reusable image sanitization seam
|   |-- office/
|   |   |-- __init__.py
|   |   |-- models.py             # Office slots/pages plus strict wire conversion
|   |   |-- package.py            # OOXML ZIP preflight and media artifact policy
|   |   |-- docx.py               # DOCX body extractor
|   |   |-- pptx.py               # PPTX slide/shape extractor
|   |   |-- merge.py              # slot-preserving native/vision merge
|   |   `-- parser.py             # orchestration and failure semantics
|   `-- registry.py               # register DOCX and PPTX after focused gates pass
`-- vision/                       # reuse the M1 client, prompts, and FIFO dispatcher
```

Keep the Office package private. The only public behavior change is that detected DOCX/PPTX inputs
become parseable by the existing API.

## Private Semantic Blocks

Extend the private renderer model rather than letting extractors create `MarkdownBlock` values.
Keep all M1 block types unchanged.

- `InlineText(text)` contains authored text and soft line breaks.
- `InlineLink(label, target)` contains a non-empty label and a validated safe target.
- `ParagraphBlock(inlines)` represents one Office paragraph or a paragraph fragment split by an
  inline picture.
- `HeadingBlock(level, inlines)` represents authored DOCX headings and PPTX title inference, with
  levels clamped to Markdown levels 1 through 6.
- `ListItemBlock(list_id, level, kind, ordinal, inlines)` represents one complete list item. `kind`
  is bullet or ordered; `list_id` and `ordinal` preserve grouping, restarts, and deterministic
  numbering without exposing OOXML identifiers.
- `HardPageBreakBlock` represents an explicit DOCX page break and renders as
  `<!-- page-break -->`; it never claims a physical page number.
- `SpannedTableCell(row, column, row_span, column_span, text)` represents one merge-origin cell.
- `SpannedTableBlock(row_count, column_count, cells, header_rows)` represents a validated occupied
  grid. Covered coordinates cannot contain another origin cell.

The renderer coalesces consecutive compatible `ListItemBlock` values while still applying the
output budget at complete item boundaries. It escapes inline labels and targets exactly once. Allow
only `http`, `https`, `mailto`, and local fragment targets in emitted Markdown links. For any other
scheme, render the label as plain text and add a stable warning; never open or resolve the target.

Render `SpannedTableBlock` as deterministic HTML with `rowspan`/`colspan`. Continue rendering simple
rectangular Office tables through the existing `TableBlock` path, so M1 table behavior is unchanged.
Flatten a nested DOCX table into its containing cell's text in source order, separated by explicit
line breaks, and emit one stable flattening warning for that table.

## Office Extraction Wire Model

The native worker must return small validated wire values, not library objects or large media blobs.
Define tagged Office records with strict `to_wire`/`from_wire` functions modeled after the PDF wire
boundary:

- `OfficeDocument(document_type, pages, warnings)`;
- `OfficePage(page_number, slots)`;
- `NativeSlot(source_index, blocks)`;
- `ImageSlot(source_index, artifact_name, content_sha256, bbox, alt_text)`;
- `BreakSlot(source_index)` for explicit DOCX hard page boundaries.

`artifact_name` is a portable workspace basename produced through `ParseWorkspace.output_path()`.
The main process resolves and verifies that path remains directly below the owned workspace. Do not
send embedded bytes through the 8 MiB inline worker protocol.

One source item may yield adjacent blocks, for example a chart title followed by a data table. Those
blocks remain atomic with respect to the surrounding slot and retain their internal order.

## Secure OOXML Preflight

Before `python-docx` or `python-pptx` opens the package, inspect the ZIP central directory inside
the native worker. Reject with a typed error before decompressing content when any of these named
private budgets is exceeded:

- archive member count;
- total declared uncompressed bytes;
- one XML part's uncompressed bytes;
- one media part's uncompressed bytes;
- aggregate media bytes;
- embedded-media count;
- suspicious compression ratio;
- duplicate, absolute, parent-traversing, encrypted, or otherwise unsafe member names.

Validate required OOXML parts and relationships before extraction. External relationships may be
retained only as safe hyperlink text targets; external media and OLE relationships are never
fetched. Corrupt, encrypted, or structurally inconsistent packages raise `CorruptDocumentError`.
True hard-budget exhaustion raises `LimitExceededError`.

Name and document the private constants, calibrate concrete values against synthetic stress cases
and the two local M2 assets, and lock the selected values with boundary tests before registering the
parsers. Do not add public tuning options in M2.

## DOCX Extraction

Treat a DOCX as one continuous logical document. Do not infer physical pages.

1. Iterate body children in authored XML order.
2. For each paragraph, walk runs, hyperlinks, breaks, and drawings in child order rather than using
   only `paragraph.text`.
3. Resolve heading levels from direct outline metadata, inherited styles, and the upstream
   English/Chinese heading-name fallback.
4. Resolve lists through paragraph numbering properties, numbering definitions, abstract numbering,
   level metadata, and start overrides. Preserve nesting and restarts; map visual numbering styles
   to bullet or ordered Markdown semantics.
5. Preserve safe hyperlink labels/targets. Keep labels as plain text with a warning when a target is
   unsafe or malformed.
6. Split a paragraph around inline pictures so the image slot occupies its actual run position.
7. Convert simple top-level tables to `TableBlock`. Detect `gridSpan`/vertical merge topology and
   create `SpannedTableBlock` when necessary. Derive header rows only from explicit Word table-row
   properties.
8. Flatten nested table content into its parent cell without losing text and emit the agreed
   warning.
9. Convert explicit manual page breaks and page-start section breaks into `BreakSlot` values.
10. Skip unsupported body constructs with deterministic warnings containing only feature code and
    source position, never private content.

Empty paragraphs without structural meaning do not become blocks. Authored soft breaks inside a
paragraph remain line breaks. Normalize line endings but do not collapse meaningful internal
whitespace.

## PPTX Extraction

Emit one `OfficePage` per slide and prepend one existing `PageBreakBlock(slide_number)` during
merge. A blank slide contributes its page boundary and a stable warning when the deck contains
other usable content; an all-blank deck raises `NoUsableContentError`.

1. Iterate slides in presentation order.
2. Pre-scan text shapes for heading levels: title placeholders are level 1, subtitle placeholders
   are level 2, and only when no primary title exists use the upstream largest non-numeric,
   reasonably bounded freeform text as level 1.
3. Walk each slide's shape tree in source order. Recurse into group shapes depth first in child
   source order. Do not sort by top/left coordinates or task completion time.
4. Preserve text-frame paragraph order, soft breaks, list levels/restarts, and safe hyperlinks.
5. Convert simple tables to `TableBlock`; use `SpannedTableBlock` for merge origins/spans. Set
   header rows only when the presentation explicitly marks the first row.
6. For charts with accessible categories and series, emit an optional title block followed by a
   table whose first column identifies the series. A chart with only a title emits that title. A
   chart with inaccessible meaningful data emits a warning rather than invented content.
7. Emit pictures as image slots at their shape positions. Backgrounds, linked media, SmartArt, OLE,
   audio, and video are not rasterized or fetched.
8. Normalize EMU geometry to `BBox` only for traceability and diagnostics. Geometry never overrides
   shape-tree ordering in M2.

## Embedded-Image Pipeline

Reuse the M1 image decoding, sanitization, pixel budgets, vision request types, prompt, adapter, and
FIFO dispatcher. Refactor only the minimum private seam needed to sanitize an extracted workspace
artifact without applying a caller filename/type mismatch check. Lock standalone-image behavior with
regression tests before and after that refactor.

The Office parser performs these stages:

1. Collect image slots in document source order.
2. Group exact duplicate embedded media by SHA-256. The first occurrence owns one sanitization and
   model call; later occurrences reuse the validated result.
3. Do not discard an image based on dimensions or a decorative-image heuristic. Small stamps,
   signatures, and QR codes may be meaningful.
4. Submit first occurrences to the existing FIFO vision dispatcher in source order, bounded by
   `ParseOptions.vision_concurrency` and the global document deadline.
5. Store outcomes by media hash, then replay each outcome into every matching image slot.
6. Within one vision result, retain validated provider `source_index` order. The entire result stays
   at the image's source slot.
7. Apply private image-count, aggregate-byte, and pixel budgets before model admission. When a
   visual budget is exhausted, retain usable native content with stable warnings; if no usable
   content can remain, raise `LimitExceededError`.

Never append all visual output to the end of a document or slide. Never geometrically sort a mixed
native/visual page. Both behaviors would violate the accepted source-order contract.

## Failure and Degradation Semantics

Match the existing PDF behavior.

- Without `VisionConfig`, an Office document with usable native content returns that content and
  deterministic `vision_unavailable_native_only` warnings for skipped image slots.
- Without `VisionConfig`, a document whose only usable content is embedded images raises
  `VisionRequiredError`.
- A recoverable image decode/model failure retains other native or successful visual content and
  emits a warning at the original slot.
- If all usable content depends on failed visual work, raise the most specific typed decode/model
  error rather than returning empty Markdown.
- Authentication, permission, and invalid-request failures are fatal configuration errors even when
  native content exists, matching PDF.
- A single unsupported or malformed element may degrade with a warning when other usable content
  remains. A corrupt package, broken required relationship graph, or failed core extraction is
  fatal.
- Structural page/break comments do not count as semantic content. An otherwise empty Office
  document raises `NoUsableContentError`.
- Global timeout/cancellation stops new native and model admission, reaps the native worker, closes
  model work, and removes workspace artifacts before returning according to the existing API cleanup
  contract.

Add only stable warning codes required by this matrix. Aggregate repeated unsupported-feature
warnings by code and source location so a document cannot create unbounded public warning spam.

## Determinism Contract

Determinism has three separately testable meanings:

1. Native-only parsing of one input is byte-for-byte identical across repeated runs.
2. With fixed replay vision results, request admission, slot merge, warnings, and final Markdown are
   byte-for-byte identical.
3. Live-model acceptance validates structural completeness, ordering, and call budgets, but does not
   require identical model wording.

No task-completion time, hash-map iteration order, relationship enumeration accident, or geometry
tie may affect output. Sort only where the source format lacks a meaningful order, and then use an
explicit stable source index as the final key.

## Implementation Slices

Each slice starts with failing regression/contract tests, makes the smallest production change, runs
focused tests, and then runs the complete public gate before the next slice.

1. **Contract tests and dependencies**
   - Add locked core `python-docx`/`python-pptx` dependencies and wheel-import smoke tests.
   - Add unsupported legacy-format and zero-M0/M1-regression assertions.
2. **Semantic IR and renderer**
   - Add inline text/link, paragraph, heading, list-item, hard-break, and spanned-table models.
   - Add validation, escaping, list coalescing, merged HTML table rendering, and output-boundary
     tests without registering Office parsers.
3. **OOXML preflight and wire boundary**
   - Add archive budgets, required-part validation, safe artifact naming, Office wire round trips,
     and oversized/path-traversal/encrypted/corrupt package tests.
4. **DOCX native extractor**
   - Implement body/run order, headings, lists, hyperlinks, images, hard breaks,
     simple/merged/nested tables, warnings, and deterministic synthetic fixtures.
5. **PPTX native extractor**
   - Implement slide/shape/group order, title inference, paragraphs/lists/links, tables, charts,
     pictures, blank slides, warnings, and deterministic synthetic fixtures.
6. **Embedded-image vision and merge**
   - Reuse the M1 sanitizer/client, add exact-media deduplication, source-slot replay, bounded
     concurrency, failure degradation, and exact admission/merge trace tests.
7. **Runtime, registry, and API integration**
   - Register DOCX/PPTX only after focused suites pass.
   - Cover path/bytes/BinaryIO, sync/async equivalence, max pages for PPTX, output limits, timeout,
     external cancellation, worker reaping, and workspace cleanup.
8. **Private M2 acceptance**
   - Add a strict local checklist schema/validator, candidate-generation helper, replay client, live
     gate, and human-approval enforcement.
9. **Documentation and release evidence**
   - Update format matrix, warning/fallback behavior, core dependencies, privacy/cost notes,
     commands, and roadmap status. Run all public and private gates and record exact evidence.

M2 is not complete after only one format lands. Incremental pull requests may merge, but the
milestone status changes to implemented only after both DOCX and PPTX pass the full exit criteria.

## Public Verification Matrix

Public CI uses only synthetic Office files and fake vision clients. It never needs credentials,
network access, Office/LibreOffice, or private corpus files.

- model and renderer validation for every new semantic block and malformed span grid;
- exact Markdown for headings, escaped plain text, safe/unsafe links, nested/restarted lists, hard
  page breaks, simple tables, merged rows/columns, nested-table flattening, and output truncation;
- DOCX paragraphs/tables/images interleaved inside one body and paragraph, inherited heading styles,
  numbering definitions/start overrides, hyperlink relationships, explicit breaks, and warnings;
- PPTX multi-slide order, blank slides, source-shape order that differs from screen coordinates,
  nested groups, placeholder/freeform titles, soft breaks, lists, links, simple/merged tables,
  charts, pictures, and unsupported shapes;
- duplicate image hashes causing one model call while output appears at every authored position;
- slow/failed early visual calls and fast later calls still producing exact source-slot order;
- missing vision, partial visual failure, all-visual failure, and fatal model configuration errors;
- ZIP member/count/size/ratio/path/encryption limits before Office-library extraction;
- worker protocol frame limits and workspace-path containment for extracted media;
- path/bytes/BinaryIO and sync/async equivalence;
- timeout/cancellation proof that the worker and model tasks stop before owned-source cleanup;
- exact M0/M1 regression suite, Ruff, formatting, ty, build, and isolated-wheel smoke test.

## Private Acceptance Checklist and Gates

Add `tests/test_m2_acceptance.py` and local-only options such as `--m2-checklist-dir`,
`--m2-replay-dir`, and `--m2-live`. The gate selects exactly the two `milestone = "M2"` entries from
the public corpus manifest and atomically verifies both filenames and source hashes before parsing
either file.

The ignored checklist records no raw content, but may contain source-bound local evidence:

- source asset SHA-256 and checklist schema/extractor version;
- maintainer-reviewed approval state that tools cannot set automatically;
- DOCX ordered anchor hashes, heading levels, table dimensions/spans, image positions/counts, and
  expected warning codes;
- PPTX slide count plus per-slide anchor hashes, table/chart/image counts, and expected warnings;
- replay request count/order and final Markdown SHA-256;
- live structural coverage and maximum expected model calls.

A helper may generate a checklist candidate, but must mark it unapproved. A maintainer compares the
candidate with the source document and explicitly approves the baseline. Once approved, all
regression checks are automatic. A mismatch fails; no command used by the test gate may regenerate
or approve expectations merely to make the failure pass.

Commit only the checklist schema, validator, and synthetic examples. Keep the completed checklist,
content anchors, replay results, model responses, and credentials under an explicitly ignored local
directory such as `tests/m2-acceptance.local/`.

Required local evidence:

```bash
uv run --frozen pytest tests/test_m2_acceptance.py -q \
  --corpus-dir=@local \
  --m2-checklist-dir=tests/m2-acceptance.local \
  --m2-replay-dir=tests/m2-replay.local

OPENDOCS_VISION_MODEL=<provider/model> \
uv run --frozen pytest tests/test_m2_acceptance.py -q \
  --corpus-dir=@local \
  --m2-checklist-dir=tests/m2-acceptance.local \
  --m2-live
```

The replay gate requires exact Markdown and warning equality across repeated runs. The live gate
asserts ordered coverage, table/chart/image structure, and call budgets while allowing model wording
to vary.

## Milestone Exit Criteria

M2 is complete only when all of the following are true:

- a default wheel installation parses DOCX and PPTX without an extra or system Office dependency;
- the accepted common-body features are complete and source ordered for path, bytes, and BinaryIO;
- DOCX does not claim physical pagination; explicit hard breaks remain observable;
- every PPTX slide has an ordered page boundary and shape-tree content remains in source order;
- embedded-image output appears at each authored image slot, with exact duplicates issuing one model
  call per document;
- native/no-vision, replay, live, partial-failure, and fatal-failure behavior matches this plan;
- native and replay output is deterministic, and the approved local checklist passes for both M2
  assets;
- all public tests and exact M0/M1 regressions pass;
- `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .`,
  `uv run --frozen ty check src tests`, and `uv build` pass;
- the built wheel passes an isolated import and DOCX/PPTX native parse smoke test;
- README, CONTRIBUTING, roadmap, dependency/privacy notes, and warning/error behavior match reality.

## Architecture Decision Gates Before Coding

Implementation proceeds with the defaults above. Escalate only if evidence forces one of these
material changes:

1. A required common-body feature cannot be obtained reliably through the selected Office libraries
   plus bounded OOXML access; document the failing fixture before proposing another dependency.
2. Merged-cell topology cannot be represented without changing existing `TableBlock`; add the new
   spanned block as planned rather than mutating M1 table semantics.
3. Private-corpus calibration exceeds a proposed ZIP/media limit; record memory/time evidence and
   adjust the named private constant without widening public API.
4. Shape-tree order demonstrably loses authored content; fix traversal while preserving source
   order, not by adding a geometry-based global reorder.
5. An upstream behavior conflicts with OpenDocs timeout, error, privacy, or provenance contracts;
   keep the OpenDocs contract and record the upstream behavior as rejected.

## References

- `docs/superpowers/specs/2026-07-27-opendocs-foundation-and-roadmap-design.md`
- `docs/roadmap.md`
- `docs/plans/2026-07-28-m1-pdf-images-architecture.md`
- local `43x-agent` DOCX/PPTX extractors, Office parser/common models, and focused tests
- python-docx documentation and OOXML element APIs used by its object model
- python-pptx documentation and OOXML element APIs used by its object model

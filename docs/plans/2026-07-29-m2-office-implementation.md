# M2 Office Detailed Implementation Plan

Status: public implementation and acceptance infrastructure complete; private approval pending
Date: 2026-07-29
Architecture: `docs/plans/2026-07-29-m2-office-architecture.md`

Progress verified on 2026-07-31:

- T00 through T09 are implemented and covered by the public suite.
- T10's strict checklist, candidate, replay, live-gate, and pytest option infrastructure is
  implemented.
- AC-18 and the live portion of AC-16 remain pending because the ignored maintainer-approved
  checklist/replay directories are not present locally and no live provider run has been approved.

## Requirements Summary

This plan implements the accepted M2 architecture without reopening product scope.

- DOCX and PPTX become core formats using the existing `parse()`/`aparse()` path at
  `src/opendocs/api.py:98`; no new public entry point or option is added.
- Add `python-docx` and `python-pptx` beside the current core dependencies at
  `pyproject.toml:21`; do not add Office/LibreOffice or a system runtime.
- Preserve M0/M1 blocks and outputs while extending the private block union at
  `src/opendocs/_models.py:33` and renderer dispatch at `src/opendocs/markdown.py:79`.
- Execute Office-library work through `ParserRuntime.run_native()` at
  `src/opendocs/_runtime.py:278`; large media crosses the process boundary as workspace paths, not
  inline bytes.
- Reuse `ParseWorkspace.output_path()` at `src/opendocs/source.py:40`, image sanitization at
  `src/opendocs/parsers/image.py:43`, and the FIFO dispatcher at
  `src/opendocs/vision/base.py:180`.
- Register parsers only after focused tests pass, extending the existing registry seam at
  `src/opendocs/parsers/registry.py:52`.
- Mirror the M1 private-gate shape at `tests/test_m1_acceptance.py:46` and CLI option pattern at
  `tests/conftest.py:9`, while requiring the human-approved M2 checklist specified in
  `AGENTS.md:58`.
- Use the local `43x-agent` Office extractors, parser, common models, and tests as the behavioral
  reference, but independently implement within OpenDocs privacy, error, and lifetime contracts.

## Scope Boundaries

Included: DOCX body paragraphs, headings, lists, safe links, explicit page breaks, top-level
tables, merged cells, nested-table text flattening, and inline raster images; PPTX slides,
shape-tree text, lists, safe links, titles, groups, tables, chart data, and raster pictures.

Excluded: physical DOCX pagination, run-level styling, headers/footers/notes/comments/revisions,
SmartArt semantics, OLE/audio/video, external media fetching, legacy `.doc`/`.ppt`, and new public
configuration. Unsupported meaningful constructs produce bounded stable warnings.

## Delivery Graph

```text
T00 Baseline
 |
 +--> T01 Dependencies ---------------------+
 |                                         |
 +--> T02 Semantic IR + renderer --------+  |
                                      |   |  |
                         +------------+---+--+
                                      |
                                      v
                              T03 OOXML preflight
                                + worker wire
                               /      |       \
                              v       v        v
                      T04 DOCX     T05 PPTX    T06 Shared
                      extractor    extractor   image seam
                              \       |       /
                               +------^------+
                                      |
                                      v
                      T07 Office merge + orchestration
                                      |
                                      v
                          T08 Registry/API/runtime
                                      |
                                      v
                         T09 Public M2 integration
                                      |
                                      v
                        T10 Private acceptance/docs
```

T04, T05, and T06 may run in the same wave after T03, but only T04/T05 are independent parser
lanes. All arrows are hard dependencies. Keep shared files (`_models.py`, `markdown.py`, `image.py`,
`registry.py`, `conftest.py`) single-owner within an execution wave.

## Acceptance Criteria

Each criterion has a direct automated proof.

- **AC-01:** Default installation imports both Office dependencies. Prove with the built-wheel
  smoke test in `tests/test_package.py`.
- **AC-02:** Public API and existing format behavior stay unchanged. Prove with existing package,
  API, and M0/M1 suites.
- **AC-03:** DOCX works for path, bytes, and named/unnamed `BinaryIO`. Prove with parameterized
  `tests/test_api_m2.py` cases.
- **AC-04:** PPTX works for the same input shapes. Prove with parameterized
  `tests/test_api_m2.py` cases.
- **AC-05:** Equivalent sync/async calls return identical Markdown. Prove in
  `tests/test_api_m2.py`.
- **AC-06:** DOCX body/run/image/table/break order is exact. Prove with synthetic
  `tests/test_docx_extract.py` fixtures.
- **AC-07:** DOCX headings, nested/restarted lists, and safe links render exactly. Prove with
  extractor and renderer snapshots.
- **AC-08:** Simple, merged, and flattened nested tables preserve all accepted text. Prove with
  DOCX/PPTX table tests.
- **AC-09:** PPTX slides and depth-first shape-tree content remain source ordered. Prove with
  synthetic `tests/test_pptx_extract.py` fixtures.
- **AC-10:** PPTX title inference and accessible chart data match the contract. Prove with focused
  title/chart fixtures.
- **AC-11:** Duplicate image bytes make one model call but render at every slot. Prove with the fake
  client trace in `tests/test_office_parser.py`.
- **AC-12:** Missing/failing vision behavior matches PDF. Prove with the error/warning matrix in
  `tests/test_office_parser.py`.
- **AC-13:** Unsafe ZIP packages fail before Office-library extraction. Prove with spy-backed
  `tests/test_office_package.py` cases.
- **AC-14:** Timeout/cancellation reaps worker/model work before cleanup. Prove in
  `tests/test_runtime.py` and `tests/test_api_m2.py`.
- **AC-15:** Native and replay runs are byte-for-byte deterministic. Prove with repeated public and
  private gate runs.
- **AC-16:** Live vision verifies structure, not exact model wording. Prove with opt-in
  `tests/test_m2_acceptance.py` mode.
- **AC-17:** A checklist cannot self-approve or regenerate on failure. Prove with checklist
  validator/generator tests.
- **AC-18:** Both private M2 assets pass one atomically verified gate. Prove with the approved local
  checklist and replay command.
- **AC-19:** All public quality commands and build pass. Prove with the final verification sequence.

## T00 - Capture the M1 Baseline

Purpose: prevent Office work from silently changing shipped behavior.

Files:

- Read `tests/test_api.py`, `tests/test_markdown.py`, `tests/test_image_parser.py`,
  `tests/test_pdf_parser.py`, and `tests/test_package.py`.
- Add regression cases only where an accepted M0/M1 behavior is not already exact.

Steps:

1. Record the current test count and expected M1 skips.
2. Confirm representative TXT, Markdown, image, native PDF, and visual PDF outputs have exact
   assertions rather than only non-empty checks.
3. Add missing exact assertions before any shared model/renderer/image edit.
4. Do not change production code in this task.

Verification:

```bash
uv run --frozen pytest tests/test_api.py tests/test_markdown.py \
  tests/test_image_parser.py tests/test_pdf_parser.py tests/test_package.py -q
uv run --frozen pytest -q
```

Exit: the pre-M2 behavior is locked and the full public suite is green.

## T01 - Add Core Office Dependencies

Depends on: T00.

Files:

- Modify `pyproject.toml:21`.
- Regenerate `uv.lock` mechanically with uv.
- Extend `tests/test_package.py` for built-wheel imports.

Test first:

1. Add a wheel smoke assertion that `docx` and `pptx` import from an isolated built wheel.
2. Assert the OpenDocs public `__all__` remains unchanged.

Implement:

1. Add `python-docx>=1.1.2,<2` and `python-pptx>=1.0.2,<2` to core dependencies.
2. Run `uv lock`, then `uv sync --all-groups --frozen`.
3. Do not register Office parsers or add optional extras in this task.

Verification:

```bash
uv run --frozen pytest tests/test_package.py -q
uv build
```

Exit: the lock is reproducible and the built wheel contains/imports the core Office dependencies.

## T02 - Add Structured Office Blocks and Rendering

Depends on: T00. May proceed alongside T01 until Office-library imports are needed.

Files:

- Modify `src/opendocs/_models.py:33` and `src/opendocs/_models.py:125`.
- Modify `src/opendocs/markdown.py:21`, `src/opendocs/markdown.py:79`, and
  `src/opendocs/markdown.py:101`.
- Extend `tests/test_models.py` and `tests/test_markdown.py`.

Test first:

1. Constructor validation for `InlineText`, `InlineLink`, `ParagraphBlock`, `HeadingBlock`,
   `ListItemBlock`, `HardPageBreakBlock`, `SpannedTableCell`, and `SpannedTableBlock`.
2. Reject unsafe spans: overlaps, uncovered coordinates, out-of-range origins, zero/negative spans,
   and invalid header rows.
3. Exact renderer tests for heading levels, paragraph soft breaks, nested/restarted lists, safe and
   unsafe links, hard-page comments, merged HTML cells, escaping, and semantic emptiness.
4. Output-budget tests prove truncation happens only between complete list items/blocks.
5. Run all existing renderer tests unchanged before implementing.

Implement:

1. Keep existing block classes and constructors unchanged; extend the private `Block` union.
2. Model links as structured values. Validate targets with `urllib.parse`; render only `http`,
   `https`, `mailto`, and local fragments as links.
3. Coalesce compatible consecutive list items while retaining complete-item budget boundaries.
4. Render spanned tables as deterministic HTML; leave existing `TableBlock` rendering untouched.
5. Add warnings for unsafe link targets through `RenderResult`, with stable order and no target
   fetching.

Verification:

```bash
uv run --frozen pytest tests/test_models.py tests/test_markdown.py -q
uv run --frozen pytest tests/test_pdf_merge.py tests/test_image_parser.py -q
```

Exit: new blocks render exactly and every pre-M2 block still renders byte-for-byte identically.

## T03 - Build the OOXML Preflight and Worker Wire Boundary

Depends on: T01, T02.

Files:

- Add `src/opendocs/parsers/office/__init__.py`.
- Add `src/opendocs/parsers/office/models.py`.
- Add `src/opendocs/parsers/office/package.py`.
- Add `tests/test_office_models.py` and `tests/test_office_package.py`.
- Reuse `ParseWorkspace.output_path()` at `src/opendocs/source.py:40` and worker serialization at
  `src/opendocs/_native_protocol.py:18`.

Test first:

1. Strict tagged-wire round trips for document, page, native, image, and break slots.
2. Reject unknown/missing fields, non-tuple containers, invalid source indexes, invalid bboxes,
   duplicate slot indexes, and artifact paths with directory components.
3. Synthetic packages cover member count, declared total size, per-XML/media size, aggregate media,
   compression ratio, duplicate names, traversal, absolute names, encryption, missing required
   parts, and broken relationships.
4. Spy on the Office-library opener and prove every failed preflight occurs before it is called.
5. Verify extracted media uses generated workspace basenames and never member-provided paths.

Implement:

1. Define immutable Office wire models and explicit `to_wire`/`from_wire` functions.
2. Define named private ZIP/media limits; calibrate values with bounded synthetic stress fixtures.
3. Validate the package centrally before either format-specific extractor runs.
4. Write media directly below the source-owned workspace. Return only metadata and portable
   basenames through the native worker frame.
5. Map corrupt/encrypted structure to `CorruptDocumentError` and hard limits to
   `LimitExceededError`.

Verification:

```bash
uv run --frozen pytest tests/test_office_models.py tests/test_office_package.py \
  tests/test_runtime.py -q
```

Exit: no untrusted package reaches an Office library or creates an uncontained artifact.

## T04 - Implement DOCX Native Extraction

Depends on: T02, T03. May run in parallel with T05.

Files:

- Add `src/opendocs/parsers/office/docx.py`.
- Add `tests/test_docx_extract.py`.
- Add or reuse a bounded synthetic builder in `tests/office_fixtures.py`.
- Reference the local upstream `docx_extractor.py` and its focused tests.

Test first:

1. Body paragraph/table order and paragraph-internal text/link/drawing order.
2. Direct, inherited, English, and Chinese heading-level detection.
3. Bullet/decimal nested lists, abstract numbering, `startOverride`, restart, and interruption.
4. Safe/unsafe/external link targets with no network access.
5. Inline image before/between/after text runs, repeated media relationships, and alt metadata.
6. Simple tables, explicit header rows, horizontal/vertical merges, nested-table text flattening,
   and one bounded warning per flattened table.
7. Manual page breaks and page-start section breaks emit break slots, not numbered pages.
8. Unsupported constructs preserve surrounding content and emit stable warning codes.
9. Empty and content-free documents produce no semantic slots.

Implement:

1. Iterate body and paragraph XML children rather than relying on `document.paragraphs` or
   `paragraph.text`.
2. Keep format-specific OOXML access private to this module.
3. Resolve numbering into stable list IDs, levels, kinds, and ordinals without exposing OOXML IDs.
4. Split paragraphs around images while preserving heading/list semantics for text fragments.
5. Produce validated Office slots only; do not render Markdown or return media bytes.

Verification:

```bash
uv run --frozen pytest tests/test_docx_extract.py tests/test_office_models.py -q
```

Exit: every accepted DOCX native feature is source ordered in a deterministic wire document.

## T05 - Implement PPTX Native Extraction

Depends on: T02, T03. May run in parallel with T04.

Files:

- Add `src/opendocs/parsers/office/pptx.py`.
- Add `tests/test_pptx_extract.py`.
- Extend `tests/office_fixtures.py`.
- Reference the local upstream `pptx_extractor.py` and its focused tests.

Test first:

1. One page per slide, including a blank slide, in deck order.
2. Shape-tree order intentionally different from top-left visual order.
3. Nested group traversal is depth first and child ordered.
4. Text paragraphs, soft breaks, nested/restarted bullets/numbers, and hyperlinks.
5. Title/subtitle placeholders and largest non-numeric freeform-title fallback boundaries.
6. Simple and merged tables with explicit first-row header semantics.
7. Charts with title+data, title-only, inaccessible data, degenerate categories, and stable
   warnings.
8. Pictures retain shape positions; linked media, SmartArt, OLE, audio, and video are never fetched.
9. EMU bboxes are valid diagnostics but never affect output order.

Implement:

1. Iterate presentation slides and source shape collections directly.
2. Pre-scan headings once per slide, then traverse shapes once for output.
3. Keep chart title and data-table blocks adjacent within one native slot.
4. Emit image slots with workspace artifacts and SHA-256 metadata.
5. Preserve source shape indexes through all group recursion and warning creation.

Verification:

```bash
uv run --frozen pytest tests/test_pptx_extract.py tests/test_office_models.py -q
```

Exit: every accepted PPTX native feature is slide/shape ordered without geometry sorting.

## T06 - Extract a Shared Embedded-Image Sanitization Seam

Depends on: T00, T03. Complete before T07.

Files:

- Refactor `src/opendocs/parsers/image.py:43` minimally.
- Extend `tests/test_image_parser.py`.
- Add embedded-image cases to `tests/test_office_parser.py` if that test file already exists in the
  active execution branch; otherwise add them in T07.

Test first:

1. Snapshot existing standalone PNG/JPEG/WebP success and all mismatch/error behavior.
2. Test a workspace media artifact without caller suffix checks but with the same decode, pixel,
   EXIF, metadata stripping, resize, and output-format guarantees.
3. Prove the shared seam never mutates Pillow process globals and always closes images.

Implement:

1. Extract a private sanitizer function/object with explicit standalone versus embedded policy.
2. Keep `ImageParser.parse()` behavior and call sequence unchanged.
3. Let Office orchestration supply unique sanitized output paths below the workspace.

Verification:

```bash
uv run --frozen pytest tests/test_image_parser.py -q
```

Exit: Office can sanitize media through the M1 pipeline with zero standalone-image regression.

## T07 - Implement Source-Slot Merge and Office Orchestration

Depends on: T04, T05, T06.

Files:

- Add `src/opendocs/parsers/office/merge.py`.
- Add `src/opendocs/parsers/office/parser.py`.
- Add `tests/test_office_merge.py` and `tests/test_office_parser.py`.
- Reuse `VisionClient.analyze()` at `src/opendocs/vision/base.py:124` and FIFO behavior at
  `src/opendocs/vision/base.py:334`.

Test first:

1. Native-only DOCX/PPTX requires zero model calls and preserves every source slot.
2. Mixed content replaces image slots in place; native slots never reorder.
3. Exact duplicate media produces one sanitize/model call and repeated in-place output.
4. Slow/retrying early images and fast later images finish out of order but merge source ordered.
5. Missing vision: native content plus `vision_unavailable_native_only`; image-only document raises
   `VisionRequiredError`.
6. Recoverable decode/model failure returns other semantic content with location-stable warnings.
7. Authentication, permission, and invalid request are fatal even with native content.
8. All visual work failing raises the most specific typed error; all blank raises
   `NoUsableContentError`.
9. Visual count/byte/pixel limits degrade only when native content remains; otherwise they raise
   `LimitExceededError`.
10. Blank slides retain numbered page comments but do not count as semantic content.
11. Cancellation stops pending admission and cleans every extracted/sanitized artifact.

Implement:

1. Run one format-specific native extraction through `ParserRuntime.run_native()`.
2. Collect first-occurrence image hashes in source order and sanitize them in bounded work.
3. Submit vision requests in source order; store outcomes by content hash.
4. Walk Office pages/slots once to build final blocks and stable warnings.
5. Implement `aclose()` only if Office owns an independently closeable resource; otherwise rely on
   the shared runtime/client lifecycle at `src/opendocs/api.py:107`.

Verification:

```bash
uv run --frozen pytest tests/test_office_merge.py tests/test_office_parser.py \
  tests/test_vision_base.py tests/test_runtime.py -q
```

Exit: Office produces one deterministic `ParsedDocument` and exactly matches the PDF degradation
matrix.

## T08 - Register Office and Prove API/Runtime Contracts

Depends on: T07.

Files:

- Modify `src/opendocs/parsers/registry.py:52`.
- Add `tests/test_api_m2.py`.
- Extend `tests/test_registry.py`, `tests/test_runtime.py`, and `tests/test_package.py`.
- Do not change public exports unless an already public exception is missing from `__all__`.

Test first:

1. Registry returns DOCX/PPTX parsers only when runtime dependencies are present.
2. Path, bytes, named stream, and unnamed stream inputs reach equivalent parsers/results.
3. `parse()` and `aparse()` outputs/warnings match byte for byte.
4. PPTX exceeding `ParseOptions.max_pages` fails before image/model scheduling.
5. DOCX remains one logical flow and does not misuse `max_pages` as physical pagination.
6. `max_output_chars` truncates only between complete semantic blocks.
7. Global timeout and external cancellation reap the native worker before owned-source cleanup for
   all three input shapes.
8. Unsupported legacy `.doc`/`.ppt` remains explicit and no URL is downloaded.

Implement:

1. Construct one Office parser with the shared runtime, vision client/config, and deadline.
2. Register it for both `DocumentType.DOCX` and `DocumentType.PPTX`.
3. Keep API orchestration unchanged unless a failing lifecycle test proves a required generic fix.

Verification:

```bash
uv run --frozen pytest tests/test_registry.py tests/test_api_m2.py \
  tests/test_runtime.py tests/test_package.py -q
uv run --frozen pytest -q
```

Exit: both formats work end to end through the unchanged public API with no surviving resources.

## T09 - Complete the Public M2 Regression Matrix

Depends on: T08.

Files:

- Consolidate the new M2 test modules without moving unrelated M0/M1 tests.
- Update `.github/workflows/ci.yml` only if dependency installation or an explicit matrix assertion
  requires it.

Steps:

1. Review AC-01 through AC-17 and map each to at least one named test.
2. Add repeat-run determinism assertions for a synthetic native DOCX/PPTX and fixed fake vision.
3. Add warning-spam bounds for repeated unsupported constructs.
4. Verify no public test accesses `tests/corpus/`, credentials, network, or a system Office program.
5. Run coverage-oriented targeted groups followed by every repository check.

Verification:

```bash
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests
uv build
```

Exit: AC-01 through AC-17 are public, deterministic, and green.

## T10 - Add Human-Approved Private Acceptance and Documentation

Depends on: T09.

Files:

- Add `tests/test_m2_acceptance.py`.
- Add a strict checklist validator and candidate helper under `tests/`.
- Modify `tests/conftest.py:9` with `--m2-checklist-dir`, `--m2-replay-dir`, and `--m2-live`.
- Update `.gitignore` for concrete local M2 checklist/replay directories.
- Update `README.md`, `CONTRIBUTING.md`, `docs/roadmap.md`, and the architecture status.
- Keep completed checklists, anchors, replay/model output, and credentials local.

Test first:

1. Public manifest selection accepts exactly two distinct M2 entries and rejects malformed scope.
2. Asset verification is atomic: neither private path is returned until both names/hashes pass.
3. Checklist schema rejects raw content, paths, missing approval, wrong asset hash, wrong extractor
   version, malformed anchor hashes, extra files, and self-approved generator output.
4. Candidate generation always writes `approved = false`; only a maintainer edits local approval.
5. Replay requires exact request count/order, warning sequence, Markdown hash, and repeated-run
   output.
6. Live mode requires ordered structural coverage and call budgets but not exact wording.
7. No M2 option means the private module skips; selecting a mode with missing inputs is a usage
   error, not a silent skip.

Implement:

1. Reuse the hardened M1 asset/replay validation patterns rather than generalizing prematurely.
2. Keep the M2 schema format-specific: DOCX ordered anchors/headings/tables/images and PPTX
   per-slide anchors/tables/charts/images.
3. Generate a local candidate, manually compare it with both source documents, then explicitly
   approve the baseline.
4. Update public documentation only after public and private behavior is proven.

Verification:

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

Exit: AC-18 passes with human-approved evidence, documentation matches reality, and no private
artifact appears in `git status`.

## Risks and Mitigations

- **Office-library OOXML APIs drift (T01/T04/T05):** pin `<2`, isolate internal access, and test
  every accessed shape.
- **ZIP bombs exhaust memory (T03):** complete central-directory preflight before any Office
  opener.
- **Media exceeds worker frames (T03):** write workspace artifacts and send only basename/hash.
- **DOCX numbering is ambiguous (T04):** resolve definitions explicitly; warn and preserve text on
  fallback.
- **PPTX geometry invites reordering (T05):** make shape-tree index authoritative and use bboxes
  only for diagnostics.
- **Shared image changes regress M1 (T00/T06):** lock exact image behavior before extracting the
  seam.
- **Concurrent model calls reorder output (T07):** use FIFO admission plus hash/slot merge, never
  completion order.
- **Table spans break rectangular tables (T02):** add a spanned block; do not mutate `TableBlock`.
- **Private acceptance gets auto-approved (T10):** prevent generator approval and never rewrite a
  mismatched baseline.
- **Scope expands toward visual fidelity (all reviews):** enforce architecture non-goals and
  decision gates.

## Final Verification Sequence

Run in this order after T10. A failure returns work to the owning task; do not waive a gate.

```bash
uv sync --all-groups --frozen
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests
uv build
uv run --frozen pytest tests/test_acceptance_corpus.py -q --corpus-dir=@local
uv run --frozen pytest tests/test_m2_acceptance.py -q \
  --corpus-dir=@local \
  --m2-checklist-dir=tests/m2-acceptance.local \
  --m2-replay-dir=tests/m2-replay.local
```

Also run the opt-in live M2 gate once with the intended provider and record only structural/call
evidence. Never record credentials, prompts, raw document content, or model responses.

## Definition of Done

- AC-01 through AC-19 have named passing evidence.
- Both DOCX and PPTX are registered as core formats; neither format is declared complete alone.
- Native/replay determinism and PDF-matched degradation semantics are proven.
- M0/M1 output, public exports, errors, and cleanup behavior remain unchanged.
- Both private M2 assets pass an approved local checklist and replay; live structure also passes.
- The wheel, sdist, public docs, roadmap status, and dependency metadata match implemented behavior.
- `git status` contains no corpus file, completed checklist, content anchor, replay/model output, or
  credential.

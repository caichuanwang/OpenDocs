# M1 PDF and Images Architecture Plan

> Archived milestone plan. M1 is implemented; current behavior is defined by the source, tests, and
> roadmap.

Status: archived; implementation complete
Scope: PNG/JPEG/WebP, PDF native/hybrid/full-vision/blank routing, internal vision adapter
Depends on: completed M0 foundation and the accepted design in
`docs/archive/m0/2026-07-27-opendocs-foundation-and-roadmap-design.md`

## Objective and Exit Contract

M1 extends the existing local-document-to-Markdown SDK without widening the public source boundary.
It must:

- parse standalone PNG, JPEG, and WebP through a configured vision model;
- parse PDFs page by page using native extraction first and vision only where necessary;
- preserve page and region order while emitting one deterministic Markdown stream;
- preserve a 20-column, multi-row-header table from the M1 ultra-wide PNG corpus asset;
- include every expected page from both M1 PDF corpus assets;
- avoid full-page model calls for pages whose usable content is fully native;
- preserve M0 source ownership, timeout, warning, error, and rendering contracts.

M1 does not add remote downloads, local OCR, PyZeroX, a second PDF fallback pipeline, a public JSON
schema, parser plugins, or provider-specific model types in public APIs.

## Invariants Carried Forward from M0

1. `source.py` remains the only owner of input normalization and SDK-created temporary files.
2. `detection.py` detects types without importing parsers.
3. `registry.py` maps one `DocumentType` to one parser; `api.py` contains no format extraction logic.
4. Parsers return private ordered blocks and warnings. Only `markdown.py` emits user-facing Markdown.
5. `parse()` and `aparse()` share one async core and stay byte-for-byte equivalent.
6. Global `ParseOptions.timeout` covers materialization, extraction, model calls, merging, rendering,
   and owned-source cleanup behavior already defined by M0.
7. Page admission, model-call admission, merge order, and warnings are deterministic in source order.
8. A caller-owned path is never deleted; no private corpus file, credential, prompt payload, or model
   output is committed.

## Dependency Boundary

Add runtime dependencies in the first M1 implementation slice, with exact compatible versions locked
by uv:

- Pillow: image verification, EXIF orientation, RGB normalization, PNG/JPEG/WebP decoding;
- pdfplumber: native PDF characters, words, tables, image/drawing metadata, page geometry;
- LiteLLM Python SDK: the only provider-neutral vision call layer.

Animated WebP is outside M1: reject `n_frames > 1` rather than silently processing one frame.
Pillow's process-global `MAX_IMAGE_PIXELS` is not mutated per request; OpenDocs enforces its own
private hard budget and promotes Pillow decompression-bomb warnings to failures locally.

Poppler is a documented system dependency, not a Python package. Integrate only its `pdftoppm`
executable behind `parsers/pdf/render.py`. Check availability lazily only when a page needs raster
rendering. Use an argv list (never a shell), the source-owned parse workspace, `-f`/`-l` for the
selected page, `-png`, and a bounded long-side scale. Map exit codes and stderr to stable OpenDocs
errors without leaking paths, credentials, or model payloads.

Do not use `pdfplumber.Page.to_image()` as the production renderer: current pdfplumber delegates that
path to pypdfium2, while the accepted design explicitly requires selective Poppler rendering.

## Target Module Layout

```text
src/opendocs/
├── _models.py                 # ordered content/table/page private models
├── _runtime.py                # per-parse runtime, FIFO dispatcher, native worker lifecycle
├── markdown.py                # sole renderer; add table/page-aware blocks
├── options.py                 # existing public budgets only
├── source.py                  # source normalization plus owned parse workspace
├── parsers/
│   ├── image.py               # standalone image parser
│   └── pdf/
│       ├── __init__.py
│       ├── analyze.py         # pdfplumber page facts and route selection
│       ├── extract.py         # native text/table extraction
│       ├── merge.py           # ordered native/vision merge and deduplication
│       ├── models.py          # private page/region/table contracts
│       ├── parser.py          # PDF parser orchestration
│       └── render.py          # selective pdftoppm adapter
└── vision/
    ├── __init__.py
    ├── base.py                # provider-neutral protocol and request/result models
    ├── prompts.py             # versioned general-image and table prompts/schemas
    └── litellm.py             # sole LiteLLM import, retry/error/usage normalization
```

`parsers/image.py`, `parsers/pdf/`, `_runtime.py`, and `vision/` remain private. The M1 public surface
stays `parse`, `aparse`, `ParseOptions`, `VisionConfig`, errors, and warnings.

Introduce an internal `ParserRuntime` assembled by `api.py` from `VisionConfig`, parse budgets, and a
`ParseWorkspace` context yielded by `source.py`. `source.py` is the sole creator/deleter of this
workspace and the materialized input; Poppler and parsers receive paths inside it but never create
or delete parse directories. `build_default_registry(runtime)` constructor-injects the shared vision
client, FIFO dispatcher, native worker, and Poppler adapter into image/PDF parsers. Keep the
`DocumentParser.parse(source, *, options)` protocol unchanged; parser methods do not accept public or
provider objects, and the M0 text parser remains stateless. Construct the runtime once per public
parse call and close it before `source.py` removes the workspace or owned source.

All Pillow decode/verify/load work and all pdfplumber open/analyze/extract work run in one spawned,
per-parse native worker process. Its versioned JSON IPC is restricted to small control/result frames
(maximum 12 MiB with an 8 MiB recursive inline-value budget); large images, PDFs, and native results
must be written below the source-owned workspace and passed as validated `Path` values rather than
base64/JSON. The protocol uses a private duplicate of the worker's original stdout while redirecting
FD 1 away from it, so Python/native/descendant stdout cannot corrupt frames. On timeout or
cancellation, `ParserRuntime.aclose()` stops admitting
work, terminates the worker, waits a bounded grace period, escalates to kill, and shield-waits for
process exit before source cleanup continues. No thread or process may retain the source after the
public call returns. Poppler runs through `asyncio.create_subprocess_exec()` and follows the same
terminate/wait/kill rule. Tests cover bytes, `BinaryIO`, and caller-owned paths under timeout and
external cancellation, and assert no surviving worker can access the source after return.

## Private Data Contracts

Extend the private intermediate representation rather than letting extractors generate Markdown:

- `PageBreakBlock(page_number)`: stable page boundary rendered by `markdown.py` as an HTML comment
  (`<!-- page: N -->`) so completeness is observable without creating artificial document headings.
- `TableBlock(grid, header_rows)`: a non-empty rectangular tuple-of-tuples of strings plus an integer
  header-row count. Normalize pdfplumber `None` cells to empty strings; require at least one row and
  column, equal row widths, and `0 <= header_rows <= row_count`. Render only `header_rows == 1` as a
  Markdown pipe table. Render `header_rows == 0` or `> 1` as deterministic HTML
  `<table><thead>…</thead><tbody>…</tbody></table>`; omit `<thead>` for zero headers. Escape pipe cells
  once for pipes/backslashes/newlines and HTML cells once for `&`, `<`, `>`, quotes, and newlines.
- `VisionTextElement(text, bbox, source_index)` and
  `VisionTableElement(grid, header_rows, bbox, source_index)`: a tagged union containing only
  OpenDocs-owned values. The wire schema has an explicit `type: "text" | "table"` discriminator.
  Every normalized PDF element carries a bbox in `page-normalized-v1` coordinates.
- PDF-local `BBox`, `CoordinateTransform`, `NativeRegion`, `VisualRegion`, `PageFacts`, `PageRoute`,
  and `PageResult` models. `page-normalized-v1` uses the displayed CropBox after page rotation,
  top-left origin, and coordinates in `[0, 1]`. General PDF point-space `BBox` values may be negative;
  normalized bboxes enforce all four edges in `[0, 1]`. `CoordinateTransform` records MediaBox,
  CropBox, rotation, full Poppler raster dimensions, and one positive crop pixel rectangle contained
  by that raster, with tested point↔normalized↔crop-pixel mappings for all four rotations.

The renderer converts `TableBlock` once into deterministic Markdown. Extractors and prompts must not
also append pre-rendered table text. This prevents the accepted-design failure mode where a table is
represented twice as raw JSON and rendered text.

## Image Pipeline

`ImageParser.parse()` performs these stages:

1. Require `VisionConfig`; otherwise raise `VisionRequiredError` before any model call.
2. Open the materialized local path with Pillow and allow only decoded PNG, JPEG, or WebP. Because
   M0 detection intentionally collapses these signatures into `DocumentType.IMAGE`, compare Pillow's
   concrete `image.format` with the source suffix and raise `DocumentTypeMismatchError` for
   `.png`/`.jpg`/`.jpeg`/`.webp` cross-format mismatches before any model call.
3. Call `verify()`, then reopen because Pillow invalidates the decoder after verification.
4. Enforce explicit width, height, and total-pixel budgets before full decode. Treat Pillow
   decompression-bomb warnings as hard `LimitExceededError` for caller-provided images.
5. Fully `load()`, apply `ImageOps.exif_transpose()`, remove metadata, convert to RGB, and resize only
   downward to the internal model-input long-side/pixel budget.
6. Encode one sanitized in-memory PNG for the vision adapter; never send the original file or EXIF.
7. Route ultra-wide images to the table schema/prompt; route other images to the general visual
   document schema/prompt.
8. Validate the provider-neutral result. A table result becomes one `TableBlock`; ordinary content
   becomes ordered text blocks. Empty valid output raises `NoUsableContentError`.

Pixel and raster dimensions are private constants in M1. Do not add public options until callers have
an evidenced need to tune them. Calibrate concrete values with memory/latency measurements before
registering the parser; tests lock the chosen values and rejection behavior. Animated images are a
typed unsupported input in M1.

## Vision Adapter

`vision/base.py` defines an async `VisionClient` protocol and immutable internal request/result
models. Parsers receive this abstraction and never import LiteLLM.

`vision/litellm.py` is the only module importing LiteLLM. It must:

- call `litellm.acompletion()` with explicit `model`, request timeout, credentials/base URL only when
  supplied, and sanitized image data URLs;
- reject a known non-vision model before sending a request when `supports_vision()` is authoritative;
- select one deterministic response mode: strict JSON Schema when `supports_response_schema()` is
  true; JSON object mode when `get_supported_openai_params()` includes `response_format`; otherwise
  plain text mode;
- classify each request as `structured_required` (table or hybrid crop) or `prose_flexible`
  (ordinary standalone image or full-page prose). In strict/object mode, parse and locally validate
  JSON; on failure, make one repair request and then raise `model_invalid_response`. In plain-text
  mode, `structured_required` extracts exactly one JSON object, validates it, repairs once on failure,
  then raises. `prose_flexible` first accepts valid JSON; otherwise a non-empty, non-JSON-looking
  response becomes conservative Markdown, while malformed JSON-looking text gets one repair before
  `model_invalid_response`. Table requests never fall back to Markdown;
- normalize output into OpenDocs-owned dataclasses immediately; no LiteLLM type crosses the adapter;
- submit every initial request, retry, and repair to one explicit FIFO dispatcher capped by
  `ParseOptions.vision_concurrency`; workers do not race to acquire a semaphore directly. The FIFO
  ticket is an independent, strictly increasing `enqueue_sequence`. `source_index`, request kind,
  retry index, and repair index are trace metadata only;
- retry only rate-limit, timeout, and server/unavailable failures up to `VisionConfig.max_retries`,
  with bounded backoff inside the global parse deadline;
- never retry authentication, permission, or invalid-request errors;
- allow at most one repair request for any repairable response-format failure: malformed JSON,
  missing/extraneous JSON extraction, schema validation failure, or malformed JSON-looking prose.
  Repair is separate from `max_retries`, consumes the same remaining document deadline, and never
  applies to transport/authentication/permission/invalid-request failures;
- normalize usage/cost metadata internally without exposing credentials or raw provider payloads.

Model-call tasks may execute concurrently, but the FIFO dispatcher alone admits them. All initial
requests are enqueued in source order before workers start; each retry or repair appends to the queue
tail with a new `enqueue_sequence` after its backoff. This is strict FIFO by actual enqueue time—an
early page does not let a later-created retry jump ahead of already queued work. The dispatcher stops
admission on cancellation and records an internal admission trace. Results land in preallocated
source-order slots. Fake-client tests cover a slow/retrying early page, a fast later page, repair,
failure, and cancellation and assert the exact admission sequence.

## PDF Analysis and Routing

Open the PDF with pdfplumber in the per-parse native worker process and reject encrypted/corrupt
documents with `CorruptDocumentError`. Enforce `ParseOptions.max_pages` before scheduling page work.
The worker processes bounded page batches, closes each pdfplumber page cache after extraction, and
is terminated and reaped by `ParserRuntime` before source cleanup on timeout/cancellation. Never use
`asyncio.to_thread()` for Pillow/pdfplumber source access: cancelling its await cannot stop the
thread, so it cannot satisfy the M0 owned-source lifetime contract.

For each page, `analyze.py` records bounded facts:

- page number and geometry;
- native character/word count and normalized text coverage;
- native tables and their bounding boxes;
- image, line, rectangle, and curve regions;
- whether useful content remains after native extraction;
- suspicious/native-poor signals such as near-empty text with significant visual coverage.

Classify each page into exactly one route:

| Route | Evidence | Work |
| --- | --- | --- |
| `native` | usable text/tables; no unresolved significant visuals | native extraction only; zero Poppler/model calls |
| `hybrid` | usable native content plus unresolved visual regions with reliable coordinate transforms | keep native-owned regions; render/send only unresolved crops; otherwise reclassify as `full_vision` |
| `full_vision` | little/no usable native content with significant page content | render the page once and use vision as page owner |
| `blank` | no usable native or visual content | emit a page boundary and a warning; no model call |

Routing thresholds are named private constants, tested at boundaries, and calibrated only with the
synthetic fixtures plus the separate M1 corpus. They are not public configuration in M1.

## Native Extraction and Selective Rendering

`extract.py` uses pdfplumber for native words/text and tables. It must preserve source order using
page coordinates, normalize line endings, and represent tables as rectangular `TableBlock` data.
`layout=True` is experimental in pdfplumber, so it cannot be the sole ordering contract; order from
word/table bounding boxes and lock it with synthetic fixtures. Resolve native ownership before emitting candidates. Normalize table bboxes, discard invalid grids,
then sort candidates by descending cell count, descending bbox area, top, left, and stable discovery
index. Greedily accept a candidate only when it is neither contained by an accepted bbox nor has
intersection-over-union `>= 0.80` with one; equal-area ties use the earlier stable index. Distinct
partially overlapping candidates below the threshold remain separate. Accepted table bboxes own and
remove intersecting words/chars, while rejected candidates fall back to their original words. Lock
the `0.80` private constant and tie-break rules with contained, equal, overlapping, and adjacent table
tests, plus paragraphs before/after tables and native/hybrid tables appearing exactly once.

`render.py` invokes `pdftoppm` only for `hybrid`/`full_vision` pages. Render one selected page at a
time using `-f page -l page -singlefile -png -scale-to <bounded-long-side>` inside the source-owned
workspace. Crop hybrid regions only when `CoordinateTransform` can prove an invertible bbox mapping.
The hybrid wire schema uses `crop-normalized-v1` (top-left origin, `[0, 1]` inside the sent crop).
`CoordinateTransform` maps each returned bbox through the crop's page-normalized bbox into
`page-normalized-v1`; reject elements that escape the crop. Hybrid is always `structured_required`,
so Markdown fallback is forbidden. A hybrid page whose unresolved regions cannot be cropped
reliably is reclassified as `full_vision`: vision owns the whole page and native candidates are
discarded. Never attempt an undefined full-page hybrid merge. `source.py` removes render output with
the workspace after all subprocesses exit.

## Merge Algorithm

`merge.py` is the sole place that combines native and visual PDF content:

1. Preallocate page slots in page-number order.
2. Within each page, sort candidates by top coordinate, then left coordinate, then stable source
   index. Never order by task completion time.
3. Assign ownership per region: native text, native table, visual element, or blank. Native tables
   first suppress their intersecting native words/chars. Full-vision owns the whole page; hybrid
   visual elements own only explicit, normalized bboxes returned by the validated schema.
4. Suppress native content intersecting a visual-owned region. Never append visual output to a copy
   of the same native text/table. Reject out-of-range, non-finite, inverted, or coordinate-version
   mismatched bboxes before merge.
5. Convert owned candidates into private renderer blocks, prepend one `PageBreakBlock`, and append
   page warnings in stable code/location order.
6. Let the existing Markdown renderer apply the final output-character budget only between complete
   blocks.

## Failure and Degradation Semantics

- Standalone images cannot fall back to native content: missing vision config raises
  `VisionRequiredError`; exhausted model failure is fatal.
- Native PDF pages remain usable without `VisionConfig`. A PDF that requires visual work but has no
  vision configuration returns all usable native pages/regions plus deterministic warnings. If no
  usable content remains anywhere, raise `VisionRequiredError`.
- A visual page/region failure is recoverable only when the document still has usable native or
  successful visual content; preserve it and emit a stable warning with page/region identity.
- A blank page contributes a page boundary and warning only when another page has semantic content.
  Structural page boundaries do not count as usable content; an all-blank PDF raises
  `NoUsableContentError` before rendering.
- Authentication, permission, invalid-request, corrupt/encrypted document, Poppler absence when
  rendering is required, and exhausted document-level limits map to typed fatal errors.
- Global cancellation and timeout cancel pending model/render tasks, await owned-resource cleanup
  according to existing sync/async semantics, and never replace the primary error with cleanup
  failure.

Before implementation, add concrete model error subclasses for the existing model codes, a
`model_invalid_response` code/class, and a new stable runtime-dependency error/code for missing or
unusable Poppler. Do not mislabel a missing local runtime as an unsupported document. After one repair,
an invalid standalone-image/full-vision response is fatal; if a PDF still has semantic content, the
same failure becomes a stable page/region warning. Define warning codes for native-only degradation,
blank page, visual-region failure, and invalid-response repair failure; update the exact public
`__all__` test only for newly approved public exception classes.

## Implementation Slices

Each slice follows red-green-refactor, runs focused tests, then the full public gate.

1. **M1 contract, dependencies, and resource lifecycle**
   - Lock Pillow, pdfplumber, LiteLLM; document Poppler installation/runtime check.
   - Add private models, `ParseWorkspace`, spawned native-worker lifecycle, coordinate transforms, and
     validation/cancellation tests; do not register new parsers yet.
2. **Vision seam with fake client**
   - Implement `vision/base.py`, schemas/prompts, FIFO dispatcher, admission tracing, and result
     validation.
   - Use deterministic fake-client tests; public CI performs no network/model calls.
3. **LiteLLM adapter**
   - Implement capability detection, `acompletion`, structured/fallback JSON, retry taxonomy,
     timeout, one repair, and exception normalization with a mocked LiteLLM boundary.
4. **Standalone image parser**
   - Add Pillow verify/reopen/orientation/pixel-budget/sanitization pipeline.
   - Test PNG/JPEG/WebP, corruption, bombs, EXIF rotation, ultra-wide table schema, missing vision,
     timeout/cancellation, and no metadata leakage.
5. **PDF native facts and extraction**
   - Add pdfplumber open/page-limit/facts/text/table extraction and synthetic fixtures.
   - Test encrypted/corrupt PDFs, page ordering, rectangular tables, and native-only zero-call path.
6. **PDF routing and Poppler adapter**
   - Lock native/hybrid/full-vision/blank boundary tests.
   - Test exact argv, selected-page rendering, scale bounds, exit-code mapping, missing executable,
     timeout/cancellation, and output cleanup.
7. **PDF vision and deterministic merge**
   - Add source-order admission, bounded concurrency, visual ownership, deduplication, page blocks,
     warnings, and partial-success behavior.
8. **Registry/API integration**
   - Register IMAGE and PDF parsers only after their focused suites pass.
   - Confirm path/bytes/BinaryIO and sync/async equivalence, output limits, public exports, and wheel
     installation.
9. **Private M1 acceptance gates**
   - Add `tests/test_m1_acceptance.py` and options `--m1-replay-dir` / `--m1-live`. A module-scoped
     fixture selects exactly the three `milestone = "M1"` public-manifest entries, resolves their
     private paths, and verifies all three names/hashes before returning any path to a parse test.
   - Replay command:
     `uv run --frozen pytest tests/test_m1_acceptance.py -q --corpus-dir=@local --m1-replay-dir=tests/m1-replay.local`.
     A missing directory, missing expected recording, schema/prompt/model-version mismatch, or extra
     private filename is a usage error/failure—not a skip. The replay `VisionClient` is wrapped by a
     recording runtime that asserts route, attempt, full-page/crop call, FIFO admission, page order,
     table shape, and deterministic merge traces. Parse page comments/tables from Markdown for
     completeness and ordering.
   - Live command:
     `OPENDOCS_VISION_MODEL=<provider/model> uv run --frozen pytest tests/test_m1_acceptance.py -q --corpus-dir=@local --m1-live`.
     `OPENDOCS_VISION_API_KEY` and `OPENDOCS_VISION_API_BASE` are optional provider inputs. Without a
     model the live test skips; without either M1 flag, the module skips so public CI never calls a
     model. Wrap the live client with the same recorder and assert structure plus route/call budgets,
     while allowing content wording to vary.
   - Store replay and model output only under gitignored `tests/m1-replay.local/` or another explicit
     local path. Commit only non-sensitive expected counts, schema/prompt versions, and structural
     assertions—never private text, images, model payloads, or responses.
10. **Documentation and CI proof**
    - Update README format matrix, Poppler/model setup, cost/privacy notes, dependency boundaries,
      warning/error behavior, and roadmap status.
    - Run the full quality, architecture, build, isolated-wheel, and private-corpus gates.

## Verification Matrix

Public CI, with no credentials, Poppler requirement, network, or private files:

- unit tests for every private model, classifier boundary, merge rule, renderer block, error, warning,
  retry decision, and cleanup path;
- exact table rendering tests for zero/one/multiple header rows, 20 columns, empty/`None`, special
  characters, multiline cells, overlapping native table bboxes, and no duplicate cell text;
- synthetic PNG/JPEG/WebP and native/hybrid/full-vision/blank PDF fixtures, including all-blank fatal
  semantics and content-plus-blank page-boundary behavior;
- fake vision client and mocked LiteLLM adapter tests with FIFO admission traces across retry, repair,
  slow early pages, fast later pages, failure, and cancellation;
- fake `pdftoppm` executable tests for argv, outputs, coordinate transforms, error mapping, and cleanup;
- spawned native-worker timeout/cancellation tests for path, bytes, and BinaryIO inputs that assert the
  worker is reaped before source cleanup and API return;
- path/bytes/BinaryIO and sync/async equivalence;
- `uv run --frozen pytest -q`, Ruff, formatting, ty, build, isolated wheel smoke test.

Optional local acceptance gate:

- `Tacore.aiBP.pdf`: all 17 pages represented in order; full-vision route evidence;
- `外贸商业计划书_BP.pdf`: all 22 pages represented; native/hybrid routes; native pages do not issue
  unnecessary full-page calls; tables occur once;
- ultra-wide PNG: exactly 20 columns and 4 body rows, with the expected multi-row header preserved;
- repeated run determinism and model-call count/cost records, with raw private content and model
  payloads excluded from Git and logs.

## Architecture Decision Gates Before Coding

Implementation may begin with the defaults in this plan. Escalate only if evidence forces one of
these changes:

1. Poppler crop-coordinate reliability is insufficient; reclassify that page as `full_vision`,
   discard native candidates, and give vision sole page ownership—not another PDF library or an
   undefined full-page hybrid merge.
2. A target LiteLLM provider lacks strict response schemas; default fallback is locally validated
   JSON with one repair request, not provider-specific public configuration.
3. Private corpus calibration shows the route constants cannot separate native/hybrid/full-vision
   documents; record the evidence and revise named private constants without widening public API.
4. The package name `opendocs-sdk` becomes unavailable before first release; change only the
   distribution name and keep `import opendocs` stable.

## References

- Accepted project design:
  `docs/archive/m0/2026-07-27-opendocs-foundation-and-roadmap-design.md`
- Roadmap: `docs/roadmap.md`
- Pillow `Image.verify()` and `ImageOps.exif_transpose()` documentation
- pdfplumber README/API: text, tables, page objects, and explicit lack of OCR
- Poppler `pdftoppm(1)`: selected pages, PNG output, scaling, and exit codes
- LiteLLM documentation: async completion, vision support, structured-output capability checks

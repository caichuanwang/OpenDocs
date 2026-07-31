# M3 Alpha Release Detailed Implementation Plan

Status: accepted plan; implementation not started
Date: 2026-07-31
Architecture: `docs/plans/2026-07-31-m3-alpha-release-architecture.md`

## Requirements Summary

M3 releases the existing parser set as `opendocs-sdk==0.1.0` Alpha. It adds quality evidence,
minimal independent Office holdout, Ubuntu/macOS verification, distribution checks, a standalone
consumer example, and a protected Trusted Publishing workflow.

It does not add formats, public parsing options, dependency extras, global concurrency control,
model-cost limits, a performance SLA, a CLI, a service, Node.js support, or a public structured
result.

The milestone is not complete at "release ready." It ends only after the exact version is on public
PyPI, public-index smoke tests pass on Ubuntu and macOS, and GitHub tag/Release `v0.1.0` exists.

## Preconditions and Human-Owned Inputs

The following inputs cannot be synthesized by implementation:

- a maintainer-approved M2 local checklist;
- one genuinely unseen real DOCX for Office holdout;
- one genuinely unseen real PPTX for Office holdout;
- access to the accepted model provider for required live gates;
- maintainer access to configure GitHub Environments and PyPI/TestPyPI Trusted Publishers;
- explicit approval at the public `pypi` environment gate.

Private files remain outside Git. If either Office holdout file has already been inspected for
tuning, it is not eligible and must be replaced before T10.

## Delivery Graph

```text
T00 Baseline and M2 closure
 |
 +--> T01 Alpha and compatibility contracts
 |
 +--> T02 Benchmark schema and split isolation
          |
          +--> T03 Quality evaluators
          |       |
          |       +--> T04 Private runners and aggregate evidence
          |
          +--> T05 Resource and lifecycle gates
 |
 +--> T06 Package and artifact boundaries
 |
 +--> T07 Ubuntu/macOS CI and Poppler smoke
 |
 +--> T08 External consumer and public documentation
 |
 +--> T09 Protected release workflow
                  |
                  v
        T10 Release-candidate evidence freeze
                  |
                  v
        T11 Trusted Publisher configuration and rehearsal
                  |
                  v
        T12 Public publish, smoke, and GitHub Release
```

T02, T06, and the initial T07 matrix work may proceed after T00/T01. T03 depends on the schema. T04
and T05 share evidence formats and should not independently redefine them. T10 freezes the exact
candidate; no source or policy change is allowed between T10 and T12 without repeating affected
gates.

## Acceptance Mapping

| Architecture criterion | Implementation proof |
| --- | --- |
| AC-01 M2 approved | T00 checklist/replay/live command |
| AC-02/03 split composition | T02 manifest validator |
| AC-04 split isolation | T02 negative tests |
| AC-05 PDF/image holdout | T03/T04 frozen-policy report |
| AC-06 Office holdout | T03/T04 structural report |
| AC-07 lifecycle/resource behavior | T05 public tests and private run |
| AC-08 supported platform CI | T07 matrix |
| AC-09 package integrity | T06 artifact suite |
| AC-10 independent consumer | T08 local-artifact and T12 PyPI smoke |
| AC-11 Alpha documentation | T01/T08 metadata and documentation tests |
| AC-12 secure publishing | T09 workflow tests/review |
| AC-13 public PyPI/install | T12 public-index jobs |
| AC-14 tag and Release identity | T09/T12 identity checks |
| AC-15 no private artifacts | T02/T04/T06 repository checks |

## T00 - Capture Baseline and Close M2

Purpose: establish the exact pre-M3 behavior and prevent release work from bypassing unfinished M2
acceptance.

Files:

- Read `docs/roadmap.md`.
- Read `docs/archive/m2/2026-07-29-m2-office-implementation.md`.
- Read `tests/conftest.py`, `tests/m2_acceptance.py`, and `tests/test_m2_acceptance.py`.
- Do not change production code in the baseline step.

Steps:

1. Record commit, branch, worktree status, version, test count, and expected opt-in skips.
2. Run the complete public quality suite and build.
3. Validate the ignored M2 checklist/candidate/replay inputs without printing private content.
4. Run the atomic M2 acceptance command using the approved checklist.
5. Run the accepted live portion only with explicit provider authorization and safe output
   handling.
6. Record only pass/fail, corpus hashes, evaluator version, and safe aggregate evidence.
7. If M2 approval is absent or fails, stop the release path and fix M2 under its accepted contract;
   do not waive the prerequisite.

Verification:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests
uv run --frozen pytest -q
uv build
```

Run the exact private options documented by `pytest --help` and the M2 plan using
`--corpus-dir=@local`. Never put private paths or outputs in a checked-in command transcript.

Exit: the public baseline is green and the maintainer-approved M2 gate has passed.

## T01 - Lock Alpha Metadata and Compatibility Contracts

Depends on: T00.

Files:

- Modify `pyproject.toml`.
- Extend `tests/test_package.py`.
- Add or extend public API contract assertions in `tests/test_api.py`.
- Add `CHANGELOG.md`.

Test first:

1. Assert project name is `opendocs-sdk`, version is `0.1.0`, Python requires 3.11+, license
   metadata remains correct, and the Alpha classifier exists.
2. Assert `opendocs.__all__`, public call signatures, option defaults, and exception exports are
   unchanged.
3. Assert sync and async APIs still return `str`.
4. Assert `VisionConfig` and `ParseOptions` do not gain a global semaphore, call cap, token cap, or
   currency cap.
5. Assert package metadata does not claim Windows support or production status.

Implement:

1. Add `Development Status :: 3 - Alpha`.
2. Keep the existing package name/version and core runtime dependencies.
3. Start `CHANGELOG.md` with `0.1.0` formats, public contracts, supported platforms, model/Poppler
   requirements, and known limitations.
4. Document the `0.1.x` compatibility policy and the `0.2.0` breaking-change boundary.

Verification:

```bash
uv run --frozen pytest tests/test_package.py tests/test_api.py -q
uv build
```

Exit: package metadata and public contracts consistently describe the accepted Alpha.

## T02 - Implement Benchmark Schema and Split Isolation

Depends on: T00, T01.

Files:

- Add `benchmarks/document_parsing/README.md`.
- Add `benchmarks/document_parsing/schema.py`.
- Add `benchmarks/document_parsing/policy-v0.1.json`.
- Add `benchmarks/document_parsing/manifest.example.json`.
- Add `tests/test_benchmark_schema.py`.
- Modify `.gitignore`.

Test first:

1. Strict parsing rejects unknown/missing fields, invalid hashes, duplicate item IDs, invalid
   categories, invalid split names, and unsupported policy versions.
2. Reject the same content hash in tuning and holdout.
3. Reject PDF/image pages from one source document crossing splits.
4. Require exactly 30 PDF/image pages per split, six accepted categories, and at least five pages
   per category.
5. Require one real DOCX and PPTX in each Office split.
6. Reject a holdout item marked inspected, used for threshold calibration, or used for prompt/code
   tuning.
7. Verify the example manifest contains placeholders only.
8. Verify ignored private directories cannot be selected by package/build configuration.

Implement:

1. Define immutable records for policy, source document, benchmark item, split, annotation
   references, run identity, and contamination state.
2. Use JSON parsing and explicit validation; do not parse private manifests with ad hoc text
   matching.
3. Store source SHA-256 and a separate source-document identity so page-level duplicate and
   document-level split leakage are both detectable.
4. Make category membership and required counts policy data.
5. Add an explicit `contaminated`/`inspected` transition that moves an item out of holdout and
   requires replacement.
6. Ignore the private manifest, raw documents, annotations, provider payloads, raw Markdown, and
   run workspaces.

Verification:

```bash
uv run --frozen pytest tests/test_benchmark_schema.py -q
uv run --frozen ruff check benchmarks tests/test_benchmark_schema.py
uv run --frozen ty check benchmarks tests/test_benchmark_schema.py
```

Exit: invalid or contaminated splits fail before parsing begins, and private data is excluded from
the repository and distributions.

## T03 - Implement Quality Evaluators

Depends on: T02.

Files:

- Add `benchmarks/document_parsing/evaluate_quality.py`.
- Add `benchmarks/document_parsing/office_quality.py`.
- Add `tests/test_benchmark_quality.py`.
- Add bounded synthetic evaluator fixtures below `tests/benchmark_fixtures/`.

Test first:

1. Exact metric cases for native/scanned text accuracy, native/visual table-cell accuracy,
   visual-content recall/precision, native visual-call rate, and unresolved counts.
2. Category-level failure cannot be hidden by a passing aggregate.
3. Unresolved annotations are disclosed and handled according to the policy rather than silently
   dropped.
4. Threshold values are read from the frozen policy and cannot be supplied from holdout output.
5. Office structural checks detect missing anchors, wrong ordering, missing slide/page boundaries,
   malformed tables, missing visual slots, and unexpected warnings.
6. Office determinism compares native/replay Markdown and normalized warning sequences exactly.
7. Live Office checks validate structure without requiring exact provider prose.
8. Evaluator reports contain no source filename, extracted text, annotation content, prompt,
   provider payload, or raw Markdown.

Implement:

1. Independently implement the accepted metric behavior using structured annotations.
2. Separate metric calculation from threshold evaluation.
3. Require every policy metric and category to emit a result or explicit unresolved status.
4. Use a structural Office checklist appropriate for the two real tuning and two real holdout
   documents; do not invent aggregate statistical claims from this small sample.
5. Produce a machine-readable safe aggregate result plus a human-readable summary.
6. Include evaluator version, policy digest, candidate commit, package version, split counts, model
   identity, and environment identity in the safe result.

Verification:

```bash
uv run --frozen pytest tests/test_benchmark_quality.py -q
uv run --frozen ruff check benchmarks tests/test_benchmark_quality.py
uv run --frozen ty check benchmarks tests/test_benchmark_quality.py
```

Exit: synthetic fixtures prove metric math, structural assertions, threshold enforcement, and
privacy-safe reporting.

## T04 - Add Private Runners and Aggregate Evidence

Depends on: T02, T03.

Files:

- Add `benchmarks/document_parsing/run_quality.py`.
- Add `benchmarks/document_parsing/render_evidence.py`.
- Add `tests/test_benchmark_runner.py`.
- Extend `benchmarks/document_parsing/README.md`.
- Later add `docs/releases/v0.1.0-evidence.md` from safe aggregate results.

Test first:

1. The runner validates the complete manifest before opening any source.
2. Tuning must finish before policy freeze; holdout refuses an unfrozen or mismatched policy.
3. Candidate commit, version, policy digest, manifest digest, replay identity, and model identity
   are bound to the run.
4. A changed source, candidate, policy, or evaluator invalidates cached evidence.
5. Failed/incomplete runs cannot be rendered as passing.
6. Raw artifacts stay below an ignored run directory.
7. Public evidence rendering consumes only the safe aggregate record and rejects content-bearing
   fields.
8. Logs redact local paths and never print source text, credentials, prompts, or provider payloads.

Implement:

1. Provide explicit `tuning`, `freeze`, and `holdout` modes.
2. Make holdout mode read-only with respect to thresholds and annotations.
3. Fail closed on missing categories, contaminated items, incomplete results, or environment
   mismatch.
4. Write raw run data only to an ignored caller-selected workspace.
5. Render `docs/releases/v0.1.0-evidence.md` only after all required safe aggregates pass.
6. Keep live provider execution opt-in and visibly separate from replay.

Verification:

```bash
uv run --frozen pytest tests/test_benchmark_runner.py -q
uv run --frozen python -m benchmarks.document_parsing.run_quality --help
```

Exit: the private process is repeatable, contamination-aware, and incapable of silently publishing
private artifacts.

## T05 - Add Resource and Lifecycle Gates

Depends on: T02. Coordinate evidence shape with T04.

Files:

- Add `benchmarks/document_parsing/evaluate_resources.py`.
- Add `tests/test_benchmark_resources.py`.
- Extend `tests/test_runtime.py`, `tests/test_api.py`, and format-specific parser tests only where
  coverage is missing.

Test first:

1. At `vision_concurrency=1` and `4`, one parse never exceeds its configured active visual calls.
2. Two simultaneous documents may each use their own limit; no hidden global semaphore serializes
   them.
3. A caller-owned semaphore bounds the number of simultaneous parse calls.
4. Timeout and cancellation during native work reap the worker and any Poppler descendants.
5. Timeout and cancellation during queued/active visual work prevent new calls and await active
   cleanup.
6. Success, failure, timeout, and cancellation remove owned workspaces and temporary sources.
7. The resource runner always has a timeout and fails on incomplete observations.
8. Safe resource results record environment and measurements but contain no private content.

Implement:

1. Reuse the production parser, real Poppler, and controlled fake/replay vision.
2. Add only missing lifecycle assertions; do not redesign runtime behavior that is already covered.
3. Observe process/resource state with standard platform facilities or development tooling, not a
   new runtime dependency.
4. Report elapsed time and available peak resource measurements as evidence.
5. Do not encode these observations as a public SLA or model-cost budget.

Verification:

```bash
uv run --frozen pytest tests/test_benchmark_resources.py tests/test_runtime.py \
  tests/test_api.py -q
```

Exit: the accepted envelope completes without OOM or leaks and enforces only the documented
per-parse visual concurrency.

## T06 - Harden Package and Artifact Boundaries

Depends on: T01.

Files:

- Extend `tests/test_package.py`.
- Add `scripts/check_release_artifacts.py`.
- Add `scripts/release_smoke.py`.
- Add `tests/test_release_scripts.py`.
- Modify packaging configuration only if tests reveal an actual omission.

Test first:

1. Artifact checker requires exactly one wheel and one source distribution with matching
   `0.1.0` metadata.
2. Wheel/sdist names, METADATA/PKG-INFO, Python requirement, dependencies, classifiers, license,
   README, and typing marker are correct.
3. Reject archives containing private corpus/manifests, raw benchmark data/output, credentials,
   caches, VCS files, application coupling, or unexpected top-level packages.
4. Generate and verify SHA-256 checksums.
5. Install wheel and sdist independently and run import, metadata, TXT/Markdown, native PDF,
   DOCX, and PPTX smoke.
6. Native smokes require neither model credentials nor outbound network.
7. Image/visual smoke uses controlled configuration and cannot accidentally charge a provider in
   public CI.

Implement:

1. Use standard archive/metadata APIs for artifact inspection.
2. Make smoke scripts runnable from an isolated environment without importing repository source.
3. Keep scripts dependency-light and exclude them from the runtime package unless needed.
4. Produce a checksums file consumed unchanged by the release job.

Verification:

```bash
uv run --frozen pytest tests/test_package.py tests/test_release_scripts.py -q
uv build
uv run --frozen python scripts/check_release_artifacts.py dist
```

Then install each artifact into a fresh temporary virtual environment and run
`scripts/release_smoke.py`.

Exit: both distribution types are complete, identical in version/contract, isolated-installable,
and free of prohibited artifacts.

## T07 - Expand CI to Ubuntu and macOS

Depends on: T01. T05/T06 tests become required as they land.

Files:

- Modify `.github/workflows/ci.yml`.
- Extend workflow tests in `tests/test_package.py` or add
  `tests/test_workflow_configuration.py`.
- Update contributor documentation.

Test first:

1. Parse workflow YAML and assert the supported matrix includes Ubuntu/macOS and Python
   3.11/3.12/3.13.
2. Assert Ubuntu installs `poppler-utils` and macOS installs Poppler.
3. Assert tests, Ruff, formatting, ty, build, artifact check, and isolated smoke remain required.
4. Assert fake/replay credentials cannot fall through to a real provider.
5. Assert third-party actions use immutable commit SHAs.

Implement:

1. Preserve one canonical CI workflow and matrix unless measured runtime justifies separate jobs.
2. Install Poppler explicitly on both operating systems.
3. Run the full public suite on every supported Python/OS pair.
4. Build distributions once in a designated job and run artifact smoke on both operating systems.
5. Upload only checked distributions and safe logs.
6. Use concurrency cancellation for superseded branch/PR runs, not release-tag jobs.

Verification:

```bash
uv run --frozen pytest tests/test_workflow_configuration.py -q
```

Push the implementation through a branch/PR and require every supported matrix job to pass before
T10.

Exit: Ubuntu and macOS are equally release-blocking on Python 3.11-3.13, with real Poppler
available.

## T08 - Add Independent Consumer and Public Documentation

Depends on: T01, T06.

Files:

- Add `examples/basic_consumer/pyproject.toml`.
- Add `examples/basic_consumer/main.py`.
- Add `examples/basic_consumer/README.md`.
- Add `tests/test_basic_consumer.py`.
- Modify `README.md` and `CONTRIBUTING.md`.
- Extend `CHANGELOG.md`.

Test first:

1. Example project depends on released `opendocs-sdk`, not repository internals or a path
   dependency.
2. Sync mode parses one local file to Markdown.
3. Async mode parses multiple local files with a caller-owned `asyncio.Semaphore`.
4. Example rejects/does not demonstrate URLs.
5. The semaphore bounds documents, while each parse retains its own `vision_concurrency`.
6. Example adds no OpenDocs runtime dependency or `43x-agent`/LangChain coupling.
7. README/package claims match Alpha status, supported OSes, formats, and limitations.
8. Documentation contains no fixed performance SLA or model-call/token/currency limit.

Implement:

1. Keep the example small and executable.
2. Accept local input paths and an output directory; write returned Markdown as a consumer concern.
3. Explain provider setup only for formats/paths that require vision.
4. Explain Poppler installation on Ubuntu and macOS.
5. Clearly distinguish within-document visual concurrency from caller-controlled document
   concurrency.
6. Replace unpublished-package wording only at the actual release stage; before T12, use a
   release-candidate marker or stage the final documentation change in the release commit.

Verification:

```bash
uv run --frozen pytest tests/test_basic_consumer.py -q
```

Install the locally built wheel into the example environment and execute sync and async modes using
public synthetic/local fixtures.

Exit: a consumer can use the built artifact without repository internals and the public
documentation makes only evidence-backed Alpha claims.

## T09 - Implement the Protected Release Workflow

Depends on: T06, T07, T08.

Files:

- Add `.github/workflows/release.yml`.
- Extend `tests/test_workflow_configuration.py`.
- Add `docs/releases/README.md` if needed.

Test first:

1. Workflow accepts only a `v*` tag and rejects a tag/version mismatch.
2. Release commit must be reachable from `origin/master`.
3. Build happens once; downstream TestPyPI, PyPI, smoke, and Release jobs download the same
   immutable artifacts.
4. Every third-party action is pinned to a full commit SHA with an upstream-version comment.
5. Default permissions are read-only; only publication gets `id-token: write`; only GitHub Release
   creation gets required contents permission.
6. Public PyPI publication uses environment `pypi` and no password/token secret.
7. TestPyPI uses its own exact trusted-publisher binding and cannot publish to production.
8. PyPI depends on artifact checks, TestPyPI install, and required evidence.
9. GitHub Release depends on public PyPI install smoke.
10. Release jobs do not expose credentials or private evidence.

Implement:

1. Trigger from protected version tags created from `master`.
2. Verify tag, package version, source commit, clean generated metadata, and evidence digests.
3. Build wheel/sdist once and generate checksums/provenance.
4. Publish the same artifacts to TestPyPI through OIDC and install them from TestPyPI.
5. Pause production publication at GitHub Environment `pypi`.
6. Publish the same artifacts to public PyPI through OIDC.
7. Install exact public version on clean Ubuntu/macOS jobs using only the public index.
8. Publish the GitHub Release only after those jobs pass.
9. Never automatically replace, delete, or overwrite an accepted public version.

Verification:

```bash
uv run --frozen pytest tests/test_workflow_configuration.py \
  tests/test_release_scripts.py -q
```

Review the resolved workflow permissions and environments in GitHub before creating a release tag.

Exit: the repository has a least-privilege, build-once, approval-gated release workflow with no
long-lived PyPI credential.

## T10 - Freeze and Verify the Release Candidate

Depends on: T00-T09.

Files:

- Add `docs/releases/v0.1.0-evidence.md` from safe aggregate records.
- Finalize `CHANGELOG.md`, `README.md`, and `docs/roadmap.md` release-candidate wording.
- Do not commit private manifests, documents, annotations, payloads, or raw output.

Steps:

1. Confirm the candidate is based on current `origin/master` and the worktree contains only
   intended release changes.
2. Run all public commands on the candidate.
3. Run M2 approved acceptance.
4. Run PDF/image tuning, freeze policy, then run the independent holdout.
5. Run the real Office tuning documents and the unseen DOCX/PPTX holdout.
6. Run accepted replay/live gates and the resource evaluator.
7. Render and manually inspect the safe aggregate evidence.
8. Build once locally and run wheel/sdist isolation smokes on both supported platforms through CI.
9. Search tracked changes and artifacts for prohibited paths/content and secrets without printing
   secret values.
10. Record the candidate commit, version, policy/evaluator digests, artifact hashes, and CI run.
11. Freeze the candidate. Any code, prompt, dependency, policy, or relevant documentation change
    requires rerunning affected gates and producing new evidence.

Verification:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src tests benchmarks
uv run --frozen pytest -q
uv build
uv run --frozen python scripts/check_release_artifacts.py dist
git diff --check
```

Exit: every public/private gate passes against one identified candidate, with safe aggregate
evidence ready for release.

## T11 - Configure Trusted Publishers and Rehearse

Depends on: T09, T10.

External configuration:

1. Create/verify GitHub Environment `testpypi` for rehearsal.
2. Create/verify GitHub Environment `pypi` with required manual approval.
3. Keep the sole maintainer eligible to approve; do not enable prevent-self-review without a
   second eligible reviewer.
4. Configure exact pending Trusted Publisher records on TestPyPI and PyPI:
   repository owner/name, workflow filename, and environment must match.
5. Confirm `opendocs-sdk` remains available on public PyPI immediately before the first publish.
6. Run a TestPyPI rehearsal from a non-production release candidate or workflow dispatch path that
   cannot reach public PyPI.
7. Install the rehearsed artifact in clean Ubuntu/macOS environments and run release smoke.
8. Confirm attestations, metadata, and checksums are visible and match the candidate.

Do not add a long-lived `PYPI_TOKEN` fallback. A Trusted Publisher mismatch blocks release and must
be corrected at the publisher/workflow binding.

Exit: TestPyPI rehearsal passes and the production environment is correctly bound but has not yet
published.

## T12 - Publish `0.1.0` and Complete M3

Depends on: T10, T11.

This task includes irreversible external publication. Confirm the exact tag, commit, artifact
hashes, and final release notes, then require explicit approval at the GitHub `pypi` Environment.

Steps:

1. Reconfirm package-name availability and that no `0.1.0` project/version conflict appeared.
2. Create `v0.1.0` from the frozen commit on `master`.
3. Observe the release workflow through build, artifact checks, and TestPyPI rehearsal.
4. Compare workflow artifact hashes with T10 evidence.
5. Approve the `pypi` environment only when every preceding gate is green.
6. Verify public PyPI exposes `opendocs-sdk==0.1.0` with the expected metadata and attestations.
7. Let clean Ubuntu/macOS jobs install only `opendocs-sdk==0.1.0` from public PyPI and run smoke.
8. Publish GitHub Release `v0.1.0` with the exact artifacts, checksum file, changelog, and safe
   evidence summary.
9. Verify tag, Release, package metadata, artifact hashes, and source commit all agree.
10. Update the roadmap status to released only after public smoke succeeds.

If publication partially succeeds, do not delete or overwrite the public version. Preserve
evidence, diagnose the exact failed downstream step, and use a new version for any artifact change.

Exit: the public package, public-index smoke, tag, and GitHub Release all exist for one exact source
and artifact set. M3 is complete.

## Final Verification Checklist

- [ ] M2 checklist approved and required private M2 gates pass.
- [ ] Public suite, lint, format, types, build, and artifact checks pass.
- [ ] PDF/image split is 30 tuning and 30 independent holdout pages across six categories.
- [ ] Office split contains two tuning and two independent holdout documents.
- [ ] Threshold policy was frozen before holdout and not modified afterward.
- [ ] Quality and Office structural gates pass with safe aggregate evidence.
- [ ] Resource/lifecycle gates pass without leaks or hidden global throttling.
- [ ] Ubuntu/macOS Python 3.11-3.13 CI is green with Poppler.
- [ ] Wheel and source distribution install independently and contain no prohibited artifacts.
- [ ] External consumer passes against the artifact.
- [ ] README, changelog, metadata, evidence, and roadmap use consistent Alpha claims.
- [ ] TestPyPI rehearsal passes through Trusted Publishing.
- [ ] Production publish requires the `pypi` Environment approval and uses no long-lived token.
- [ ] Public PyPI exact-version smoke passes on Ubuntu and macOS.
- [ ] Tag and GitHub Release `v0.1.0` match the published commit and artifact hashes.
- [ ] No private documents, annotations, provider payloads, raw output, credentials, or
  authorization record were committed.

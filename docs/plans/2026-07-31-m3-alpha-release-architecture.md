# M3 Alpha Release Architecture

Status: accepted plan; implementation not started
Date: 2026-07-31
Scope: release evidence, independent quality holdout, supported-platform CI, packaging, and the
first public Alpha release

## Objective

M3 turns the implemented OpenDocs parsers into a verifiable public Alpha package. It does not add
new parsing formats or redesign the SDK. The milestone establishes evidence that the current TXT,
Markdown, PDF, image, DOCX, and PPTX behavior is installable, deterministic, resource-bounded under
the documented test envelope, and releasable from a protected source revision.

M3 is complete only when all of the following are true:

1. The M2 local acceptance checklist has been approved by the maintainer.
2. Public CI and the private tuning/holdout gates pass for the release candidate.
3. `opendocs-sdk==0.1.0` is published to the public PyPI index.
4. A clean public `pip install opendocs-sdk==0.1.0` smoke test passes on Ubuntu and macOS.
5. The `v0.1.0` GitHub tag and public GitHub Release exist for the exact published source.

A build that is merely ready to publish does not satisfy M3.

## Current Baseline

At plan acceptance:

- `pyproject.toml` already declares `opendocs-sdk` version `0.1.0` for Python 3.11 and newer.
- M1 PDF/image implementation and its replay/live acceptance infrastructure are present.
- M2 DOCX/PPTX implementation and its checklist/replay/live infrastructure are present.
- M2 milestone acceptance is still pending the maintainer-approved local checklist.
- public CI runs Python 3.11, 3.12, and 3.13 on Ubuntu, but does not yet cover macOS.
- the README still describes the package as unpublished.
- the public suite passes with 471 tests, 9 opt-in skips, and one expected ZIP warning.
- there is no Git tag or release workflow.

These facts describe the starting point, not the M3 exit state.

## Accepted Product Contract

### Release Positioning

Version `0.1.0` is the first public Alpha:

- add `Development Status :: 3 - Alpha` to package metadata;
- describe it as suitable for evaluation and controlled use;
- document supported formats, model requirements, Poppler requirements, and known limitations;
- do not claim production readiness, enterprise readiness, broad external adoption, or
  large-scale operating guarantees.

### Supported Platforms

The supported M3 matrix is:

| Operating system | Python versions | Release blocking |
| --- | --- | --- |
| Ubuntu on GitHub-hosted `ubuntu-latest` | 3.11, 3.12, 3.13 | yes |
| macOS on GitHub-hosted `macos-latest` | 3.11, 3.12, 3.13 | yes |
| Windows | unverified | no |

Both supported operating systems must install Poppler and run source tests plus built-artifact
installation and basic parsing gates. Ubuntu installs `poppler-utils`. macOS installs Poppler
through the runner's package manager.

Windows is neither declared supported nor declared broken. Documentation must call it unverified,
and Windows failures do not block `v0.1.0`.

### Public Compatibility

The following interfaces remain compatible throughout the `0.1.x` line:

- `parse()` and `aparse()`;
- `ParseOptions` and `VisionConfig`;
- public input types and exception classes;
- Markdown string return values.

For the same package version, input, and configuration, semantic ordering and native parsing output
must be deterministic. Exact vision wording may vary by provider response, and exact Markdown may
improve between patch releases. The project does not promise byte-identical output across versions.

Private parser models, benchmark schemas, and internal orchestration may change without a public
compatibility promise. A public breaking change requires version `0.2.0` or later and migration
notes.

### Concurrency and Cost

`ParseOptions.vision_concurrency`, whose default is 4, limits visual requests within one parse. It
does not limit the number of documents being parsed by the application. Cross-document concurrency
belongs to the caller, as shown by the external consumer example.

M3 does not introduce:

- a process-wide semaphore;
- a global concurrency singleton;
- a public `max_vision_calls` option;
- a model-call, token, or currency budget.

Documentation must still warn that visual parsing can make paid model calls and that callers should
choose their own document-level concurrency and provider controls.

### Performance

M3 publishes reproducible resource evidence, not a public performance SLA. It does not promise a
maximum parse duration, fixed memory ceiling, throughput target, or document-size service level.

The release candidate must nevertheless pass the selected test envelope without:

- an out-of-memory failure;
- a leaked native worker;
- a leaked Poppler process;
- a leaked temporary source or parse workspace;
- model work continuing after cancellation or timeout;
- concurrency exceeding the configured per-parse visual limit.

The evidence report records the environment, inputs, concurrency, elapsed time, peak resource
observations, and cleanup result so later releases can detect regressions.

## Explicit Non-Goals

M3 excludes:

- formal Windows support;
- new document formats;
- a Node.js SDK;
- a CLI or hosted parsing service;
- a public structured/JSON result schema;
- a dependency-extras redesign;
- global cross-document concurrency control;
- model cost limits;
- a public performance SLA;
- production-readiness or enterprise-readiness claims.

These remain future work only when demonstrated demand and a separately accepted design justify
them.

## Release Evidence Architecture

M3 uses four evidence layers. No single layer substitutes for another.

```text
public source tests and artifact checks
                 |
                 v
private tuning and replay/live checks
                 |
                 v
independent holdout and resource checks
                 |
                 v
protected publish, public install smoke, GitHub Release
```

### Layer 1 - Public Repository Evidence

Public CI proves behavior that can be reproduced without private documents or provider credentials:

- unit and integration tests;
- synthetic parser fixtures;
- deterministic fake-vision and replay behavior;
- cancellation, timeout, cleanup, and per-parse concurrency behavior;
- Ruff, formatting, and ty checks;
- source distribution and wheel construction;
- metadata, manifest, and dependency inspection;
- isolated installation from built wheel and source distribution;
- basic TXT, Markdown, native PDF, image, DOCX, and PPTX smoke paths.

Synthetic Office fixtures cover the supported feature matrix but do not count as real holdout
documents.

### Layer 2 - Private Tuning Evidence

Tuning data may be inspected while implementation and thresholds are adjusted.

- PDF/image tuning uses 30 sanitized pages spanning the six accepted document categories.
- The existing M2 DOCX and PPTX acceptance documents form the Office tuning set.
- Replay and explicitly approved live model runs may be used here.

Tuning results determine and freeze release thresholds before the holdout is opened. The threshold
file records metric names, aggregation, handling of unresolved annotations, and required pass
conditions. It must not contain private content or model output.

### Layer 3 - Independent Holdout Evidence

Holdout data is evidence against overfitting:

- PDF/image holdout uses 30 unseen sanitized pages across the same six categories.
- No source document may contribute pages to both PDF/image tuning and holdout.
- Office holdout contains one unseen real DOCX and one unseen real PPTX.
- An Office document may not appear in both tuning and holdout.

If a holdout document is inspected for implementation tuning, threshold calibration, or prompt
changes, it is reclassified as tuning and replaced with a genuinely unseen document before
release. A holdout failure may be diagnosed, but any change made from that diagnosis requires a new
holdout run with uncontaminated replacement data.

The minimum real Office corpus for M3 is therefore four documents:

| Split | DOCX | PPTX |
| --- | ---: | ---: |
| Tuning | 1 existing M2 document | 1 existing M2 document |
| Holdout | 1 new unseen document | 1 new unseen document |

The tuning and holdout files, annotations, model payloads, and raw parse outputs remain local and
ignored.

### Layer 4 - Public Release Evidence

The final layer proves that the publicly named artifact, not only a local build, works:

- the tag points to the reviewed release candidate on `master`;
- tag and package versions match exactly;
- the release workflow builds distributions once from the tagged source;
- the exact checked artifacts are promoted to public PyPI;
- PyPI reports version `0.1.0`;
- clean Ubuntu and macOS jobs install that exact public version without repository source present;
- the installed package passes import, metadata, and basic parsing smoke tests;
- the GitHub Release publishes the checked distributions, checksums, release notes, and evidence
  summary.

## Benchmark Data Contract

### PDF and Image Quality

The PDF/image track independently implements the behavior of the established 60-page process from
the local `43x-agent` reference:

- 30 tuning pages and 30 holdout pages;
- six content categories represented in both splits;
- at least five pages from each category in each split;
- source-document isolation across splits;
- annotations for native/scanned text, native/visual tables, visual content, and unresolved cases.

The evaluator reports, at minimum:

- native and scanned text accuracy;
- native and visual table-cell accuracy;
- visual-content recall and precision;
- unnecessary visual-call rate on native content;
- unresolved annotation count and disclosure.

Exact metric formulas and thresholds are versioned in the public benchmark policy. Thresholds are
calibrated only on tuning data, frozen before holdout, and applied unchanged to holdout. An
aggregate average cannot hide a required category or metric failure.

### Office Quality

The deliberately small real Office corpus uses structural pass/fail evidence rather than pretending
to support statistically meaningful aggregate quality scores.

Each real DOCX/PPTX run checks:

- required native text anchors are present;
- paragraphs, tables, slides, shapes, and embedded images retain semantic source order;
- expected structural boundaries and tables are present;
- unsupported meaningful content is disclosed by bounded warnings;
- repeated native and replay runs are deterministic;
- live visual output satisfies structural assertions without snapshotting provider prose.

Synthetic Office fixtures retain exhaustive feature-level coverage in the public suite.

### Private Manifest and Public Aggregate

The ignored local manifest stores:

- opaque source identifier and SHA-256;
- split and category;
- expected annotations or checklist reference;
- provider/model/replay identity when applicable;
- evaluator-policy version.

It must not store credentials. The public aggregate evidence stores only:

- release candidate commit and package version;
- evaluator-policy version;
- split sizes and category counts;
- metric aggregates and pass/fail status;
- runtime environment and resource summary;
- unresolved-count disclosure.

No filenames, extracted text, annotations, prompts, model payloads, or raw generated Markdown enter
Git.

## Resource and Lifecycle Evidence

The resource evaluator exercises the production parser with real Poppler and controlled fake or
replay vision responses. It covers:

- configured visual concurrency of 1 and 4 within one document;
- caller-limited multi-document concurrency in the example or test harness;
- native worker timeout;
- cancellation during native work;
- cancellation during queued and active visual work;
- parser exceptions while other work is active;
- process and workspace cleanup after success and failure.

Use operating-system evidence available in CI without adding a runtime dependency. Linux may use
cgroup/process observations when available; macOS records compatible process and elapsed-time
observations. Platform-specific measurements are evidence fields, not cross-platform SLA values.

Every run must have an explicit timeout. A timed-out resource gate fails rather than silently
discarding measurements.

## Dependency and Distribution Boundary

M3 keeps current runtime dependencies as core dependencies. It does not add extras or new runtime
packages.

Distribution checks must prove:

- wheel and source distribution contain the intended `opendocs` package, typing marker, license,
  and public documentation required by package metadata;
- private corpus paths, local manifests, generated parse output, caches, credentials, benchmark
  raw data, and application-specific integrations are absent;
- imports succeed in an isolated environment;
- each declared runtime dependency is sufficient on both supported operating systems;
- native-only formats do not require a model credential or network call;
- the SDK still refuses remote URL inputs.

## External Consumer Example

Add an independent `examples/basic_consumer/` project that installs `opendocs-sdk` from PyPI. It
must demonstrate:

- synchronous single-file parsing;
- asynchronous batch parsing;
- a caller-owned semaphore that limits simultaneous documents;
- local-only input paths;
- Markdown output written by the consumer.

The example must not import repository internals, LangChain, `43x-agent`, application storage, or a
new OpenDocs runtime dependency. Before public publication, CI may install the locally built wheel;
the post-PyPI smoke installs the exact public version.

## Release Workflow and Security

### Trusted Publishing

Use PyPI Trusted Publishing through GitHub Actions. Do not create or store a long-lived
`PYPI_TOKEN`.

Configure:

- a GitHub Environment named `pypi`;
- required manual approval for that environment;
- a PyPI pending trusted publisher bound to the repository, exact release-workflow filename, and
  `pypi` environment;
- the minimal `id-token: write` permission only on the PyPI publication job;
- read-only repository permissions elsewhere unless an operation requires more.

The sole maintainer must remain able to approve the environment. Do not configure a rule that
forbids self-approval when no second eligible reviewer exists.

Pin third-party GitHub Actions to immutable commit SHAs and note the upstream release in comments.
Publishing must use a currently supported PyPA action version that uploads provenance attestations.

### Protected Release Sequence

1. Complete M2 approval, public CI, tuning, holdout, resource, and artifact gates on the release
   candidate.
2. Confirm the public PyPI project name is still available immediately before release.
3. Confirm `pyproject.toml` version is `0.1.0` and the candidate is reachable from `origin/master`.
4. Create signed or otherwise protected tag `v0.1.0` from that exact commit.
5. The tag workflow checks out the tagged commit and builds wheel and source distribution once.
6. Verify metadata, contents, checksums, installation, and TestPyPI rehearsal from those artifacts.
7. Pause at the `pypi` GitHub Environment for explicit approval of the irreversible public publish.
8. Promote the already verified artifacts to public PyPI through OIDC.
9. Run clean public-index installation smoke jobs on Ubuntu and macOS.
10. Publish the GitHub Release from the same tag with the same artifacts, checksums, notes, and
    evidence summary.
11. Update roadmap status only after public installation smoke passes.

The workflow must never rebuild different distributions after the approval point. If public
publication fails after PyPI accepts the version, do not delete or overwrite it; diagnose and
publish a new compliant version if correction is required.

The actual public publish remains an explicit human approval point during implementation even
though the overall M3 plan is accepted.

## Documentation Set

M3 updates:

- README installation, supported-platform, Alpha-positioning, model-cost, and Poppler sections;
- contributor instructions for public and private gates;
- `CHANGELOG.md` with `0.1.0` user-facing behavior and limitations;
- a release evidence document containing only safe aggregate results;
- the independent consumer example;
- roadmap status and links.

Documentation must distinguish:

- native parsing, which does not require provider credentials;
- visual parsing, which requires configured model access and may incur provider charges;
- per-document visual concurrency from caller-owned cross-document concurrency;
- public reproducible tests from private tuning/holdout evidence.

## Provenance and Privacy Decision

M3 does not add a NOTICE file or an authorization record. The repository's existing conservative
boundary remains authoritative:

- use the local `43x-agent` implementation as a behavioral and test-design reference;
- write OpenDocs implementation, tests, benchmark tools, and documentation independently;
- do not treat unrecorded authorization as justification for verbatim source reuse;
- never commit private documents, credentials, prompts containing private content, model payloads,
  raw generated output, deployment configuration, or application-specific coupling.

The release gate checks the distribution and Git diff for these prohibited artifacts. It does not
record private authorization.

## Failure Semantics and Release Blockers

The release is blocked by any of the following:

- M2 maintainer approval is absent;
- a public required job fails on a supported OS/Python combination;
- tuning thresholds were changed after holdout access;
- split isolation or manifest integrity fails;
- either unseen Office holdout document is missing;
- a required quality metric/category or Office structural check fails;
- cancellation, timeout, concurrency, or cleanup evidence fails;
- distribution contents or version/tag identity differ;
- TestPyPI installation fails;
- PyPI Trusted Publishing configuration is not exact;
- public PyPI installation smoke fails;
- release notes or evidence make claims not supported by the gates.

A flaky or unavailable external provider does not authorize skipping required live evidence.
Resolve the provider issue, use the accepted replay evidence where the gate explicitly permits it,
or delay the release.

## M3 Acceptance Criteria

- **AC-01:** The maintainer-approved M2 checklist and required replay/live evidence pass atomically.
- **AC-02:** PDF/image tuning and holdout each contain 30 pages across six categories with
  source-document isolation.
- **AC-03:** Office tuning contains the current DOCX/PPTX, and Office holdout contains one unseen
  DOCX/PPTX.
- **AC-04:** Split validation rejects duplicate hashes, cross-split source documents, missing
  categories, inspected holdout, and malformed annotations.
- **AC-05:** Frozen tuning thresholds pass unchanged on the independent PDF/image holdout.
- **AC-06:** Both Office holdout documents pass structure, ordering, warning, determinism, and
  accepted live/replay checks.
- **AC-07:** Resource tests prove bounded per-parse visual concurrency and complete cleanup under
  success, cancellation, timeout, and failure.
- **AC-08:** Public CI passes on Python 3.11-3.13 for Ubuntu and macOS with real Poppler smoke.
- **AC-09:** Wheel and source distribution contents, metadata, version, checksums, and isolated
  installs pass.
- **AC-10:** The independent consumer example works with the built artifact and then the exact
  public PyPI version.
- **AC-11:** Public metadata and documentation consistently position `0.1.0` as Alpha and state
  supported platforms and limitations.
- **AC-12:** The release workflow uses protected source, immutable action pins, Trusted Publishing,
  a `pypi` approval environment, least-privilege permissions, and build-once artifact promotion.
- **AC-13:** `opendocs-sdk==0.1.0` exists on public PyPI and clean Ubuntu/macOS installs pass.
- **AC-14:** GitHub tag and Release `v0.1.0` identify the exact published source and artifacts.
- **AC-15:** All public and private release evidence is complete without committing prohibited
  private data.

## References

- [OpenDocs Roadmap](../roadmap.md)
- [M1 PDF and Images Architecture](../archive/m1/2026-07-28-m1-pdf-images-architecture.md)
- [M2 Office Architecture](../archive/m2/2026-07-29-m2-office-architecture.md)
- [M2 Office Detailed Implementation Plan](../archive/m2/2026-07-29-m2-office-implementation.md)
- [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
- [PyPI pending publisher setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
- [Using a PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [PyPA GitHub Actions publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)

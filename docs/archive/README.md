# Archived Milestone Records

This directory contains completed or superseded planning records. Archiving preserves design
rationale, implementation context, and milestone evidence without presenting old task lists as
active work.

## About the Former `docs/superpowers` Directory

The former `docs/superpowers` directory was created by the Superpowers planning workflow used
during the initial M0 work. OpenDocs source code, packaging, tests, and CI do not depend on that
directory name.

Its two documents remain useful:

- the foundation design records the original public API, architecture, format strategy, and
  milestone rationale;
- the M0 implementation plan records the original test-first task sequence and code snapshots.

The implementation plan is not a current runbook. Its commands, paths, package metadata, and code
examples reflect the M0 repository state. Historical tree diagrams may still show the former
`docs/superpowers` layout.

## Contents

| Milestone | Document | Current value |
| --- | --- | --- |
| M0 | [Foundation and Roadmap Design](m0/2026-07-27-opendocs-foundation-and-roadmap-design.md) | Original accepted architecture and decision record |
| M0 | [Foundation Implementation Plan](m0/2026-07-27-opendocs-foundation.md) | Historical generated execution plan; do not execute as a current runbook |
| M1 | [PDF and Images Architecture](m1/2026-07-28-m1-pdf-images-architecture.md) | Implemented PDF/image contract and verification rationale |
| M2 | [Office Architecture](m2/2026-07-29-m2-office-architecture.md) | Implemented DOCX/PPTX design; maintainer acceptance remains pending |
| M2 | [Office Detailed Implementation Plan](m2/2026-07-29-m2-office-implementation.md) | Completed public tasks and the remaining private acceptance gate |

## Archive Rules

- Do not update archived documents to describe new implementation behavior.
- Add a short correction note only when a historical statement would otherwise be unsafe or
  materially misleading.
- Put active milestone plans in `docs/plans/`.
- Use [the roadmap](../roadmap.md) for current milestone status.
- Use [the documentation index](../README.md) as the stable entry point.

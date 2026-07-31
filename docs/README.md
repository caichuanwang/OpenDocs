# OpenDocs Documentation

Use this page as the documentation entry point.

## Current Work

- [Roadmap](roadmap.md) - milestone status and release sequence.
- [M3 Alpha Release Architecture](plans/2026-07-31-m3-alpha-release-architecture.md) - implemented
  repository contract with external release gates still pending.
- [M3 Alpha Release Detailed Implementation Plan](plans/2026-07-31-m3-alpha-release-implementation.md)
  - implemented T01-T09 sequence and pending T10-T12 release gates.
- [Release runbook](releases/README.md) - Trusted Publisher bindings and irreversible release order.

## Historical Records

[Archived milestone records](archive/README.md) preserve the decisions and implementation context
for the foundation and M0-M2 work. They are useful for design rationale and regression
investigation, but they are not current execution instructions.

M2 implementation is archived because its implementation work is complete. Its milestone
acceptance remains pending the maintainer-approved local checklist described by the roadmap and M3
preconditions.

## Authority

When documents disagree, use this order:

1. current source and tests for implemented behavior;
2. `docs/roadmap.md` for milestone status;
3. plans under `docs/plans/` for active work;
4. documents under `docs/archive/` for historical rationale only.

# App + Reviewer UI Roadmap

## Phase 0: Pilot tooling (now)

- CLI processing pipeline on local workstation.
- Rule file controls transformations and manual-review triggers.
- Reports drive deterministic review workflow.

## Phase 1: Institutional rulepack

- Move `rules/default_rules.json` to a versioned institutional rulepack.
- Add change control: owner, effective date, approval notes.
- Add per-department overrides when needed.

## Phase 2: Reviewer web UI

- Upload export package.
- Show grouped issues by module/page type.
- Allow reviewer decisions (`accept`, `replace`, `skip`) with audit trail.
- Rebuild package after reviewer decisions.

## Phase 3: Integration and automation

- Pull best-practices source from SharePoint/Teams export.
- Scheduled audit for drift/conflicts.
- Optional API integration with Canvas for post-import validation.

## Data model recommendation

- `rules`: transformation + validation rules with version IDs.
- `findings`: issue records with severity and auto/manual status.
- `decisions`: reviewer actions tied to findings and user identity.
- `runs`: immutable migration run metadata and artifacts.

## Non-negotiable engineering controls

- Deterministic transforms (same input + rules => same output).
- Full run artifact retention.
- Idempotent re-runs.
- Signed releases for rulepack versions.

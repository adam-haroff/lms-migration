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

## Phase 4: Post-migration automation (from coworker feedback, 2026-03-18)

Items sourced from documented manual pain points gathered from the migration team.
Priority order reflects frequency and full-automation potential.

- **Gradebook group + drop rules** — Read D2L category XML (drop-lowest/drop-highest
  settings) and recreate as Canvas assignment groups with correct drop configurations.
  Full automation possible; direct API mapping.

- **Prerequisite gating (syllabus quiz)** — Detect D2L "Not in Gradebook" flag and
  recreate assignment in Canvas with "Do not count towards final grade" checked while
  preserving point value for module prerequisite logic.

- **Extra credit / bonus assignments** — Detect D2L bonus flag → 0-point Canvas
  assignment placed in an appropriate group with a capping rubric. Flag for instructor
  verification.

- **Rubric migration and configuration** — Detect when D2L rubric ratings represent
  ranges rather than fixed values and enable the Range checkbox in Canvas. Auto-attach
  rubrics to corresponding assignments. Verify point totals match and flag discrepancies.

- **Faculty-only content** — Detect role-restricted D2L content; recreate as unpublished
  Canvas pages with `[FACULTY]` naming convention and an "INSTRUCTOR ONLY" warning header.

- **Discussion / assignment submission types** — Detect graded D2L content pages and
  recreate as Canvas Assignments (pages cannot be graded). Flag email-based workflows for
  conversion to online submission assignments with correct submission type.

- **LTI tool references** — Detect Panopto/Studio embeds and LTI links; map to Canvas
  equivalents using a configurable org-level lookup table; flag unresolvable references.

- **Blueprint-specific audit** — Before sync: flag ghost records in discussions (deleted
  replies), verify unpublished page state won't be overwritten, confirm gradebook
  structure and module prerequisites survive sync to child courses.

- **Item bank sharing** — After migration to Blueprint, auto-share all item banks at the
  course level so any enrolled instructor has edit access without manual per-bank sharing.

- **Accessibility (image alt text + post-import check)** — Improve round-trip alt text
  preservation. Run Canvas a11y checker via API post-import and surface results in the
  migration report.

---

## Non-negotiable engineering controls

- Deterministic transforms (same input + rules => same output).
- Full run artifact retention.
- Idempotent re-runs.
- Signed releases for rulepack versions.

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

## Phase 4 additions (from external research + codebase gap audit, 2026-03-21)

Items below were identified by cross-referencing (a) universally-reported D2L→Canvas
migration failure points, (b) codebase audit of what is and is not yet implemented,
and (c) the lms-migration-custom-instructions-draft and pdf-best-practices docs.
Confirmed non-redundant against existing pipeline capabilities before adding.

Each item feeds the existing `fix_checklist.py` instruction engine — every detected
condition emits a checklist item with `priority`, `owner`, `description`, and a
step-by-step `action` field that appears in the generated Markdown and CSV reports.

- **New Quizzes settings audit (pre-import)** — All quizzes are required to be Canvas
  New Quizzes. D2L QTI exports through Canvas import but loses per-quiz settings:
  timing limits, attempt counts, availability windows, and question randomization require
  manual re-entry. Parse D2L gradebook/quiz XML pre-import to produce a per-quiz
  settings inventory (name, time limit, attempts, shuffling, availability window).
  The fix checklist emits a P1 item per quiz with specific instructions for recreating
  each setting in New Quizzes — replacing the current unconditional boilerplate reminder.

- **New Quizzes compatibility review (per-quiz)** — Certain D2L question types behave
  differently or are unsupported in Canvas New Quizzes (e.g., ordering, multi-select
  with partial credit, calculated formula questions). Parse QTI XML to inventory question
  types per quiz and flag quizzes that contain at-risk types, with a P1 checklist item
  describing how to rebuild or substitute in New Quizzes before release.

- **Assignment / quiz / discussion availability window audit** — Due dates and
  availability windows are stored in D2L manifest and grade item XML, not in HTML.
  Nothing currently reads or reports on them. Parse these pre-import and emit a
  per-item checklist entry for every date-bearing object, giving the ID the exact
  original windows so they can be re-entered in Canvas. Feeds directly into the Canvas
  import date-shift workflow.

- **Course start-date and global date-shift report** — Canvas's import tool offers a
  bulk date shift, but requires knowing the original course start date. Read the D2L
  course offering XML for the official start date, surface it in the preflight report,
  and emit a P1 checklist item with exact Canvas import date-shift instructions. Also
  list every item that has explicit date windows so the ID can verify them after shift.

- **D2L media library URL detection (distinct category)** — The existing `/d2l/` link
  neutralizer catches media library URLs incidentally but treats them as generic
  "D2L link needs review." Media library URLs (patterns: `ouFileId=`,
  `/d2l/lp/media/`, `/d2l/tools/mediaLibrary/`) point to files that will be missing
  after import. These must be identified as a separate P1 category with a checklist
  action: move the file to Canvas Studio or course Files, update the embed/link.

- **Graded discussion detection** — D2L graded discussions sometimes import into
  Canvas as ungraded discussions, silently losing the gradebook connection. Parse
  `*_d2l.xml` discussion XML for grade category associations; emit a P1 checklist item
  per affected discussion with instructions to enable grading and reconnect to the
  gradebook in Canvas.

---

## Phase 4 additions (from BIS 1400 + ACC 2321 post-import cleanup, 2026-04-08)

These came from live-course cleanup after import, not just pre-import analysis. The
goal is to reduce the manual Canvas-side cleanup that still happened even when the
package imported successfully.

- **Canonical starter-template asset mapping** — Stop treating starter-template icons,
  banners, and shared support files as disposable package-local duplicates when a full
  starter shell is already being injected. Add a canonical asset manifest for the
  approved template course and rewrite generated page HTML to that one approved asset
  set. This should prevent duplicate `template-images`, duplicate `course-card` files,
  and later broken references caused by deleting the wrong copy in Canvas Files.

- **Clean output file structure matching live Blueprint practice** — Add a post-build
  package organization pass so generated files land in a predictable Sinclair-style
  layout from the start (for example `template-images`, `course_image`, course-specific
  content folders, and PowerPoints) instead of mixing template artifacts, D2L remnants,
  and generated content under `web_resources/` and scattered import folders. Preserve
  path safety, but emit the cleaner final structure by default when no collisions exist.

- **Template-aware description injection for assignments, discussions, and quizzes** —
  Extend the existing page templating logic so supported D2L discussions, assignments,
  and quiz descriptions are converted directly into the Canvas template wrappers during
  package generation. This should eliminate the current manual copy/paste step from
  generated snippets into the live course for common overview/instructions/technical
  support blocks.

- **Post-import duplicate/orphan audit** — Add an API-backed validation pass that flags:
  duplicate modules from accidental re-imports, duplicate pages by title/slug,
  published-but-unlinked pages, empty folders after file cleanup, duplicate template
  assets where only one is referenced, and legacy D2L carryover content that is still
  published. This should generate a cleanup report before manual review begins.

- **Course-image and template reserve handling** — Add a rule for course image files
  and template reserve assets (alternate banners, optional sample images) so the tool
  distinguishes between "unused but intentionally kept" and "safe to delete". This
  avoids over-cleaning template reserves while still identifying real leftovers.

- **Template shell course-number hydration** — When starting from the clean Canvas
  starter template, hydrate course-specific links and known template references with the
  destination course number before downstream content injection. Generated HTML should
  then reuse those resolved references consistently instead of introducing a second
  layer of template-file IDs later.

---

## Non-negotiable engineering controls

- Deterministic transforms (same input + rules => same output).
- Full run artifact retention.
- Idempotent re-runs.
- Signed releases for rulepack versions.

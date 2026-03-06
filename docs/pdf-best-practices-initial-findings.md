# Initial Findings From Provided PDFs

Sources:

- `/Users/adam.haroff/Desktop/Templated Course Set-Up Essentials Checklist.pdf`
- `/Users/adam.haroff/Desktop/Canvas Blueprints - Best Practices.pdf`

This is an initial machine-assisted pass to identify potential inconsistency/redundancy candidates before formal policy review.

## High-signal rules to enforce in the pilot

- Replace D2L-specific UI language in instructions and student directions.
- Run link validation and fix broken links before release.
- Rebuild or verify quizzes with item banks/question pools manually.
- Treat SCORM/H5P/Panopto embeds as manual-review-required.
- Apply accessibility checks on each page (headings, alt text, tables, link text).
- Keep module naming consistent and avoid ambiguous titles.

## Possible inconsistencies to resolve in one canonical standard

- `Quiz template wording`
- Content indicates old wording (`Take the Quiz`) and updated wording (`Begin` / `Submit`) with a note that post-pilot courses may not need the fix. Policy should explicitly define current canonical language and effective date.

- `Link opening behavior`
- Guidance says default to same-window links for accessibility and UX, while other instructions include per-link new-tab forcing. Policy should define when new-tab is mandatory vs prohibited.

- `Formatting standards`
- One section states spacing standards are not yet defined; other sections prescribe specific icon sizing and template expectations. Policy should identify what is required vs optional.

- `Legacy workaround status`
- Troubleshooting includes at least one issue marked as resolved. Active runbooks should separate current issues from historical notes to avoid unnecessary workaround steps.

## Redundancies to consolidate

- Repeated guidance to replace/remove broken `About the Instructor` links appears in multiple sections.
- Repeated reminders to remove D2L-specific language appear across checklist and best-practices sections.
- Repeated quiz migration caveats (timing, randomization, item bank behavior) can be consolidated into a single quiz migration SOP.

## Suggested policy structure

- `Required`: must pass before course release.
- `Recommended`: preferred, exceptions allowed.
- `Pilot-only`: temporary guidance with expiration date.
- `Deprecated`: retained only for historical context.

## Pilot implementation mapping

- Rule file: `rules/sinclair_pilot_rules.json`
- Migration command:

```bash
lms-migrate /path/to/d2l-export.zip --rules /Users/adam.haroff/Desktop/projects/codex/lms-migration/rules/sinclair_pilot_rules.json --output-dir /Users/adam.haroff/Desktop/projects/codex/lms-migration/output
```

- Review artifacts:
- `*.migration-report.md`
- `*.manual-review.csv`

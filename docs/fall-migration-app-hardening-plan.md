# Fall Migration App Hardening Plan

This plan is based on the repeated cleanup work from:
- `MAT 0200`
- `MAT 0470`
- `PSY 2180`

The goal is to reduce manual Canvas-side repair during the next migration round.

## Priority 1: Stabilize and Ship Current Local Work

- Review the current uncommitted app changes.
- Keep the changes that are already production-ready.
- Commit the safe subset.
- Isolate anything experimental or course-specific on a branch.

Why first:
- The current local changes already include useful work that should not remain in limbo.

## Priority 2: Strengthen Post-Import Automation

These are the next highest-value items because they remove repeated manual cleanup:

1. Template-page accessibility preset pack
2. New Quizzes asset reconciliation after import
3. Post-import checklist title sync to final Canvas item names
4. Post-import import-artifact cleanup for package source files
5. Link-validator triage classifier

Why this order:
- Summer-course cleanup showed that the package build is no longer the main bottleneck.
- The repeated time loss is happening after upload inside Canvas.

## Priority 3: Productize Bulk Canvas API Operations

Formalize the repeated post-import batch edits into supported features:

- bulk page body replacement by title/pattern
- bulk assignment-setting updates by naming convention
- bulk discussion / quiz description updates
- bulk publish/unpublish helpers
- live inventories for instructor notes, cleanup candidates, and unused files/pages

Why:
- These batch operations already proved their value in live courses.
- They should become reusable tools instead of one-off scripts.

## Priority 4: Improve Review and Finalization Workflows

After the post-import automation is stronger:

1. improve the Page Review workbench
2. improve link-validator triage output
3. improve overall UI/UX and responsive behavior

Why:
- The review interface is already useful, but still too expert-dependent.
- Broader UI redesign should follow workflow stabilization, not come first.

## Priority 5: Longer-Horizon Items

These are still worthwhile, but should not come before the items above:

- equation-image transcription assistant
- Panopto quickLink iframe conversion
- broader rubric migration/configuration
- deeper New Quizzes compatibility rebuild guidance
- richer LTI detection/resolution

## Definition of Success for the Fall Round

The next migration round should require less manual work in these areas:

- template-page accessibility fixes
- missing New Quiz files/images
- stale checklist wording after live renaming
- import-artifact cleanup in Canvas Files
- interpreting Canvas Link Validator output
- repetitive content renaming / reference synchronization

## Immediate Next Build Sequence

1. Commit the current safe local batch.
2. Finish and validate the template-page accessibility preset pack.
3. Finish and validate New Quizzes asset reconciliation after import.
4. Add checklist title sync using live Canvas module names.
5. Add conservative import-artifact cleanup with a dry-run report first.
6. Add link-validator triage classification output for CC handoff notes.

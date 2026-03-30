# Course Migration Runbook

Use this as the default per-course workflow for live migration work.

## Goal

Move one D2L export into a Canvas-ready package with the fewest possible manual passes:

1. classify the course quickly
2. review only the pages most likely to drift
3. preserve file paths during migration
4. leave faculty-content choices for handoff unless they block import quality

## Primary Outputs

After the pre-import pipeline finishes, work from these artifacts in this order:

1. `output/<course-code>/<zip-name>.kickoff-summary.md`
2. `output/<course-code>/<zip-name>.preflight-checklist.md`
3. `output/<course-code>/<zip-name>.manual-review.csv`
4. `output/<course-code>/<zip-name>.page-review-shortlist.csv`
5. `output/<course-code>/<zip-name>.page-review.html`

Use the kickoff summary to understand the course. Use the preflight and manual-review files to understand what still needs attention. Use the shortlist and workbench to decide which HTML pages actually need eyes on them.

## Template Assumption

The app now supports two template modes:

- default template merge: a **template-derived D2L package** with template-driven page shells and materialized `TemplateAssets/`
- full starter template shell: a **full starter-template package merge** that injects the starter shell modules/pages/resources into the generated cartridge

Use the full starter template shell mode when your Blueprint workflow expects the official starter template content to already be present in the import package.

Important nuance:

- full starter template shell adds the real starter-template modules, pages, quizzes, discussions, and `web_resources/` assets to the generated package
- `TemplateAssets/` may still exist in the package for overlay-generated icon/banner replacements on migrated D2L pages
- if you do **not** enable full starter template shell, the package remains a template-derived D2L package rather than a wholesale template-course import

## Pre-Import Workflow

### 1. Run the pre-import pipeline

In the UI, run the normal pre-import conversion flow for the D2L export zip. That produces the Canvas-ready zip plus the reports above.

For Sinclair Blueprint work:

- enable `Apply Template Overlay`
- enable `Apply Template Merge`
- enable `Include Full Starter Template Shell` if the Blueprint should receive the official starter template content as part of the generated package

### 2. Read the kickoff summary first

Open `*.kickoff-summary.md` before anything else.

It is the quickest way to see whether the course has:

- a course alignment document
- rubric-linked folders
- hidden or instructor-only content
- quiz-based release conditions
- quiz rebuild risks such as question banks, random order, or embedded media
- gradebook reconstruction risk
- scattered or duplicate file organization risk

If the kickoff summary is quiet, the course is probably straightforward. If it flags gradebook, quiz gating, question banks, or instructor-only content, plan for a slower review.

### 3. Use the preflight checklist for migration decisions

Open `*.preflight-checklist.md` next.

Treat it as the migration queue, not a faculty-content queue. Prioritize:

- broken or unresolved references
- quiz settings and release conditions
- gradebook structure
- file organization warnings that say to preserve paths
- accessibility issues that affect migration quality

Do not spend migration time reorganizing files in Canvas Files unless a broken reference forces it.

### 4. Use the manual-review CSV only for detail

Use `*.manual-review.csv` when you need the exact rows behind a checklist item, especially for:

- gradebook rules
- extra credit
- quiz details
- link/citation recovery
- course alignment references

## Page Review Workflow

### 1. Start with the shortlist CSV

Open `*.page-review-shortlist.csv` first.

Sort/filter by:

- `priority`
- `layout_risk_score`
- `content_loss_score`
- `review_focus`
- `why_flagged`

This is the fastest way to find the pages that actually deserve inspection.

### 2. Use the HTML workbench second

Open `*.page-review.html` after you know which pages matter.

Use the filters in this order:

1. `Layout Risk`
2. `Content Loss`
3. `Manual Fix`
4. `Accessibility`

Then narrow further with `Has Images`, `Has Tables`, `Has Accordions`, and `Has Iframes` if needed.

### 3. What the workbench is showing

The page review covers every HTML page found across the original and converted zip packages.

- The top sections in the Markdown and shortlist are triage views.
- The JSON and HTML workbench still include the full HTML page set.

### 4. How to interpret red divider (`hr`) behavior

The workbench now distinguishes between the exact converted page and the editor convenience layer:

- `Canvas Layout Preview` shows the converted package as-is.
- The `Approval Editor` may prepend a 10px red divider to pages without an icon heading so you can work with the template-style opener more easily.

That means:

- use `Canvas Layout Preview` when verifying whether top and bottom red dividers were actually generated
- use the `Dividers` metric on each page card to compare original vs converted divider counts
- use the shortlist columns `original_dividers` and `converted_dividers` to find pages where divider counts changed

If divider placement matters for a page, trust the compare preview and the divider metrics more than the editor surface.

### 5. What pages deserve review first

Review these before spending time on low-risk pages:

- pages with low preview similarity
- pages with image loss or table drift
- pages with accordion or iframe content
- pages with heavy layout-transform flags
- pages where divider counts changed unexpectedly
- pages with manual-fix and layout-risk together

## Post-Import Minimum Checks

After import to Canvas, do the minimum high-value verification pass:

1. run Canvas `Validate Links in Content`
2. use `Student View`
3. open:
   - home page
   - syllabus
   - one module intro/checklist page
   - one learning/resource page
   - one quiz
   - one assignment or external-tool item
4. verify gradebook weights/drop rules if the kickoff summary or manual review flagged them
5. verify module prerequisites/requirements if the kickoff summary flagged quiz gating

If the course uses faculty-only pages or unpublished setup content, keep those decisions in the handoff notes rather than trying to resolve them during migration unless they block the student-facing course.

## Practical Stop Rule

Stop improving a course when:

- the import package is structurally sound
- the high-risk pages are reviewed
- known quiz/gradebook/release-condition risks are documented or resolved
- the remaining work is primarily faculty-content review

That is the handoff boundary. Do not let low-value cleanup expand the migration pass.

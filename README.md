# LMS Migration Pilot (D2L -> Canvas)

Local-first tooling for a quick migration pilot:

- Process a D2L export zip and produce a Canvas-ready zip.
- Apply rule-driven HTML transformations for known platform/template gaps.
- Emit manual-review and accessibility issue reports.
- Audit a best-practices spreadsheet for duplicates/conflicts/redundancies.

No cloud upload is required. Processing runs locally on your machine.

## Why this pilot exists

A direct D2L export + Canvas import frequently leaves formatting drift, unsupported feature mappings, and accessibility cleanup work. This toolkit automates repetitive cleanup and produces a review queue.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Local UI (recommended)

Launch the desktop UI:

```bash
lms-migrate-ui
```

On macOS you can also double-click:

- `launch-ui.command`

The UI provides:

- Local ZIP migration with file pickers.
- Cleaner default layout with optional tools collapsed by default and advanced Canvas controls hidden behind a toggle.
- Split pipeline actions:
- `Run Pre-Import Pipeline` (local migration + reports)
- `Run Post-Import Pipeline` (Canvas issues export + fix checklist after Canvas import)
- `Run Full Post-Import` (pre issues + optional auto-relink + live audit/safe fixes + post issues + checklist)
- Course identifier fields (Canvas course ID + local Sinclair course code) to keep output paths organized.
- Sinclair course code history dropdown (remembers recent entries locally).
- D2L export ZIP history dropdown (remembers recent local paths).
- Policy profile selector (`strict` or `standard`).
- Optional Template Overlay stage (experimental) to map known Brightspace template refs to assets from a Canvas template package and materialize those template assets into the migrated package.
- Accordion handling mode selector (`details`, `flatten`, or `none`) during pre-import conversion.
- Optional best-practice enforcer toggle for safe template/process normalization during pre-import conversion.
- Non-sensitive summary generation (no file names, no content snippets).
- Optional best-practices spreadsheet audit.
- Optional reference-doc audit (instruction/template/best-practices comparison report).
- Optional Canvas import-issues export via API (token entered in UI; output saved locally as JSON).
- Optional post-import auto-relink action for missing page file/image links in Canvas.
- Optional live Canvas link audit with exportable JSON/CSV/Markdown findings and optional safe page-fix pass.
- Run status line + clear-log control for faster troubleshooting.
- A/B helper: one-click variant cycle (`pre issues -> optional auto-relink -> post issues`) with auto-managed output paths under `ab-test/<A|B>/` and variant-tagged filenames.
- Optional Canvas course snapshot capture (pages/modules/files/assignments/discussions/announcements) for gold-course comparison.
- Canvas sanitizer for common Brightspace template breakage (removes missing template assets, neutralizes legacy D2L links, and keeps review flags in reports).
- One-click fix-checklist builder (combines Canvas import issues with optional manual-review and reference-audit signals).

## 1) Run migration pipeline

```bash
lms-migrate /path/to/d2l-export.zip --rules rules/default_rules.json --output-dir output
```

Use a policy profile:

```bash
lms-migrate /path/to/d2l-export.zip --rules rules/sinclair_pilot_rules.json --policy-profile strict --output-dir output
```

Enable safe best-practice enforcement during pre-import processing:

```bash
lms-migrate /path/to/d2l-export.zip \
  --rules rules/sinclair_pilot_rules.json \
  --policy-profile strict \
  --best-practice-enforcer \
  --output-dir output
```

Include reference-audit alignment in migration report/checklist:

```bash
lms-migrate /path/to/d2l-export.zip \
  --rules rules/sinclair_pilot_rules.json \
  --policy-profile strict \
  --reference-audit-json output/reference_audit/reference-audit.json \
  --output-dir output
```

Institution-specific rulepack (derived from your provided checklist + best-practices PDFs):

```bash
lms-migrate /path/to/d2l-export.zip --rules rules/sinclair_pilot_rules.json --output-dir output
```

Enable template overlay mapping against a Canvas template package:

```bash
lms-migrate /path/to/d2l-export.zip \
  --rules rules/sinclair_pilot_rules.json \
  --template-package resources/examples/template/elearn-standard-template-export.imscc \
  --template-alias-map-json rules/template_asset_aliases.json \
  --output-dir output
```

Set accordion handling mode during migration:

```bash
lms-migrate /path/to/d2l-export.zip \
  --rules rules/sinclair_pilot_rules.json \
  --accordion-handling flatten \
  --output-dir output
```

Modes:

- `details`: Convert legacy Bootstrap accordion cards to accessible `<details>/<summary>` blocks.
- `flatten`: Convert accordion cards into plain heading/content sections.
- `none`: Leave legacy accordion markup unchanged.

Disable optional template transforms when needed:

```bash
lms-migrate /path/to/d2l-export.zip \
  --rules rules/sinclair_pilot_rules.json \
  --no-template-module-structure \
  --no-template-visual-standards \
  --output-dir output
```

Outputs:

- `output/<zip-name>.canvas-ready.zip`
- `output/<zip-name>.migration-report.json`
- `output/<zip-name>.migration-report.md`
- `output/<zip-name>.manual-review.csv`
- `output/<zip-name>.preflight-checklist.md`
- Optional: `output/<zip-name>.template-overlay-report.json`

Template overlay notes:

- Mapped template assets are materialized into `TemplateAssets/` inside the generated package.
- Common Brightspace framework CSS/JS references are tracked as `ignored_unresolved_total` in the overlay report because they are intentionally removed by sanitizer logic.

## 1b) Generate non-sensitive summary from report JSON

```bash
lms-safe-summary /path/to/course.migration-report.json
```

Outputs:

- `*.safe-summary.txt` with counts and issue-reason totals only.

## 1c) Visual HTML structure audit (original vs converted)

```bash
lms-visual-audit \
  --original-zip /path/to/d2l-export.zip \
  --converted-zip /path/to/d2l-export.canvas-ready.zip
```

Outputs:

- `*.visual-audit.json`
- `*.visual-audit.md`

## 2) Audit best-practices spreadsheet

Works with `.xlsx` or `.csv`:

```bash
lms-best-practices-audit /path/to/best-practices.xlsx --output-dir output
```

Optional sheet selection:

```bash
lms-best-practices-audit /path/to/best-practices.xlsx --sheet "Migration Rules" --output-dir output
```

Outputs:

- `output/<sheet-file>.best-practices-audit.json`
- `output/<sheet-file>.best-practices-audit.md`

## 3) Audit reference docs for app/process improvements

Compares instruction/best-practices/template docs against current draft guidance and rule coverage:

```bash
lms-reference-audit \
  --instructions-docx "/path/to/Customize ChatGPT for D2L to Canvas Migrations.docx" \
  --best-practices-docx "/path/to/Canvas Blueprints - Best Practices.docx" \
  --page-templates-docx "/path/to/Canvas Page Templates.docx" \
  --syllabus-template-docx "/path/to/Canvas Syllabus Page Template.docx"
```

Outputs:

- `output/reference_audit/reference-audit.json`
- `output/reference_audit/reference-audit.md`

## 4) Build a unified migration fix checklist

Combines Canvas import issues with optional manual-review and reference-audit signals:

```bash
lms-build-fix-checklist /path/to/canvas-migration-issues.json \
  --manual-review-csv /path/to/course.manual-review.csv \
  --reference-audit-json output/reference_audit/reference-audit.json \
  --output-dir /path/to/course-output
```

Outputs:

- `migration-fix-checklist.csv`
- `migration-fix-checklist.md`

## 4b) Auto-relink missing page links in Canvas (post-import)

```bash
lms-canvas-auto-relink \
  --base-url "https://sinclair.instructure.com" \
  --course-id <canvas-course-id> \
  --token "$CANVAS_TOKEN" \
  --issues-json output/<course-code>/canvas-migration-issues.json \
  --alias-map-json rules/template_asset_aliases.json \
  --output-json output/<course-code>/canvas-auto-relink-report.json
```

Outputs:

- `canvas-auto-relink-report.json`

Notes:

- Alias map is optional. Keep it disabled until you validate mappings in your sandbox.
- Example starter map: `rules/template_asset_aliases.json`

## 4c) Live Canvas link audit (with optional safe fixes)

```bash
lms-canvas-live-audit \
  --base-url "https://sinclair.instructure.com" \
  --course-id <canvas-course-id> \
  --token "$CANVAS_TOKEN" \
  --alias-map-json rules/template_asset_aliases.json \
  --output-json output/<course-code>/canvas-live-link-audit.json
```

Optional safe-fix mode (updates page HTML only):

```bash
lms-canvas-live-audit \
  --base-url "https://sinclair.instructure.com" \
  --course-id <canvas-course-id> \
  --token "$CANVAS_TOKEN" \
  --alias-map-json rules/template_asset_aliases.json \
  --apply-safe-fixes \
  --output-json output/<course-code>/canvas-live-link-audit.json
```

Outputs:

- `canvas-live-link-audit.json`
- `canvas-live-link-audit.md`
- `canvas-live-link-audit.csv`

## 4d) Snapshot a manually-converted Canvas course

```bash
lms-canvas-snapshot \
  --base-url "https://sinclair.instructure.com" \
  --course-id <canvas-course-id> \
  --token "$CANVAS_TOKEN" \
  --output-dir output/<course-code>
```

Outputs:

- `canvas-course-<course-id>.snapshot.json`
- `canvas-course-<course-id>.snapshot.md`

## 4e) Analyze example corpus (D2L + issues + snapshots)

```bash
lms-analyze-examples \
  --examples-dir resources/examples \
  --snapshot-root output \
  --output-dir output/examples
```

Outputs:

- `training-corpus-analysis.json`
- `training-corpus-analysis.md`

## 4f) Analyze template IMSCC compatibility against Brightspace refs

```bash
lms-analyze-template-package \
  --template-package resources/examples/template/elearn-standard-template-export.imscc \
  --examples-dir resources/examples \
  --output-dir output/examples
```

Outputs:

- `template-compatibility-analysis.json`
- `template-compatibility-analysis.md`

## Rule customization

Edit `rules/default_rules.json`:

- `replacements`: regex substitutions for HTML/text cleanup
- `link_rewrites`: host/path rewriting from D2L links to Canvas links
- `manual_review_triggers`: patterns that should force human review
- `banner`: optional template banner injection

If you are following the Sinclair pilot guidance from the provided PDFs, start with:

- `rules/sinclair_pilot_rules.json`
- `rules/policy_profiles.json`
- `docs/pdf-best-practices-initial-findings.md`

## Pilot workflow for one course due soon

1. Export from D2L (zip).
2. Run `lms-migrate` with institution rules.
3. Import generated zip into a Canvas sandbox course.
4. Use `manual-review.csv` as the checklist.
5. Run accessibility checks in Canvas and resolve remaining issues.
6. Promote into production course shell.

## Roadmap to full app + reviewer UI

See `docs/app-roadmap.md`.

## Security and policy considerations

See `docs/security-and-governance.md`.

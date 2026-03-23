# LMS Migration — Project Instructions for GitHub Copilot

This is a **Python 3.12 local-first pipeline** for migrating course content from
**D2L Brightspace → Canvas LMS**. There are NO runtime AI API calls — the pipeline
is entirely regex/rules-based transforms. Copilot built the initial codebase; this
file captures the architecture and decisions so every new conversation starts with
full context.

---

## Architecture

| Entry point       | Purpose                                      |
| ----------------- | -------------------------------------------- |
| `lms-migrate` CLI | Batch course conversion (primary use)        |
| `lms-migrate-ui`  | Tkinter GUI wrapper around the same pipeline |

### Key source files

| File                                          | Role                                                                                                                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lms_migration/html_tools.py`             | Core HTML transformation engine — ALL sanitization, accordion conversion, image/table processing, Bootstrap handling, LTI/media-library detection. ~2,400 lines. |
| `src/lms_migration/pipeline.py`               | Orchestrates the full conversion from zip-in to zip-out; contains all `_audit_*` XML helpers                                                                     |
| `src/lms_migration/fix_checklist.py`          | Maps every detected issue reason string → `(priority, category, owner, action)` tuple; drives preflight checklist generation                                     |
| `src/lms_migration/quiz_audit.py`             | Parses D2L QTI quiz XML; produces per-quiz inventory (timing, attempts, shuffling, availability windows)                                                         |
| `src/lms_migration/template_overlay.py`       | Maps Brightspace template icon refs → Canvas template assets                                                                                                     |
| `src/lms_migration/best_practice_enforcer.py` | Checks converted pages against best-practice rules                                                                                                               |
| `rules/default_rules.json`                    | Minimal rule set: font-family removal, D2L link rewriting                                                                                                        |
| `rules/sinclair_pilot_rules.json`             | Aggressive rule set used in pilot experiments                                                                                                                    |
| `rules/template_asset_aliases.json`           | 42 alias rules mapping old Brightspace icon filenames → Canvas equivalents                                                                                       |
| `rules/policy_profiles.json`                  | Named policy presets (strict, permissive, etc.)                                                                                                                  |

### Test courses

Primary test case: **ACC-2321** (`resources/incoming/acc-2321/`).
Expected output goes to `output/acc-2321/`.
Baseline: 87 HTML files, ~88% approval, 0 manual review issues, 1 a11y issue.

Other courses in `resources/incoming/`: agr-1208, bis-1400, com-2220, mat-0200, mat-0470,
psy-2180, psy-2235, vet-2111.

- **bis-1400, com-2220, psy-2235, psy-2180, vet-2111** — have `rubrics_d2l.xml`
- **mat-0200, vet-2111** — have D2L quickLink LTI iframes (`quickLink.d2l?type=lti`)
- **psy-2180** — has a Panopto iframe embed

Standard conversion command:

```bash
source .venv/bin/activate
lms-migrate resources/incoming/acc-2321/before/d2l-export.zip \
  --rules rules/default_rules.json \
  --policy-profile strict \
  --best-practice-enforcer \
  --template-package resources/examples/template/elearn-standard-template-export-20260316.imscc \
  --template-alias-map-json rules/template_asset_aliases.json \
  --output-dir output/acc-2321
```

Run tests:

```bash
.venv/bin/python -m pytest tests/ -v
# 257 tests across tests/test_html_tools.py and tests/test_new_audit_features.py
```

---

## html_tools.py — Key internals

### `apply_canvas_sanitizer(content, policy, *, file_path="")`

Main entry point. Returns `(html_str, list[AppliedChange])`. Controlled by a
`CanvasSanitizerPolicy` dataclass (all flags default `True`):

| Flag                           | Effect                                                                |
| ------------------------------ | --------------------------------------------------------------------- |
| `sanitize_brightspace_assets`  | Strips Brightspace CSS/JS refs; gates Bootstrap class processing      |
| `strip_bootstrap_grid_classes` | Removes Bootstrap tokens; requires `sanitize_brightspace_assets=True` |
| `neutralize_legacy_d2l_links`  | Rewrites `/d2l/` URLs                                                 |
| `normalize_divider_styling`    | Standardises `<hr>` styling                                           |
| `accordion_handling`           | `"smart"` (auto), `"details"`, `"flatten"`                            |

### `_convert_bootstrap_accordion_cards(content, mode, *, alignment="left")`

Converts Bootstrap card accordions. Pattern: `card > card-header + collapse > card-body`.

- **flatten mode**: emits `<h3>title</h3><div>body</div>` — skips heading if title is in
  `_ACCORDION_PLACEHOLDER_TITLES` (e.g. "section", "item", "content")
- **details mode**: emits `<details><summary>…</summary><div>…</div></details>`
- **smart mode**: chooses based on page hints (syllabus/policy → flatten, lesson/FAQ → details)

### LTI and media detection (in html_tools.py)

These run on **pre-sanitized** content in the pipeline (critical: the sanitizer neutralises
`/d2l/` hrefs to `#` before detection would find them):

| Function                                   | What it detects                                                                                    |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `detect_lti_embed_issues(content)`         | Panopto/Kaltura/YuJa iframes AND D2L quickLink LTI iframes/hrefs (`quickLink.d2l?type=lti`)        |
| `detect_d2l_media_library_embeds(content)` | `ouFileId=`, `/d2l/lp/media/`, `/d2l/tools/mediaLibrary/` — files that will be missing post-import |

**quickLink LTI pattern**: `/d2l/common/dialogs/quickLink/quickLink.d2l?ou=…&type=lti&rCode=sinclairc-…`
— appears as both `<iframe src>` and `<a href>` tags. Detected by `detect_lti_embed_issues`.
Emits reason: `"LTI tool embed (D2L QuickLink) — reconfigure as Canvas LTI external tool after migration"`.

### Important constants

- `_BOOTSTRAP_UTILITY_CSS_MAP` — 32-entry dict mapping utility class tokens to CSS properties.
  Applied as inline styles BEFORE class tokens are stripped so float/align/bg/padding survives.
- `_ACCORDION_PLACEHOLDER_TITLES` — frozenset of generic D2L template titles to suppress.
- `_BOOTSTRAP_GRID_CLASS_RE` — matches grid tokens (col-\*, row, container).
- `_BOOTSTRAP_UTILITY_CLASS_RE` — matches utility tokens (float-_, text-_, bg-_, m-_, p-\*).
- `_LEGACY_TEMPLATE_CLASS_RE` — matches Brightspace accordion classes (accordion, card, collapse, etc.).
- `_LTI_TOOL_DOMAIN_MAP` — dict of domain → tool-name for known LTI providers (Panopto, Kaltura, YuJa, etc.).

### Helper functions

| Function                                     | Signature                                                                     |
| -------------------------------------------- | ----------------------------------------------------------------------------- |
| `_merge_inline_style(tag_html, additions)`   | Merges CSS properties into a tag's `style` attr; returns `(new_tag, changed)` |
| `_remove_inline_style_keys(tag_html, keys)`  | Removes specific CSS property keys from `style`; returns `(new_tag, changed)` |
| `_extract_attr_value(tag_html, attr_name)`   | Returns attribute value or `None`                                             |
| `_plain_text(value)`                         | Strips HTML tags, returns plain text                                          |
| `_extract_accordion_title_text(header_html)` | Extracts title text from accordion header HTML                                |

---

## pipeline.py — XML audit helpers

These functions run inside `_append_xml_audit_rows_to_csv()` and emit rows to the
`d2l-export.manual-review.csv` with `type="d2l_xml_audit"`. Each row feeds `fix_checklist.py`.

| Function                        | Source file(s)          | What it audits                                                                        |
| ------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| `_audit_graded_discussions()`   | `*_discussions_d2l.xml` | Discussions with grade-category associations — may silently lose gradebook connection |
| `_audit_availability_windows()` | `*_grades_d2l.xml`      | Grade items with start/end date windows — need manual re-entry in Canvas              |
| `_audit_gradebook_groups()`     | `*_grades_d2l.xml`      | Assignment groups with drop-lowest/drop-highest rules and extra-credit (bonus) items  |
| `_audit_rubrics()`              | `rubrics_d2l.xml`       | Per-rubric inventory: name, criteria count, level count, scoring method, Range hint   |
| `_audit_date_shift_items()`     | course XML + grades XML | Course start-date advisory + items with explicit availability windows                 |

### `_audit_rubrics()` — details

D2L rubrics are NOT migrated by Canvas IMSCC import and must be recreated manually.

- Parses `rubrics_d2l.xml` schemaversion v2011
- `scoring_method`: `"3"` = custom points (per-criterion cell values), `"2"` = level-based
- When `scoring_method="2"` and cell_values are all empty: adds NOTE recommending Canvas "Range" option
- Emits reason: `"D2L rubric detected — recreate in Canvas and attach to assignment"`
- Evidence: `rubric: "Name" | N criteria | N levels | scoring label | status: active/archived/draft`

---

## fix_checklist.py — issue → checklist mapping

`_map_manual_review_group(issue_type, reason)` is the central dispatch. Returns
`(priority, category, owner, action)`. Key categories:

| Category                        | Priority | Trigger (in lowered reason)                                           |
| ------------------------------- | -------- | --------------------------------------------------------------------- |
| `lti_quicklink_reconfiguration` | P1       | `"d2l quicklink"` AND `"lti tool embed"` (checked BEFORE generic LTI) |
| `lti_embed_reconfiguration`     | P1       | `"lti tool embed"` (generic fallback for Panopto, Kaltura, etc.)      |
| `rubric_import_setup`           | P1       | `"d2l rubric detected"`                                               |
| `d2l_media_library_file`        | P1       | `"d2l media library"`                                                 |
| `graded_discussion_reconnect`   | P1       | `"graded discussion"`                                                 |
| `availability_window_reentry`   | P1       | `"availability window"`                                               |
| `gradebook_group_drop_rules`    | P1       | `"gradebook group"`                                                   |
| `extra_credit_setup`            | P1       | `"extra credit"` OR `"bonus"`                                         |
| `date_shift_planning`           | P1       | `"course start date"`                                                 |
| `quiz_settings_inventory`       | P1       | `"quiz settings"`                                                     |

**Handler order matters**: `lti_quicklink_reconfiguration` is checked before `lti_embed_reconfiguration`
so the D2L quickLink reason string doesn't fall through to the generic handler.

---

## quiz_audit.py — quiz settings inventory

`audit_quizzes(zip_path)` → `QuizAuditReport` with list of `QuizInfo`.

Each `QuizInfo` contains: `name`, `time_limit_minutes`, `time_limit_enforced`,
`attempts_allowed`, `shuffle_questions`, `shuffle_answers`, `start_date`, `end_date`.

Generates `d2l-export.quiz-audit.json` and `d2l-export.quiz-audit.md` reports.
Feeds into the pipeline's fix checklist as a P1 item with per-quiz settings inventory.

---

## Bug fixes applied (copilot-test session, 2026-03-18)

Five bugs were fixed in `html_tools.py`. All have regression tests in `tests/test_html_tools.py`.

| Bug                                      | Root cause                                           | Fix                                                            |
| ---------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------- |
| **Image layout destroyed**               | `align`/`hspace`/`vspace` stripped without CSS       | Convert to `float`/`margin` CSS before stripping               |
| **Bootstrap layout collapsed**           | Utility classes stripped without CSS replacement     | Promote to inline CSS via `_BOOTSTRAP_UTILITY_CSS_MAP` first   |
| **Spurious `<h3>Section</h3>` headings** | Flatten mode used "Section" fallback unconditionally | Skip heading when title is in `_ACCORDION_PLACEHOLDER_TITLES`  |
| **Fixed-pixel overflow**                 | 941px tables/images passed through unchanged         | Add `max-width:100%` to images; convert tables >500px to fluid |
| **Spacing loss**                         | All empty `<p>&nbsp;</p>` stripped                   | Only collapse runs of 3+ spacers; preserve singles/pairs       |

---

## Key exploration findings (2026-03-22)

From scanning all 9 course zip exports:

- **`not_in_gradebook`** is NOT present in D2L IMSCC exports (it's server-only). Prerequisite
  gating / "Do not count towards final grade" detection is **not feasible** from exports.
- **Role-restriction data** is not in any export. Faculty-only content detection is **not feasible**.
- **D2L quickLink LTI URLs** are the dominant LTI pattern: `quickLink.d2l?type=lti&rCode=sinclairc-…`
  They appear as `<iframe src>` (most common) and `<a href>` tags.
- **Rubric XML is standalone** — no foreign key from `_grades_d2l.xml` to rubric IDs.
  The `<rubric>` tags in quiz QTI XML are QTI feedback wrappers, NOT assessment rubric refs.
  Rubric-to-assignment correlation is not feasible from export data.

---

## Strategic decisions (confirmed by user)

1. **Template asset mapping** — Style Inference: parse Brightspace CSS/JS to derive canvas equivalents (Phase 3)
2. **Preview workflow** — Canvas Preview API: render in a real Canvas sandbox before upload (Phase 2)
3. **Layout preservation** — CSS Parser: parse inline styles to detect layout intent (Phase 2)

---

## Development conventions

- Virtual environment: `.venv/` (Python 3.12). Always activate before running.
- Package installed editable: `pip install -e .`
- Tests: `pytest` via `pyproject.toml` config. **257 tests** in `tests/test_html_tools.py`
  and `tests/test_new_audit_features.py`.
- Branch model: `main` is stable.
- No AI API calls at runtime — all transforms are local regex/rules.
- Output reports land in `output/<course-id>/`. Don't commit outputs.
- The 88% "approval score" measures structural metrics, NOT visual fidelity — treat it as a floor, not a ceiling.

---

## What needs doing next (Phase 2)

1. **`src/lms_migration/css_parser.py`** — Parse inline styles to detect layout intent
   (float, multi-column, positioned elements) and preserve them through Canvas import.
2. **`src/lms_migration/canvas_preview.py`** — Canvas Preview API integration:
   upload converted zip to a test Canvas sandbox and render pages for visual review
   before the instructor sees anything.

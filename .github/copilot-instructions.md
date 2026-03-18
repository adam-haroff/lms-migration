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

| File                                          | Role                                                                                                                                |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `src/lms_migration/html_tools.py`             | Core HTML transformation engine — ALL sanitization, accordion conversion, image/table processing, Bootstrap handling. ~2,300 lines. |
| `src/lms_migration/pipeline.py`               | Orchestrates the full conversion from zip-in to zip-out                                                                             |
| `src/lms_migration/template_overlay.py`       | Maps Brightspace template icon refs → Canvas template assets                                                                        |
| `src/lms_migration/best_practice_enforcer.py` | Checks converted pages against best-practice rules                                                                                  |
| `rules/default_rules.json`                    | Minimal rule set: font-family removal, D2L link rewriting                                                                           |
| `rules/sinclair_pilot_rules.json`             | Aggressive rule set used in pilot experiments                                                                                       |
| `rules/template_asset_aliases.json`           | 42 alias rules mapping old Brightspace icon filenames → Canvas equivalents                                                          |
| `rules/policy_profiles.json`                  | Named policy presets (strict, permissive, etc.)                                                                                     |

### Test course

Primary test case: **ACC-2321** (`resources/incoming/acc-2321/`).
Expected output goes to `output/acc-2321/`.
Baseline: 87 HTML files, ~88% approval, 0 manual review issues, 1 a11y issue.

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

### Important constants

- `_BOOTSTRAP_UTILITY_CSS_MAP` — 32-entry dict mapping utility class tokens to CSS properties.
  Applied as inline styles BEFORE class tokens are stripped so float/align/bg/padding survives.
- `_ACCORDION_PLACEHOLDER_TITLES` — frozenset of generic D2L template titles to suppress.
- `_BOOTSTRAP_GRID_CLASS_RE` — matches grid tokens (col-\*, row, container).
- `_BOOTSTRAP_UTILITY_CLASS_RE` — matches utility tokens (float-_, text-_, bg-_, m-_, p-\*).
- `_LEGACY_TEMPLATE_CLASS_RE` — matches Brightspace accordion classes (accordion, card, collapse, etc.).

### Helper functions

| Function                                     | Signature                                                                     |
| -------------------------------------------- | ----------------------------------------------------------------------------- |
| `_merge_inline_style(tag_html, additions)`   | Merges CSS properties into a tag's `style` attr; returns `(new_tag, changed)` |
| `_remove_inline_style_keys(tag_html, keys)`  | Removes specific CSS property keys from `style`; returns `(new_tag, changed)` |
| `_extract_attr_value(tag_html, attr_name)`   | Returns attribute value or `None`                                             |
| `_plain_text(value)`                         | Strips HTML tags, returns plain text                                          |
| `_extract_accordion_title_text(header_html)` | Extracts title text from accordion header HTML                                |

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

## Strategic decisions (confirmed by user)

1. **Template asset mapping** — Style Inference: parse Brightspace CSS/JS to derive canvas equivalents (Phase 3)
2. **Preview workflow** — Canvas Preview API: render in a real Canvas sandbox before upload (Phase 2)
3. **Layout preservation** — CSS Parser: parse inline styles to detect layout intent (Phase 2)

---

## Development conventions

- Virtual environment: `.venv/` (Python 3.12). Always activate before running.
- Package installed editable: `pip install -e .`
- Tests: `pytest` via `pyproject.toml` config, 50 tests in `tests/test_html_tools.py`.
- Branch model: `main` is stable. Feature work goes on `copilot-test` branch.
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

"""
Tests for lms_migration.html_tools — covering the five bug-fixes applied in
the copilot-test session (2026-03-18):

  Bug 1 – image align/hspace/vspace converted to inline CSS (not silently stripped)
  Bug 2 – Bootstrap utility classes promoted to inline CSS before being removed
  Bug 3 – Accordion flatten mode suppresses generic "Section" placeholder headings
  Bug 4 – Content images get max-width:100%; fixed-pixel tables become fluid
  Bug 5 – Intentional empty-spacer paragraphs are preserved (only 3+ runs collapsed)
"""

import re
from pathlib import Path
from lms_migration.html_tools import (
    CanvasSanitizerPolicy,
    apply_canvas_sanitizer,
    BestPracticeEnforcerPolicy,
    apply_best_practice_enforcer,
    inject_accent_divider,
    _convert_bootstrap_accordion_cards,
    _merge_inline_style,
    _extract_attr_value,
    _BOOTSTRAP_UTILITY_CSS_MAP,
    _ACCORDION_PLACEHOLDER_TITLES,
    _normalize_course_outline_columns,
)
from lms_migration.template_overlay import (
    TemplateOverlayContext,
    apply_template_overlay,
    _canonical_heading_label,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal policy defaults — any kwarg can override these.
_POLICY_DEFAULTS = dict(
    sanitize_brightspace_assets=False,
    neutralize_legacy_d2l_links=False,
    use_alt_text_for_removed_template_images=False,
    repair_missing_local_references=False,
    normalize_divider_styling=False,
)


def _sanitize(html: str, **policy_kwargs) -> str:
    """Run apply_canvas_sanitizer with minimal defaults. Any kwarg overrides."""
    params = {**_POLICY_DEFAULTS, **policy_kwargs}
    policy = CanvasSanitizerPolicy(**params)
    result, _ = apply_canvas_sanitizer(html, policy)
    return result


def _changes(html: str, **policy_kwargs) -> list:
    params = {**_POLICY_DEFAULTS, **policy_kwargs}
    policy = CanvasSanitizerPolicy(**params)
    _, applied = apply_canvas_sanitizer(html, policy)
    return applied


# ===========================================================================
# Bug 1 — image align / hspace / vspace  →  inline CSS
# ===========================================================================


class TestImageLayoutAttrsToCss:
    """align/hspace/vspace are deprecated HTML4 attributes.  The sanitizer must
    convert them to CSS float/margin before stripping them so that text-wrap and
    image positioning survive the Canvas import."""

    def test_align_left_becomes_float(self):
        html = '<img src="photo.jpg" align="left" alt="photo">'
        out = _sanitize(html)
        assert "float: left" in out or "float:left" in out
        assert "align=" not in out

    def test_align_right_becomes_float(self):
        html = '<img src="photo.jpg" align="right" alt="photo">'
        out = _sanitize(html)
        assert "float: right" in out or "float:right" in out
        assert "align=" not in out

    def test_align_center_becomes_margin_auto(self):
        html = '<img src="photo.jpg" align="center" alt="photo">'
        out = _sanitize(html)
        assert "margin" in out.lower()
        assert "align=" not in out

    def test_hspace_becomes_horizontal_margin(self):
        html = '<img src="photo.jpg" hspace="10" alt="photo">'
        out = _sanitize(html)
        assert "10px" in out
        assert "hspace=" not in out

    def test_vspace_becomes_vertical_margin(self):
        html = '<img src="photo.jpg" vspace="8" alt="photo">'
        out = _sanitize(html)
        assert "8px" in out
        assert "vspace=" not in out

    def test_combined_attrs_all_converted(self):
        html = '<img src="photo.jpg" align="left" hspace="12" vspace="6" alt="photo">'
        out = _sanitize(html)
        assert "float" in out.lower()
        assert "12px" in out
        assert "6px" in out
        assert "align=" not in out
        assert "hspace=" not in out
        assert "vspace=" not in out

    def test_border_attr_stripped(self):
        # border=0 is legacy and should be stripped without a CSS replacement
        html = '<img src="photo.jpg" border="0" alt="photo">'
        out = _sanitize(html)
        assert "border=" not in out

    def test_existing_style_preserved(self):
        # When the image already has an inline style, it should be merged, not replaced
        html = '<img src="photo.jpg" align="left" style="opacity:0.9" alt="photo">'
        out = _sanitize(html)
        assert "opacity" in out
        assert "float" in out.lower()

    def test_layout_attrs_converted_change_recorded(self):
        html = '<img src="photo.jpg" align="left" alt="photo">'
        changes = _changes(html)
        descs = [c.description for c in changes]
        assert any("align" in d.lower() or "layout" in d.lower() for d in descs)


# ===========================================================================
# Bug 2 — Bootstrap utility classes  →  inline CSS
# ===========================================================================


class TestBootstrapUtilityClassesToCss:
    """Bootstrap utility classes like float-left, text-center, bg-light carry
    visible layout intent.  They must be promoted to inline CSS *before* the
    class tokens are removed so the visual appearance is preserved in Canvas."""

    # Bootstrap processing is gated on sanitize_brightspace_assets (Bootstrap
    # is a Brightspace dependency), so tests must enable that flag.
    _BS = dict(sanitize_brightspace_assets=True, strip_bootstrap_grid_classes=True)

    def test_float_left_promoted(self):
        html = '<div class="float-left"><p>content</p></div>'
        out = _sanitize(html, **self._BS)
        assert "float: left" in out or "float:left" in out

    def test_float_right_promoted(self):
        html = '<div class="float-right"><p>content</p></div>'
        out = _sanitize(html, **self._BS)
        assert "float: right" in out or "float:right" in out

    def test_text_center_promoted(self):
        html = '<p class="text-center">Centered</p>'
        out = _sanitize(html, **self._BS)
        assert "text-align: center" in out or "text-align:center" in out

    def test_text_right_promoted(self):
        html = '<p class="text-right">Right</p>'
        out = _sanitize(html, **self._BS)
        assert "text-align: right" in out or "text-align:right" in out

    def test_bg_light_promoted(self):
        html = '<div class="bg-light"><p>box</p></div>'
        out = _sanitize(html, **self._BS)
        assert "background-color" in out.lower()

    def test_padding_class_promoted(self):
        html = '<div class="p-3"><p>padded</p></div>'
        out = _sanitize(html, **self._BS)
        assert "padding" in out.lower()

    def test_utility_class_token_removed_after_promotion(self):
        html = '<div class="float-left col-md-6"><p>content</p></div>'
        out = _sanitize(html, **self._BS)
        # The class token should be gone even though CSS was promoted
        assert "float-left" not in out
        assert "col-md-6" not in out

    def test_non_layout_class_not_spuriously_styled(self):
        # A custom class with no Bootstrap equivalent should not gain a style attr
        html = '<div class="course-intro"><p>hello</p></div>'
        out = _sanitize(
            html, sanitize_brightspace_assets=True, strip_bootstrap_grid_classes=False
        )
        assert "style" not in out

    def test_bootstrap_utility_css_map_completeness(self):
        # Verify the map covers at minimum the documented utility groups
        keys = set(_BOOTSTRAP_UTILITY_CSS_MAP.keys())
        assert "float-left" in keys
        assert "float-right" in keys
        assert "text-center" in keys
        assert "bg-light" in keys
        # spacing entries present
        assert any(k.startswith("p-") for k in keys)
        assert any(k.startswith("mt-") for k in keys)


# ===========================================================================
# Bug 3 — Accordion flatten: suppress generic "Section" placeholder headings
# ===========================================================================


class TestAccordionFlattenPlaceholderHeadings:
    """When a D2L accordion card has a placeholder title like 'Section' (common
    in template-generated pages), flatten mode must NOT emit a spurious <h3>."""

    # Minimal accordion card that matches _ACCORDION_CARD_PATTERN:
    # card > card-header + collapse > card-body
    _CARD_TMPL = """
    <div class="card">
        <div class="card-header"><h3 class="card-title">{title}</h3></div>
        <div class="collapse show">
            <div class="card-body">{body}</div>
        </div>
    </div>
    """

    def _accordion(self, title: str, body: str = "<p>Body text.</p>") -> str:
        return self._CARD_TMPL.format(title=title, body=body)

    def test_section_placeholder_suppressed(self):
        html = self._accordion("Section")
        out, count = _convert_bootstrap_accordion_cards(html, "flatten")
        assert count == 1
        assert "<h3>Section</h3>" not in out

    def test_item_placeholder_suppressed(self):
        html = self._accordion("Item")
        out, _ = _convert_bootstrap_accordion_cards(html, "flatten")
        assert "<h3>Item</h3>" not in out

    def test_real_title_preserved(self):
        html = self._accordion("Course Policies")
        out, _ = _convert_bootstrap_accordion_cards(html, "flatten")
        assert "<h3>Course Policies</h3>" in out

    def test_real_title_with_placeholder_name_preserved(self):
        # "Section 3: Reading" is NOT a placeholder — it's a real heading
        html = self._accordion("Section 3: Reading")
        out, _ = _convert_bootstrap_accordion_cards(html, "flatten")
        assert "<h3>Section 3: Reading</h3>" in out

    def test_empty_title_suppressed(self):
        html = self._accordion("")
        out, _ = _convert_bootstrap_accordion_cards(html, "flatten")
        # No empty <h3></h3> should appear
        assert "<h3></h3>" not in out

    def test_details_mode_still_gets_section_label(self):
        # In details mode the summary tag should still show a fallback label
        html = self._accordion("Section")
        out, _ = _convert_bootstrap_accordion_cards(html, "details")
        assert "<summary" in out
        # Should have SOME content in summary (fallback "Section" label is fine in
        # details mode because it acts as the expand/collapse control, not a heading)
        assert re.search(r"<summary[^>]*>\s*\S", out)

    def test_placeholder_titles_frozenset_contains_section(self):
        assert "section" in _ACCORDION_PLACEHOLDER_TITLES

    def test_body_content_retained_even_when_heading_suppressed(self):
        html = self._accordion("Section", "<p>Important content.</p>")
        out, _ = _convert_bootstrap_accordion_cards(html, "flatten")
        assert "Important content." in out


# ===========================================================================
# Bug 4 — Responsive images and fluid wide tables
# ===========================================================================


class TestResponsiveLayout:
    """Content images should get max-width: 100% so they don't overflow the
    Canvas page.  Tables with fixed pixel widths > 500 px should become fluid."""

    def test_image_gets_max_width(self):
        html = '<img src="diagram.png" alt="diagram">'
        out = _sanitize(html)
        assert "max-width" in out.lower()
        assert "100%" in out

    def test_image_height_auto_added(self):
        html = '<img src="diagram.png" alt="diagram">'
        out = _sanitize(html)
        assert "height: auto" in out or "height:auto" in out

    def test_image_with_existing_max_width_unchanged(self):
        html = (
            '<img src="diagram.png" style="max-width: 50%; height: auto" alt="diagram">'
        )
        out = _sanitize(html)
        # Should not double-add max-width
        assert out.count("max-width") == 1

    def test_template_asset_image_skipped(self):
        # Icons in templateassets/ already have correct sizing from the overlay pass
        html = '<img src="/templateassets/icons/check.svg" alt="check">'
        out = _sanitize(html)
        # max-width should NOT be injected for template assets
        assert "max-width" not in out.lower()

    def test_wide_table_made_fluid(self):
        html = '<table style="width: 900px"><tr><td>data</td></tr></table>'
        out = _sanitize(html)
        assert "width: 100%" in out or "width:100%" in out
        assert "max-width: 900px" in out or "max-width:900px" in out

    def test_narrow_table_untouched(self):
        html = '<table style="width: 300px"><tr><td>data</td></tr></table>'
        out = _sanitize(html)
        # 300 px is within Canvas safe zone — leave it alone
        assert "max-width" not in out.lower()

    def test_table_at_threshold_untouched(self):
        html = '<table style="width: 500px"><tr><td>data</td></tr></table>'
        out = _sanitize(html)
        assert "max-width" not in out.lower()

    def test_table_just_over_threshold_made_fluid(self):
        html = '<table style="width: 501px"><tr><td>data</td></tr></table>'
        out = _sanitize(html)
        assert "width: 100%" in out or "width:100%" in out

    def test_table_without_width_untouched(self):
        html = "<table><tr><td>data</td></tr></table>"
        out_table = _sanitize(html)
        # No style injection for tables without explicit width
        assert "max-width" not in out_table.lower()


# ===========================================================================
# Bug 5 — Preserve intentional empty-spacer paragraphs
# ===========================================================================


class TestSpacerParagraphPreservation:
    """Empty paragraphs (<p>&nbsp;</p>) are used intentionally in D2L pages for
    section spacing.  The sanitizer should only collapse large *runs* (3+), not
    remove isolated or paired spacers."""

    _REAL_CONTENT = "<p>Real paragraph.</p>"
    _SPACER = "<p>&nbsp;</p>"

    def test_single_spacer_preserved(self):
        html = self._REAL_CONTENT + self._SPACER + self._REAL_CONTENT
        out = _sanitize(html)
        assert self._SPACER in out

    def test_double_spacer_preserved(self):
        html = self._REAL_CONTENT + self._SPACER * 2 + self._REAL_CONTENT
        out = _sanitize(html)
        assert out.count("&nbsp;") >= 2

    def test_triple_spacer_run_collapsed_to_one(self):
        html = self._REAL_CONTENT + self._SPACER * 3 + self._REAL_CONTENT
        out = _sanitize(html)
        # 3 spacers → collapsed to 1
        assert out.count("&nbsp;") == 1

    def test_large_run_collapsed_to_one(self):
        html = self._REAL_CONTENT + self._SPACER * 10 + self._REAL_CONTENT
        out = _sanitize(html)
        assert out.count("&nbsp;") == 1

    def test_real_content_between_spacers_preserved(self):
        html = self._SPACER + self._REAL_CONTENT + self._SPACER
        out = _sanitize(html)
        assert "Real paragraph." in out

    def test_br_spacer_paragraph_preserved(self):
        spacer = "<p><br></p>"
        html = self._REAL_CONTENT + spacer + self._REAL_CONTENT
        out = _sanitize(html)
        assert "<br>" in out or "<br/>" in out

    def test_span_nbsp_spacer_preserved(self):
        spacer = "<p><span>&nbsp;</span></p>"
        html = self._REAL_CONTENT + spacer + self._REAL_CONTENT
        out = _sanitize(html)
        assert "&nbsp;" in out


# ===========================================================================
# Low-level helper tests
# ===========================================================================


class TestMergeInlineStyle:
    """Unit tests for the _merge_inline_style helper used by all bug fixes."""

    def test_adds_style_attr_when_absent(self):
        tag = "<div>"
        out, changed = _merge_inline_style(tag, {"float": "left"})
        assert changed
        assert "style=" in out
        assert "float: left" in out or "float:left" in out

    def test_merges_into_existing_style(self):
        tag = '<div style="color: red">'
        out, changed = _merge_inline_style(tag, {"float": "left"})
        assert changed
        assert "color" in out
        assert "float" in out

    def test_does_not_duplicate_existing_key(self):
        tag = '<div style="float: right">'
        out, changed = _merge_inline_style(tag, {"float": "left"})
        # Should not add a second float declaration
        assert out.count("float") == 1

    def test_no_change_when_props_already_present(self):
        tag = '<div style="float: left; margin-right: 12px">'
        out, changed = _merge_inline_style(
            tag, {"float": "left", "margin-right": "12px"}
        )
        assert not changed


class TestExtractAttrValue:
    def test_extracts_double_quoted(self):
        assert _extract_attr_value('<img src="photo.jpg">', "src") == "photo.jpg"

    def test_extracts_single_quoted(self):
        assert _extract_attr_value("<img src='photo.jpg'>", "src") == "photo.jpg"

    def test_returns_none_when_absent(self):
        assert _extract_attr_value('<img alt="photo">', "src") is None

    def test_case_insensitive(self):
        assert _extract_attr_value('<img ALIGN="left">', "align") == "left"


# ===========================================================================
# normalize_icon_only_paragraph — standalone <p><img templateassets/…></p>
# ===========================================================================


def _make_ctx(**overrides) -> TemplateOverlayContext:
    """Minimal TemplateOverlayContext for the icon-heading normalisation tests.
    The template_package path never needs to exist because the tested code path
    only reads icon_label_by_basename and apply_color_standards."""
    defaults: dict = dict(
        template_package=Path("."),
        alias_map_source="test",
        alias_map={},
        assets_by_basename={
            "checklist.png": ["TemplateAssets/checklist.png"],
            "video.png": ["TemplateAssets/video.png"],
        },
        file_name_collisions={},
        icon_label_by_basename={
            "checklist.png": "Checklist",
            "video.png": "Learning Activities",
        },
        apply_visual_standards=True,
        apply_color_standards=True,
        apply_divider_standards=True,
        image_layout_mode="safe-block",
    )
    defaults.update(overrides)
    return TemplateOverlayContext(**defaults)


class TestNormalizeIconOnlyParagraph:
    """A <p> or <div> block that contains ONLY a TemplateAssets icon should be
    promoted to a labelled icon heading by the template overlay pass."""

    def _overlay(self, html: str, **ctx_overrides) -> str:
        ctx = _make_ctx(**ctx_overrides)
        out, _, _, _ = apply_template_overlay(html, file_path="test.html", context=ctx)
        return out

    def test_p_icon_only_becomes_heading(self):
        html = '<p><img src="../TemplateAssets/checklist.png" alt=""/></p>'
        out = self._overlay(html)
        assert "<h3" in out
        assert "checklist.png" in out

    def test_heading_contains_canonical_label(self):
        html = '<p><img src="../TemplateAssets/checklist.png" alt=""/></p>'
        out = self._overlay(html)
        assert "Checklist" in out

    def test_video_icon_gets_label(self):
        # video.png is hardcoded to return 'View' by _canonical_heading_label
        html = '<p><img src="../TemplateAssets/video.png" alt=""/></p>'
        out = self._overlay(html)
        assert "View" in out

    def test_div_wrapper_also_converted(self):
        html = '<div><img src="../TemplateAssets/checklist.png" alt=""/></div>'
        out = self._overlay(html)
        assert "<h3" in out
        assert "Checklist" in out

    def test_icon_with_text_sibling_not_affected(self):
        # A <p> with an icon AND text next to it should NOT be re-wrapped
        html = '<p><img src="../TemplateAssets/checklist.png" alt=""/> Assignment checklist</p>'
        out = self._overlay(html)
        # Should remain a <p>, not be converted to a heading
        assert "<p>" in out or "<p " in out

    def test_unknown_icon_not_converted(self):
        # An icon with no label entry AND no hardcoded default should stay as-is.
        # 'photo-placeholder.png' is not in _DEFAULT_ICON_LABELS or any special case.
        ctx = _make_ctx(
            assets_by_basename={
                "photo-placeholder.png": ["TemplateAssets/photo-placeholder.png"]
            },
            icon_label_by_basename={},
        )
        html = '<p><img src="../TemplateAssets/photo-placeholder.png" alt=""/></p>'
        out, _, _, _ = apply_template_overlay(html, file_path="test.html", context=ctx)
        assert "<h3" not in out

    def test_non_templateassets_img_not_converted(self):
        html = '<p><img src="../images/photo.jpg" alt="photo"/></p>'
        out = self._overlay(html)
        assert "<h3" not in out


# ===========================================================================
# _canonical_heading_label — checklist vs checkmark, question.png
# ===========================================================================


class TestCanonicalHeadingLabel:
    """Regression tests for the checklist/checkmark icon label split and the
    question.png 'Help Links' mapping, which were the two confirmed bugs found
    during the March 2026 template imscc audit."""

    def test_checkmark_returns_module_checklist(self):
        assert (
            _canonical_heading_label("", icon_basename="checkmark.png")
            == "Module Checklist"
        )

    def test_checklist_returns_checklist_not_module_checklist(self):
        # checklist.png is the generic checklist / Sinclair Policies icon,
        # NOT the module-level activity list.
        assert (
            _canonical_heading_label("Checklist", icon_basename="checklist.png")
            == "Checklist"
        )

    def test_key_checklist_returns_checklist(self):
        # A heading whose text normalises to "checklist" should be "Checklist",
        # not "Module Checklist".
        assert _canonical_heading_label("Checklist") == "Checklist"

    def test_key_module_checklist_returns_module_checklist(self):
        assert _canonical_heading_label("Module Checklist") == "Module Checklist"

    def test_question_png_returns_help_links(self):
        # question.png maps to "Help Links" whether resolved via basename or key.
        assert (
            _canonical_heading_label("", icon_basename="question.png") == "Help Links"
        )
        assert _canonical_heading_label("Hints") == "Help Links"

    def test_video_always_returns_view(self):
        assert _canonical_heading_label("", icon_basename="video.png") == "View"
        assert _canonical_heading_label("View") == "View"


# ===========================================================================
# _INTRO_HEADING_SPECS — page-title h2 vs section h2 styles
# ===========================================================================


class TestIntroHeadingStyle:
    """Gold standard (ast-1111 training corpus) shows two distinct h2 styles:

    * PAGE TITLE h2 (Introduction, Learning Activities — the FIRST h2 on the
      page): ``color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px``
    * SECTION h2 (Module Objectives, Module Checklist — SUBSEQUENT h2s on the
      same page): bare ``<h2>`` tag, color applied only via inner content span.
      NO border-bottom, NO padding on the h2 tag itself.

    Confirmed by forensic scan of 479 gold pages across 5 training courses.
    """

    def _make_intro_ctx(self) -> TemplateOverlayContext:
        return TemplateOverlayContext(
            template_package=Path("."),
            alias_map_source="test",
            alias_map={},
            assets_by_basename={
                "star.png": ["TemplateAssets/star.png"],
                "bullseye.png": ["TemplateAssets/bullseye.png"],
                "checkmark.png": ["TemplateAssets/checkmark.png"],
            },
            file_name_collisions={},
            icon_label_by_basename={},
            apply_visual_standards=True,
            apply_color_standards=True,
            apply_divider_standards=True,
            image_layout_mode="safe-block",
        )

    def _apply(self, source_h1_text: str) -> str:
        html = f"<body><div><h1>{source_h1_text}</h1><p>Content.</p></div></body>"
        out, _, _, _ = apply_template_overlay(
            html,
            file_path="Introduction and Objectives.html",
            context=self._make_intro_ctx(),
        )
        return out

    # --- Page-title heading (Introduction) ---

    def test_introduction_h1_gets_full_red_h2_style(self):
        """Introduction is the page title — must have border-bottom + padding."""
        result = self._apply("Introduction")
        assert "border-bottom: 10px solid #ac1a2f" in result.lower()
        assert "padding: 10px" in result.lower()
        assert "color: #ac1a2f" in result.lower()

    def test_introduction_h2_tag_has_border_bottom(self):
        """The border-bottom must be on the h2 tag itself, not just inner content."""
        result = self._apply("Introduction")
        h2_match = re.search(r"<h2[^>]*>", result, re.IGNORECASE)
        assert h2_match, "Expected an <h2> element"
        assert "border-bottom" in h2_match.group(0).lower()

    # --- Section headings (Module Objectives, Module Checklist) ---

    def test_learning_objectives_is_color_only_no_border_bottom(self):
        """Section headings must NOT have border-bottom — only the page title does."""
        result = self._apply("Learning Objectives")
        assert "color: #ac1a2f" in result.lower()
        h2_match = re.search(r"<h2[^>]*>", result, re.IGNORECASE)
        assert h2_match, "Expected an <h2> element"
        assert (
            "border-bottom" not in h2_match.group(0).lower()
        ), f"Section heading h2 must not have border-bottom, got: {h2_match.group(0)}"

    def test_module_objectives_is_color_only_no_border_bottom(self):
        result = self._apply("Module Objectives")
        h2_match = re.search(r"<h2[^>]*>", result, re.IGNORECASE)
        assert h2_match
        assert "border-bottom" not in h2_match.group(0).lower()

    def test_checklist_is_color_only_no_border_bottom(self):
        result = self._apply("Checklist")
        h2_match = re.search(r"<h2[^>]*>", result, re.IGNORECASE)
        assert h2_match
        assert "border-bottom" not in h2_match.group(0).lower()

    def test_module_checklist_is_color_only_no_border_bottom(self):
        result = self._apply("Module Checklist")
        h2_match = re.search(r"<h2[^>]*>", result, re.IGNORECASE)
        assert h2_match
        assert "border-bottom" not in h2_match.group(0).lower()


# ===========================================================================
# Trailing red hr — all template content pages must end with the 8px red hr
# ===========================================================================


class TestTrailingRedHr:
    """Gold standard: every module content page (lesson, activities, intro,
    review) ends with ``<hr style="border-top: 8px solid #AC1A2F;">``.
    Home pages are excluded.  The hr is added by apply_template_overlay when
    apply_divider_standards=True and the page doesn't already have one."""

    _CANONICAL_HR = '<hr style="border-top: 8px solid #AC1A2F;">'

    def _make_ctx(self) -> TemplateOverlayContext:
        return TemplateOverlayContext(
            template_package=Path("."),
            alias_map_source="test",
            alias_map={},
            assets_by_basename={},
            file_name_collisions={},
            icon_label_by_basename={},
            apply_visual_standards=False,
            apply_color_standards=False,
            apply_divider_standards=True,
            image_layout_mode="safe-block",
        )

    def _apply(self, html: str, file_path: str = "module-1-lesson.html") -> str:
        out, _, _, _ = apply_template_overlay(
            html,
            file_path=file_path,
            context=self._make_ctx(),
        )
        return out

    def test_trailing_hr_added_to_content_page(self):
        html = "<body><div><h2>Some Content</h2><p>Text.</p></div></body>"
        result = self._apply(html)
        assert self._CANONICAL_HR in result

    def test_trailing_hr_before_body_close(self):
        html = "<body><p>Content.</p></body>"
        result = self._apply(html)
        # hr must appear before </body>
        hr_pos = result.lower().find("border-top: 8px")
        body_close_pos = result.lower().rfind("</body>")
        assert hr_pos != -1, "Canonical hr not found"
        assert hr_pos < body_close_pos

    def test_trailing_hr_not_duplicated_if_already_present(self):
        html = (
            "<body><p>Content.</p>"
            '<hr style="border-top: 8px solid #AC1A2F;">'
            "</body>"
        )
        result = self._apply(html)
        count = result.lower().count("border-top: 8px solid")
        assert count == 1, f"Expected exactly 1 canonical closing hr, got {count}"

    def test_home_page_excluded(self):
        html = "<body><p>Home page content.</p></body>"
        result = self._apply(html, file_path="home-page.html")
        assert "border-top: 8px solid" not in result.lower()

    def test_home_page_variants_excluded(self):
        for name in ("home-page-lcs.html", "home-page-stem.html", "home page.html"):
            html = "<body><p>Content.</p></body>"
            result = self._apply(html, file_path=name)
            assert (
                "border-top: 8px solid" not in result.lower()
            ), f"Home page {name!r} should not get trailing hr"

    def test_apply_divider_standards_false_skips_trailing_hr(self):
        ctx = TemplateOverlayContext(
            template_package=Path("."),
            alias_map_source="test",
            alias_map={},
            assets_by_basename={},
            file_name_collisions={},
            icon_label_by_basename={},
            apply_visual_standards=False,
            apply_color_standards=False,
            apply_divider_standards=False,
            image_layout_mode="safe-block",
        )
        html = "<body><p>Content.</p></body>"
        out, _, _, _ = apply_template_overlay(
            html, file_path="module-1-lesson.html", context=ctx
        )
        assert "border-top: 8px solid" not in out.lower()

    def test_trailing_hr_change_logged(self):
        html = "<body><p>Content.</p></body>"
        _, changes, _, _ = apply_template_overlay(
            html,
            file_path="module-1-lesson.html",
            context=self._make_ctx(),
        )
        descriptions = [c.description.lower() for c in changes]
        assert any(
            "closing red divider" in d for d in descriptions
        ), f"Expected trailing-hr AppliedChange, got: {descriptions}"


# ===========================================================================
# remove_leading_divider — regression tests for the D2L template header strip
# ===========================================================================


class TestRemoveLeadingDivider:
    """Regression tests for the remove_leading_divider fix.

    The D2L Brightspace template adds a 'header region' <div> at the top of
    every page that contains a printer-friendly link (removed by the sanitizer)
    and a decorative <hr>.  After sanitisation the div is left with one or two
    empty spacer paragraphs and the <hr>.  These tests verify that
    remove_leading_divider strips the entire header div so Canvas pages don't
    start with an orphan red rule.
    """

    @staticmethod
    def _make_ctx() -> "TemplateOverlayContext":
        return TemplateOverlayContext(
            template_package=Path("."),
            alias_map_source="test",
            alias_map={},
            assets_by_basename={
                "star.png": ["TemplateAssets/star.png"],
                "bullseye.png": ["TemplateAssets/bullseye.png"],
                "bookmark.png": ["TemplateAssets/bookmark.png"],
            },
            file_name_collisions={},
            icon_label_by_basename={
                "star.png": "Introduction",
                "bullseye.png": "Module Objectives",
                "bookmark.png": "Learning Activities",
            },
            apply_visual_standards=True,
            apply_color_standards=True,
            apply_divider_standards=True,
            image_layout_mode="safe-block",
        )

    def _apply(
        self, html: str, file_path: str = "Introduction and Objectives.html"
    ) -> str:
        out, _, _, _ = apply_template_overlay(
            html, file_path=file_path, context=self._make_ctx()
        )
        return out

    def test_header_div_with_two_spacers_stripped(self):
        """Two empty paragraphs + <hr> in the first div must be removed.

        This was the primary bug: the regex only allowed ONE empty paragraph,
        but the D2L template always emits TWO (the former printer-link paragraph
        and the spacer paragraph).
        """
        html = (
            "<body><div>"
            "<p></p>"
            '<p><span style="">&nbsp;</span></p>'
            '<hr style="border: 0; height: 2px; background-color: #ac1a2f;" row="">'
            "</div>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Content here.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        # The D2L header hr (with background-color styling) must be stripped.
        # The canonical closing red hr may be present at the end of the page.
        assert (
            "background-color: #ac1a2f" not in result.lower()
        ), "D2L header hr should have been stripped"
        assert "Content here" in result

    def test_header_div_not_present_in_output(self):
        """The first orphan div (with only spacers + hr) should be gone entirely."""
        html = (
            "<body><div>"
            "<p></p>"
            '<p><span style="">&nbsp;</span></p>'
            '<hr style="border: 0; height: 2px; background-color: #ac1a2f;">'
            "</div>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Content.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        # Body should start immediately with the content div, not a bare <div>
        import re

        body_inner = re.search(r"<body[^>]*>(.*)", result, re.DOTALL)
        assert body_inner is not None
        stripped = body_inner.group(1).lstrip()
        assert stripped.startswith("<div"), "Body should start with the content div"

    def test_single_spacer_also_stripped(self):
        """The fix must still handle the original single-spacer case."""
        html = (
            "<body><div>"
            '<p><span style="">&nbsp;</span></p>'
            '<hr style="height: 2px; background-color: #ac1a2f;">'
            "</div>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Content.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        # The D2L banner hr must be gone.
        assert "background-color: #ac1a2f" not in result.lower()
        assert "Content." in result

    def test_no_header_div_not_affected(self):
        """Pages with no D2L header div must keep their content intact.
        A canonical trailing red hr is expected to be present (added by the
        trailing-hr logic) but no D2L banner hr."""
        html = (
            "<body>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Content without any initial hr.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        assert "Introduction" in result
        # Only the canonical closing hr should be present; no old D2L banner hr.
        assert "background-color" not in result.lower()
        import re as _re

        hr_styles = [
            m.group(0) for m in _re.finditer(r"<hr[^>]*>", result, _re.IGNORECASE)
        ]
        assert all(
            "border-top: 8px solid" in s.lower() for s in hr_styles
        ), f"Only canonical closing hr expected, found: {hr_styles}"

    def test_content_hr_inside_body_not_stripped(self):
        """An <hr> that appears WITHIN content (not as the first div) must be kept."""
        html = (
            "<body>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Intro text.</p>"
            '<hr style="height: 2px; background-color: #ac1a2f;">'
            "<h2>Section Two</h2>"
            "<p>More content.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        # The in-content hr should be preserved
        assert "<hr" in result.lower(), "In-content <hr> should not be removed"

    def test_leading_divider_removal_logged_as_change(self):
        """The AppliedChange for divider removal must be emitted."""
        html = (
            "<body><div>"
            "<p></p>"
            '<p><span style="">&nbsp;</span></p>'
            '<hr style="height: 2px; background-color: #ac1a2f;" row="">'
            "</div>"
            "<div>"
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>Content.</p>"
            "</div>"
            "</body>"
        )
        _, changes, _, _ = apply_template_overlay(
            html,
            file_path="Introduction and Objectives.html",
            context=self._make_ctx(),
        )
        divider_change = next(
            (c for c in changes if "divider" in c.description.lower()), None
        )
        assert divider_change is not None, "Expected a divider-removal AppliedChange"
        assert divider_change.count == 1

    def test_pre_sanitization_structure_stripped(self):
        """The fix must handle the PRE-sanitised structure where the printer-friendly
        link paragraph is still present (before apply_canvas_sanitizer runs).

        This is the actual pipeline call order: apply_template_overlay fires
        BEFORE apply_canvas_sanitizer, so the printer-link anchor is still live
        when remove_leading_divider executes.
        """
        html = (
            '<body><div class="container-fluid">'
            '<p><span style="font-family: Lato, sans-serif;">'
            '<a href="javascript:window.print()" style="float: right;" class="courseLink">'
            "Printer-friendly version</a>"
            "</span></p>"
            '<p><span style="font-family: Lato, sans-serif;">&nbsp;</span></p>'
            '<hr style="width: 100%; height: 4px; color: #ac1a2f; background-color: #ac1a2f;" row=""></div>'
            '<div class="offset-md-2 col-md-8">'
            '<h2><img src="../TemplateAssets/star.png" alt=""> Introduction</h2>'
            "<p>This topic will discuss taxation.</p>"
            "</div>"
            "</body>"
        )
        result = self._apply(html)
        # The D2L header hr must be stripped.
        assert (
            "background-color: #ac1a2f" not in result.lower()
        ), "Header <hr> should be stripped even with printer link present"
        # The printer link paragraph should also be gone (it was in the stripped header div)
        assert "Printer-friendly version" not in result


# ===========================================================================
# Course-outline table normalisation
# ===========================================================================

# Minimal wrapper that triggers the syllabus-table normalisation path.
_SYLLABUS_WRAP = "<html><head></head><body><h1>Syllabus</h1>{}</body></html>"

_COURSE_OUTLINE_TABLE = (
    '<table summary="Course Outline">'
    "<tr>"
    '<th scope="col">16-WEEK</th>'
    '<th scope="col">TOPICS</th>'
    '<th scope="col">CHAPTER</th>'
    '<th scope="col">ASSIGNMENTS</th>'
    '<th scope="col">DUE DATE</th>'
    "</tr>"
    "<tr>"
    "<td>1</td>"
    "<td>Topic 1: Introduction to Taxation</td>"
    "<td>Chapter 1</td>"
    "<td>Read Chapter 1; Complete Quiz 1</td>"
    "<td>Jan 15</td>"
    "</tr>"
    "<tr>"
    "<td>2</td>"
    "<td>Topic 2: Tax Formula</td>"
    "<td>Chapter 2</td>"
    "<td>Read Chapter 2; Homework 2</td>"
    "<td>Jan 22</td>"
    "</tr>"
    "</table>"
)


def _sanitize_syllabus(html: str) -> str:
    """Run sanitizer in syllabus mode (all default flags, file recognised as syllabus)."""
    policy = CanvasSanitizerPolicy()
    result, _ = apply_canvas_sanitizer(html, policy, file_path="course-syllabus.html")
    return result


class TestCourseOutlineCaption:
    """Caption for a course-outline table is always 'Course Outline'."""

    def test_caption_has_no_16week_suffix(self):
        html = _SYLLABUS_WRAP.format(_COURSE_OUTLINE_TABLE)
        result = _sanitize_syllabus(html)
        assert "Course Outline (16-Week)" not in result
        assert "Course Outline (12-Week)" not in result
        assert "Course Outline" in result

    def test_caption_preserved_without_week_qualifier(self):
        table = _COURSE_OUTLINE_TABLE.replace("16-WEEK", "WEEK")
        html = _SYLLABUS_WRAP.format(table)
        result = _sanitize_syllabus(html)
        assert "Course Outline (16-Week)" not in result
        assert "Course Outline" in result

    def test_existing_caption_with_week_suffix_is_normalised(self):
        table = (
            '<table summary="">'
            "<caption>Course Outline (16-Week)</caption>"
            '<tr><th scope="col">16-WEEK</th>'
            '<th scope="col">TOPICS</th></tr>'
            "<tr><td>1</td><td>Topic 1</td></tr>"
            "</table>"
        )
        html = _SYLLABUS_WRAP.format(table)
        result = _sanitize_syllabus(html)
        assert "Course Outline (16-Week)" not in result
        assert "Course Outline" in result


class TestCourseOutlineColumnNormalisation:
    """WEEK / DUE DATE columns dropped; TOPICS/ASSIGNMENTS/CHAPTER renamed."""

    def _run(self, inner_html: str) -> str:
        return _normalize_course_outline_columns(inner_html)

    def test_week_column_dropped(self):
        inner = (
            "<tr>"
            '<th scope="col">16-WEEK</th>'
            '<th scope="col">TOPICS</th>'
            "</tr>"
            "<tr><td>1</td><td>Topic 1</td></tr>"
        )
        result = self._run(inner)
        assert "16-WEEK" not in result
        # Week number cell ("1") should be gone
        assert "<td>1</td>" not in result
        # TOPICS data cell gets Topic→Module transformation
        assert "Module 1" in result

    def test_due_date_column_dropped(self):
        inner = (
            "<tr>"
            '<th scope="col">TOPICS</th>'
            '<th scope="col">DUE DATE</th>'
            "</tr>"
            "<tr><td>Topic 1</td><td>Jan 15</td></tr>"
        )
        result = self._run(inner)
        assert "DUE DATE" not in result
        assert "Jan 15" not in result
        # TOPICS data cell gets Topic→Module transformation
        assert "Module 1" in result

    def test_topics_renamed_to_modules(self):
        inner = (
            "<tr>"
            '<th scope="col">TOPICS</th>'
            "</tr>"
            "<tr><td>Topic 1: Intro</td></tr>"
        )
        result = self._run(inner)
        assert "TOPICS" not in result
        assert "Modules" in result

    def test_assignments_label_preserved(self):
        """ASSIGNMENTS column header is case-normalised to Assignments, not renamed."""
        inner = (
            "<tr>"
            '<th scope="col">ASSIGNMENTS</th>'
            "</tr>"
            "<tr><td>Read Chapter 1</td></tr>"
        )
        result = self._run(inner)
        assert "ASSIGNMENTS" not in result
        assert "Assignments" in result
        # No TOPICS col → no Activities column should be injected
        assert "Activities" not in result

    def test_activities_column_inserted_after_modules(self):
        """A blank Activities column is inserted after Modules when none exists."""
        inner = (
            "<tr>"
            '<th scope="col">TOPICS</th>'
            '<th scope="col">ASSIGNMENTS</th>'
            "</tr>"
            "<tr><td>Topic 1: Intro</td><td>Quiz 1</td></tr>"
        )
        result = self._run(inner)
        # Activities header inserted
        assert "Activities" in result
        # Assignments label preserved
        assert "Assignments" in result
        assert "ASSIGNMENTS" not in result
        # Activities column appears BEFORE Assignments column
        assert result.index("Activities") < result.index("Assignments")

    def test_activities_column_not_duplicated(self):
        """No Activities column is inserted when the source already has one."""
        inner = (
            "<tr>"
            '<th scope="col">TOPICS</th>'
            '<th scope="col">Activities</th>'
            '<th scope="col">ASSIGNMENTS</th>'
            "</tr>"
            "<tr><td>Topic 1</td><td>Watch video</td><td>Quiz 1</td></tr>"
        )
        result = self._run(inner)
        assert result.count("Activities") == 1

    def test_chapter_renamed_canonical(self):
        inner = (
            "<tr>" '<th scope="col">CHAPTERS</th>' "</tr>" "<tr><td>Chapter 1</td></tr>"
        )
        result = self._run(inner)
        assert "CHAPTERS" not in result
        assert "Chapter" in result

    def test_topic_prefix_in_data_cells_replaced(self):
        inner = (
            "<tr>"
            '<th scope="col">TOPICS</th>'
            "</tr>"
            "<tr><td>Topic 1: Introduction to Taxation</td></tr>"
            "<tr><td>Topic 2: Tax Formula</td></tr>"
        )
        result = self._run(inner)
        assert "Topic 1:" not in result
        assert "Topic 2:" not in result
        assert "Module 1: Introduction to Taxation" in result
        assert "Module 2: Tax Formula" in result

    def test_full_table_via_sanitizer(self):
        """Integration: pipeline produces correct columns for ACC-2321-style input."""
        html = _SYLLABUS_WRAP.format(_COURSE_OUTLINE_TABLE)
        result = _sanitize_syllabus(html)
        # Caption
        assert "Course Outline" in result
        assert "(16-Week)" not in result
        # Dropped column headers
        assert "16-WEEK" not in result
        assert "DUE DATE" not in result
        # Renamed / inserted headers present
        assert "Modules" in result
        assert "Activities" in result  # blank column inserted by tool for faculty
        assert "Chapter" in result
        assert "Assignments" in result  # preserved from D2L source (not renamed)
        # Activities appears before Assignments in column order
        assert result.index("Activities") < result.index("Assignments")
        # Week data cells (just numbers) are gone
        assert "<td>1</td>" not in result
        assert "<td>2</td>" not in result
        # Due date data gone
        assert "Jan 15" not in result
        assert "Jan 22" not in result
        # Topic → Module replacement in data cells
        assert "Topic 1:" not in result
        assert "Module 1: Introduction to Taxation" in result


# ===========================================================================
# Divider normalisation
# ===========================================================================


class TestDividerNormalisation:
    """normalize_hr_tag: bare <hr> stays as-is; D2L/styled <hr> → bare <hr>; canonical red preserved."""

    def _run(self, html: str) -> str:
        return _sanitize(html, normalize_divider_styling=True)

    def test_bare_hr_not_made_red(self):
        """A plain <hr> should be left untouched — renders as grey in Canvas."""
        result = self._run("<div><hr></div>")
        assert "#ac1a2f" not in result.lower()
        assert "background-color" not in result.lower()
        assert "<hr>" in result.lower() or "<hr " in result.lower()

    def test_bare_hr_self_closing_not_made_red(self):
        result = self._run("<div><hr/></div>")
        assert "#ac1a2f" not in result.lower()

    def test_d2l_banner_hr_converted_to_bare_hr(self):
        """The D2L top-banner <hr style="...background-color: #ac1a2f;"> → bare <hr>."""
        hr = '<hr style="width: 100%; height: 4px; color: #ac1a2f; background-color: #ac1a2f;" row="">'
        result = self._run(f"<div>{hr}</div>")
        assert "#ac1a2f" not in result.lower()
        assert "background-color" not in result.lower()
        assert "style=" not in result.lower() or "border-top" in result.lower()
        assert "<hr>" in result.lower()

    def test_canonical_red_closing_divider_preserved(self):
        """<hr style="border-top: 8px solid #AC1A2F; ..."> must not change."""
        hr = '<hr style="border-top: 8px solid #AC1A2F; border-bottom: 0; height: 0; margin: 16px 0;">'
        result = self._run(f"<div>{hr}</div>")
        assert "border-top" in result
        assert "AC1A2F" in result or "ac1a2f" in result.lower()

    def test_canonical_minimal_red_closing_divider_preserved(self):
        hr = '<hr style="border-top: 8px solid #AC1A2F;">'
        result = self._run(f"<div>{hr}</div>")
        assert "border-top" in result
        assert "AC1A2F" in result or "ac1a2f" in result.lower()


# ===========================================================================
# D2L Rule image → <hr> replacement
# ===========================================================================


class TestRuleImageReplacement:
    """D2L Rule_brown_gradient.png inside a lone <p> becomes a bare <hr>."""

    _RULE_SRC = (
        "/shared/Brightspace_HTML_Template/pages/../_assets/"
        "standardImages/Rule_brown_gradient.png"
    )

    def _make_rule_para(self, extra_span: bool = True) -> str:
        img = (
            f'<img src="{self._RULE_SRC}" alt="" title="" '
            'data-d2l-editor-default-img-style="true" style="max-width: 100%;">'
        )
        if extra_span:
            return f'<p><span style="font-family: Lato, sans-serif;">{img}</span></p>'
        return f"<p>{img}</p>"

    def _run(self, html: str) -> str:
        return _sanitize(html, sanitize_brightspace_assets=True)

    def test_rule_image_in_span_para_becomes_hr(self):
        body = f"<div><p>Before</p>{self._make_rule_para(extra_span=True)}<p>After</p></div>"
        result = self._run(body)
        assert "<hr>" in result.lower() or "<hr " in result.lower()
        assert "Rule_brown_gradient" not in result
        assert "standardImages" not in result

    def test_rule_image_bare_para_becomes_hr(self):
        body = f"<div><p>Before</p>{self._make_rule_para(extra_span=False)}<p>After</p></div>"
        result = self._run(body)
        assert "<hr>" in result.lower() or "<hr " in result.lower()
        assert "Rule_brown_gradient" not in result

    def test_rule_hr_has_no_style(self):
        """The replacement <hr> must be bare (no style attribute)."""
        body = self._make_rule_para()
        result = self._run(body)
        hr_match = re.search(r"<hr\b([^>]*)>", result, re.IGNORECASE)
        assert hr_match is not None, "Expected an <hr> tag in result"
        attrs = hr_match.group(1)
        assert "style" not in attrs.lower()

    def test_non_rule_template_image_not_replaced_with_hr(self):
        """Other Brightspace template images (e.g. a header logo) are stripped, not hr'd."""
        src = "/shared/Brightspace_HTML_Template/pages/../_assets/img/logo.png"
        body = f'<p><img src="{src}" alt="logo"></p>'
        result = self._run(body)
        assert "logo.png" not in result
        assert "<hr>" not in result.lower()


# ===========================================================================
# Module Checklist closer — flags as ManualReviewIssue, no HTML injection
# ===========================================================================


class TestModuleChecklistCloserFlaggedNotInjected:
    """apply_best_practice_enforcer must emit a ManualReviewIssue (not inject HTML)
    when the Module Checklist closer is absent from an intro/checklist page.
    """

    _INTRO_PATH = "course_data/Introduction and Objectives.html"
    _CHECKLIST_PATH = "course_data/Introduction and Checklist.html"

    _PAGE_WITHOUT_CLOSER = """<html><body>
<h2>Module Checklist</h2>
<ul>
  <li>Read chapter 1</li>
  <li>Complete quiz</li>
</ul>
</body></html>"""

    _PAGE_WITH_CLOSER = """<html><body>
<h2>Module Checklist</h2>
<ul>
  <li>Read chapter 1</li>
  <li>Contact your instructor with any questions or post in the Course Q&amp;A.</li>
</ul>
</body></html>"""

    _PAGE_WITH_LOCAL_CLOSER = """<html><body>
<h2>Module Checklist</h2>
<ul>
  <li>Read chapter 1</li>
  <li>Contact your instructor with any course questions.</li>
</ul>
</body></html>"""

    def _run(self, html: str, file_path: str = _INTRO_PATH):
        policy = BestPracticeEnforcerPolicy(
            enabled=True,
            enforce_module_checklist_closer=True,
            ensure_external_links_new_tab=False,
        )
        return apply_best_practice_enforcer(html, file_path=file_path, policy=policy)

    def test_returns_three_tuple(self):
        updated, changes, issues = self._run(self._PAGE_WITHOUT_CLOSER)
        assert isinstance(updated, str)
        assert isinstance(changes, list)
        assert isinstance(issues, list)

    def test_missing_closer_emits_manual_review_issue(self):
        _, _, issues = self._run(self._PAGE_WITHOUT_CLOSER)
        assert len(issues) == 1
        assert "module checklist" in issues[0].reason.lower()
        assert "instructor contact" in issues[0].reason.lower()

    def test_missing_closer_does_not_inject_html(self):
        updated, _, _ = self._run(self._PAGE_WITHOUT_CLOSER)
        assert "migration-checklist-closer" not in updated
        assert "Module Checklist Reminder" not in updated
        assert "Contact your instructor" not in updated

    def test_page_with_q_and_a_closer_no_issue(self):
        _, _, issues = self._run(self._PAGE_WITH_CLOSER)
        checklist_issues = [i for i in issues if "module checklist" in i.reason.lower()]
        assert checklist_issues == []

    def test_page_with_local_closer_no_issue(self):
        _, _, issues = self._run(self._PAGE_WITH_LOCAL_CLOSER)
        checklist_issues = [i for i in issues if "module checklist" in i.reason.lower()]
        assert checklist_issues == []

    def test_checklist_path_triggers_detection(self):
        _, _, issues = self._run(
            self._PAGE_WITHOUT_CLOSER, file_path=self._CHECKLIST_PATH
        )
        assert any("module checklist" in i.reason.lower() for i in issues)

    def test_stale_migration_closer_paragraph_stripped(self):
        """Old migration-checklist-closer <p> from prior runs is stripped from the page."""
        html_with_stale = self._PAGE_WITHOUT_CLOSER.replace(
            "</body>",
            '<p class="migration-checklist-closer"><strong>Module Checklist Reminder:</strong>'
            " Contact your instructor with any course questions.</p></body>",
        )
        updated, _, _ = self._run(html_with_stale)
        assert "migration-checklist-closer" not in updated

    def test_disabled_enforcer_returns_empty_issues(self):
        policy = BestPracticeEnforcerPolicy(enabled=False)
        updated, changes, issues = apply_best_practice_enforcer(
            self._PAGE_WITHOUT_CLOSER, file_path=self._INTRO_PATH, policy=policy
        )
        assert issues == []
        assert changes == []
        assert updated == self._PAGE_WITHOUT_CLOSER

    def test_issue_category_is_content(self):
        _, _, issues = self._run(self._PAGE_WITHOUT_CLOSER)
        assert issues[0].category == "content"

    def test_non_intro_page_no_issue(self):
        """Pages not matching intro/checklist patterns should not get flagged."""
        _, _, issues = self._run(
            "<html><body><h1>Assignment 1</h1><p>Do the readings.</p></body></html>",
            file_path="course_data/Assignment 1.html",
        )
        checklist_issues = [i for i in issues if "module checklist" in i.reason.lower()]
        assert checklist_issues == []


# ===========================================================================
# inject_accent_divider — pipeline HR injection
# ===========================================================================


class TestInjectAccentDivider:
    """inject_accent_divider should prepend a 10 px red accent <hr> to pages
    that lack an icon heading and don't already have a top-of-body accent hr.
    """

    def test_plain_page_gets_hr(self):
        html = "<html><body><p>Hello</p></body></html>"
        updated, changes = inject_accent_divider(html)
        assert "border-top: 10px solid #AC1A2F" in updated
        assert len(changes) == 1
        assert changes[0].category == "structure"

    def test_page_with_icon_heading_unchanged(self):
        html = '<html><body><h2><img src="star.png"> Intro</h2><p>Content</p></body></html>'
        updated, changes = inject_accent_divider(html)
        assert updated == html
        assert changes == []

    def test_page_already_has_accent_hr(self):
        html = '<hr style="border-top: 10px solid #AC1A2F; border-bottom: none;">\n<p>Content</p>'
        updated, changes = inject_accent_divider(html)
        assert updated == html
        assert changes == []

    def test_hr_injected_inside_body_tag(self):
        html = "<html><body><p>Content</p></body></html>"
        updated, _ = inject_accent_divider(html)
        # The HR should appear right after <body>, not before <html>
        assert "<body>\n<hr" in updated

    def test_no_body_tag_prepends(self):
        html = "<p>Content</p>"
        updated, _ = inject_accent_divider(html)
        assert updated.startswith("<hr")

    def test_page_with_non_icon_heading_gets_hr(self):
        """An h2 without an <img> is not an icon heading — should inject."""
        html = "<html><body><h2>Just Text</h2><p>Content</p></body></html>"
        updated, changes = inject_accent_divider(html)
        assert "border-top: 10px solid #AC1A2F" in updated
        assert len(changes) == 1

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
import pytest
from lms_migration.html_tools import (
    CanvasSanitizerPolicy,
    apply_canvas_sanitizer,
    _convert_bootstrap_accordion_cards,
    _merge_inline_style,
    _extract_attr_value,
    _BOOTSTRAP_UTILITY_CSS_MAP,
    _ACCORDION_PLACEHOLDER_TITLES,
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

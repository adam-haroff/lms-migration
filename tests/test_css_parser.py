"""Tests for css_parser.py — inline CSS parsing and layout-intent classification."""

from __future__ import annotations

import pytest

from lms_migration.css_parser import (
    FIXED_WIDTH_THRESHOLD_PX,
    LayoutIntent,
    LayoutIssue,
    classify_layout_intent,
    find_layout_breaking_elements,
    parse_inline_style,
    serialize_inline_style,
)


# ─── parse_inline_style ───────────────────────────────────────────────────────


class TestParseInlineStyle:
    def test_basic_single_property(self):
        assert parse_inline_style("float: left") == {"float": "left"}

    def test_multiple_properties(self):
        result = parse_inline_style("float: left; margin: 0 auto; color: red")
        assert result == {"float": "left", "margin": "0 auto", "color": "red"}

    def test_trailing_semicolon(self):
        assert parse_inline_style("float: left;") == {"float": "left"}

    def test_empty_string(self):
        assert parse_inline_style("") == {}

    def test_whitespace_only(self):
        assert parse_inline_style("   ") == {}

    def test_key_is_lowercased(self):
        result = parse_inline_style("Float: Left")
        assert "float" in result
        assert result["float"] == "Left"

    def test_key_whitespace_stripped(self):
        result = parse_inline_style("  float  :  left  ")
        assert result["float"] == "left"

    def test_malformed_declaration_skipped(self):
        # "float" with no colon or value is silently ignored
        result = parse_inline_style("float; color: red")
        assert result == {"color": "red"}

    def test_last_wins_on_duplicate_key(self):
        result = parse_inline_style("color: red; color: blue")
        assert result["color"] == "blue"

    def test_value_preserves_case(self):
        result = parse_inline_style("font-family: Arial, Sans-Serif")
        assert result["font-family"] == "Arial, Sans-Serif"

    def test_colon_in_value(self):
        # Values containing a colon (e.g. URLs) should be preserved in full
        result = parse_inline_style("background: url(http://example.com/img.png)")
        assert result["background"] == "url(http://example.com/img.png)"

    def test_empty_declaration_between_semicolons(self):
        result = parse_inline_style("float: left;; color: red")
        assert result == {"float": "left", "color": "red"}


# ─── serialize_inline_style ───────────────────────────────────────────────────


class TestSerializeInlineStyle:
    def test_empty_dict_returns_empty_string(self):
        assert serialize_inline_style({}) == ""

    def test_single_property(self):
        assert serialize_inline_style({"float": "left"}) == "float: left;"

    def test_multiple_properties_ends_with_semicolon(self):
        result = serialize_inline_style({"float": "left", "margin": "0 auto"})
        assert result.endswith(";")
        assert "float: left" in result
        assert "margin: 0 auto" in result

    def test_roundtrip(self):
        original = "float: left; margin-top: 8px; color: red"
        props = parse_inline_style(original)
        serialized = serialize_inline_style(props)
        # Parse again and compare dicts (order may differ)
        assert parse_inline_style(serialized) == props


# ─── classify_layout_intent ───────────────────────────────────────────────────


class TestClassifyLayoutIntent:
    def test_no_style_attr_returns_all_false(self):
        intent = classify_layout_intent('<div class="content">')
        assert intent == LayoutIntent()

    def test_float_left_detected(self):
        intent = classify_layout_intent('<div style="float: left">')
        assert intent.has_float is True
        assert intent.has_absolute_position is False

    def test_float_right_detected(self):
        intent = classify_layout_intent('<div style="float: right; margin: 0 12px">')
        assert intent.has_float is True

    def test_float_none_not_flagged(self):
        intent = classify_layout_intent('<div style="float: none">')
        assert intent.has_float is False

    def test_position_absolute_detected(self):
        intent = classify_layout_intent('<div style="position: absolute; top: 0">')
        assert intent.has_absolute_position is True

    def test_position_fixed_detected(self):
        intent = classify_layout_intent('<div style="position: fixed; top: 0">')
        assert intent.has_absolute_position is True

    def test_position_relative_not_flagged(self):
        intent = classify_layout_intent('<div style="position: relative">')
        assert intent.has_absolute_position is False

    def test_fixed_width_above_threshold(self):
        px = FIXED_WIDTH_THRESHOLD_PX + 1
        intent = classify_layout_intent(f'<div style="width: {px}px">')
        assert intent.has_fixed_width is True
        assert intent.fixed_width_px == px

    def test_fixed_width_at_threshold_not_flagged(self):
        intent = classify_layout_intent(
            f'<div style="width: {FIXED_WIDTH_THRESHOLD_PX}px">'
        )
        assert intent.has_fixed_width is False

    def test_fixed_width_below_threshold_not_flagged(self):
        intent = classify_layout_intent('<div style="width: 200px">')
        assert intent.has_fixed_width is False

    def test_width_percent_not_flagged(self):
        intent = classify_layout_intent('<div style="width: 100%">')
        assert intent.has_fixed_width is False

    def test_overflow_hidden_detected(self):
        intent = classify_layout_intent('<div style="overflow: hidden">')
        assert intent.has_overflow_control is True

    def test_overflow_scroll_detected(self):
        intent = classify_layout_intent('<div style="overflow-x: scroll">')
        assert intent.has_overflow_control is True

    def test_overflow_auto_detected(self):
        intent = classify_layout_intent('<div style="overflow-y: auto">')
        assert intent.has_overflow_control is True

    def test_overflow_visible_not_flagged(self):
        intent = classify_layout_intent('<div style="overflow: visible">')
        assert intent.has_overflow_control is False

    def test_display_flex_detected(self):
        intent = classify_layout_intent('<div style="display: flex">')
        assert intent.has_flex_or_grid is True

    def test_display_inline_flex_detected(self):
        intent = classify_layout_intent('<div style="display: inline-flex">')
        assert intent.has_flex_or_grid is True

    def test_display_grid_detected(self):
        intent = classify_layout_intent('<div style="display: grid">')
        assert intent.has_flex_or_grid is True

    def test_display_inline_grid_detected(self):
        intent = classify_layout_intent('<div style="display: inline-grid">')
        assert intent.has_flex_or_grid is True

    def test_display_block_not_flagged(self):
        intent = classify_layout_intent('<div style="display: block">')
        assert intent.has_flex_or_grid is False

    def test_column_count_detected(self):
        intent = classify_layout_intent('<div style="column-count: 2">')
        assert intent.has_multicolumn is True

    def test_columns_shorthand_detected(self):
        intent = classify_layout_intent('<div style="columns: 3 200px">')
        assert intent.has_multicolumn is True

    def test_z_index_detected(self):
        intent = classify_layout_intent('<div style="z-index: 10">')
        assert intent.has_z_index is True

    def test_multiple_flags_simultaneously(self):
        intent = classify_layout_intent(
            '<div style="position: absolute; display: flex; z-index: 99">'
        )
        assert intent.has_absolute_position is True
        assert intent.has_flex_or_grid is True
        assert intent.has_z_index is True

    def test_case_insensitive_style_attr(self):
        intent = classify_layout_intent('<DIV STYLE="Float: Left">')
        assert intent.has_float is True


# ─── LayoutIntent.is_breaking / is_notable ───────────────────────────────────


class TestLayoutIntentMethods:
    def test_is_breaking_absolute_position(self):
        intent = LayoutIntent(has_absolute_position=True)
        assert intent.is_breaking() is True

    def test_is_breaking_flex(self):
        intent = LayoutIntent(has_flex_or_grid=True)
        assert intent.is_breaking() is True

    def test_is_breaking_multicolumn(self):
        intent = LayoutIntent(has_multicolumn=True)
        assert intent.is_breaking() is True

    def test_is_breaking_float_only_is_false(self):
        # Float is notable but not "breaking" — it is preserved via inline style
        intent = LayoutIntent(has_float=True)
        assert intent.is_breaking() is False

    def test_is_breaking_overflow_only_is_false(self):
        intent = LayoutIntent(has_overflow_control=True)
        assert intent.is_breaking() is False

    def test_is_notable_all_false(self):
        assert LayoutIntent().is_notable() is False

    def test_is_notable_float(self):
        assert LayoutIntent(has_float=True).is_notable() is True

    def test_is_notable_z_index(self):
        assert LayoutIntent(has_z_index=True).is_notable() is True

    def test_is_notable_fixed_width_px_alone_is_false(self):
        # fixed_width_px without has_fixed_width should not be notable
        assert LayoutIntent(fixed_width_px=800).is_notable() is False


# ─── find_layout_breaking_elements ───────────────────────────────────────────


class TestFindLayoutBreakingElements:
    def test_no_issues_for_clean_html(self):
        html = "<div><p>Hello world</p></div>"
        assert find_layout_breaking_elements(html) == []

    def test_detects_absolute_positioned_div(self):
        html = '<div style="position: absolute; top: 10px; left: 20px;">Hidden</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].intent.has_absolute_position is True
        assert "absolute" in issues[0].description

    def test_detects_flex_container(self):
        html = (
            '<div style="display: flex; gap: 1rem;"><span>A</span><span>B</span></div>'
        )
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].intent.has_flex_or_grid is True

    def test_detects_multicolumn(self):
        html = '<div style="column-count: 3; column-gap: 20px;">Content</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].intent.has_multicolumn is True

    def test_detects_float_div_as_info(self):
        html = '<div style="float: left; width: 40%;">Sidebar</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].severity == "info"
        assert issues[0].intent.has_float is True

    def test_detects_wide_fixed_width_div(self):
        px = FIXED_WIDTH_THRESHOLD_PX + 100
        html = f'<div style="width: {px}px;">Content</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].intent.has_fixed_width is True
        assert str(px) in issues[0].description

    def test_no_issue_for_narrow_fixed_width(self):
        html = '<div style="width: 300px;">Content</div>'
        assert find_layout_breaking_elements(html) == []

    def test_img_tag_excluded(self):
        # <img> is handled by the image pipeline; should not be double-reported
        html = '<img src="x.png" style="float: left; position: absolute">'
        assert find_layout_breaking_elements(html) == []

    def test_multiple_issues_in_document(self):
        html = (
            '<div style="position: absolute">A</div>'
            "<p>Normal paragraph</p>"
            '<section style="display: grid">B</section>'
        )
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 2
        tags = {
            i.intent.has_absolute_position or i.intent.has_flex_or_grid for i in issues
        }
        assert True in tags

    def test_issue_offset_is_correct(self):
        prefix = "<div><p>intro</p>"
        styled = '<span style="position: fixed">X</span>'
        html = prefix + styled
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].offset == len(prefix)

    def test_description_contains_tag_snippet(self):
        html = '<div style="display: flex">content</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        # The issue tag_html should match the opening tag
        assert "display: flex" in issues[0].tag_html

    def test_z_index_only_reported_as_info(self):
        html = '<div style="z-index: 50">content</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].severity == "info"
        assert "z-index" in issues[0].description

    def test_plain_style_no_layout_triggers_no_issue(self):
        # color, font-size, etc. have no layout impact
        html = '<p style="color: red; font-size: 14px">Text</p>'
        assert find_layout_breaking_elements(html) == []

    def test_pipeline_authored_accordion_excluded(self):
        # <details class="migration-accordion"> with overflow: hidden is injected
        # by the pipeline itself — it must not be re-flagged as a layout issue.
        html = (
            '<details class="migration-accordion" style="overflow: hidden; border: 1px solid #ccc">'
            "<summary>Title</summary><div>Body</div></details>"
        )
        assert find_layout_breaking_elements(html) == []

    def test_pipeline_authored_equation_wrapper_excluded(self):
        # <div class="migration-display-equation"> with overflow-x: auto is also
        # pipeline-authored and should not be flagged.
        html = (
            '<div class="migration-display-equation" style="overflow-x: auto; overflow-y: hidden;">'
            '<div class="migration-display-equation__inner">x^2</div></div>'
        )
        assert find_layout_breaking_elements(html) == []

    def test_non_pipeline_element_with_overflow_still_flagged(self):
        # A <div> with overflow: hidden that is NOT pipeline-authored should still fire.
        html = '<div style="overflow: hidden; height: 200px">content</div>'
        issues = find_layout_breaking_elements(html)
        assert len(issues) == 1
        assert issues[0].intent.has_overflow_control is True


# ─── detect_layout_breaking_issues (html_tools wrapper) ──────────────────────


class TestDetectLayoutBreakingIssues:
    """Integration smoke-tests that the html_tools wrapper returns ManualReviewIssue objects."""

    def test_returns_manual_review_issues(self):
        from lms_migration.html_tools import (
            ManualReviewIssue,
            detect_layout_breaking_issues,
        )

        html = '<div style="position: absolute; top: 0">hidden</div>'
        issues = detect_layout_breaking_issues(html)
        assert len(issues) >= 1
        assert all(isinstance(i, ManualReviewIssue) for i in issues)

    def test_breaking_issues_labelled_correctly(self):
        from lms_migration.html_tools import detect_layout_breaking_issues

        html = '<div style="display: flex">content</div>'
        issues = detect_layout_breaking_issues(html)
        assert any("Layout-breaking CSS" in i.reason for i in issues)

    def test_notable_non_breaking_labelled_correctly(self):
        from lms_migration.html_tools import detect_layout_breaking_issues

        html = '<div style="float: left">sidebar</div>'
        issues = detect_layout_breaking_issues(html)
        assert any("may render differently" in i.reason for i in issues)

    def test_clean_html_returns_empty(self):
        from lms_migration.html_tools import detect_layout_breaking_issues

        html = "<div><p>Hello</p></div>"
        assert detect_layout_breaking_issues(html) == []


# ─── degrade_breaking_layout_css ─────────────────────────────────────────────


class TestDegradeBreakingLayoutCss:

    # ── position: absolute / fixed ───────────────────────────────────────────

    def test_position_absolute_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = (
            '<div style="position: absolute; top: 10px; left: 20px; color: red">x</div>'
        )
        result, abs_count, flex_count, col_count = degrade_breaking_layout_css(html)
        assert abs_count == 1
        assert flex_count == 0
        assert col_count == 0
        assert "position" not in result
        assert "top:" not in result
        assert "left:" not in result
        assert "color: red" in result

    def test_position_fixed_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: fixed; bottom: 0; z-index: 999">footer</div>'
        result, abs_count, _, _ = degrade_breaking_layout_css(html)
        assert abs_count == 1
        assert "position" not in result
        assert "bottom" not in result
        assert "z-index" not in result

    def test_position_relative_not_touched(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: relative; top: 5px">x</div>'
        result, abs_count, _, _ = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert result == html

    def test_position_static_not_touched(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: static">x</div>'
        result, abs_count, _, _ = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert result == html

    def test_all_offset_props_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: absolute; top: 0; right: 0; bottom: 0; left: 0; z-index: 50">x</div>'
        result, _, _, _ = degrade_breaking_layout_css(html)
        for prop in ("position", "top", "right", "bottom", "left", "z-index"):
            assert f"{prop}:" not in result

    def test_only_position_props_style_attr_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: absolute; top: 0; left: 0">x</div>'
        result, _, _, _ = degrade_breaking_layout_css(html)
        assert "style=" not in result

    # ── display: flex / grid ─────────────────────────────────────────────────

    def test_display_flex_degraded_to_block(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="display: flex; gap: 1rem">content</div>'
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 1
        assert "display: block" in result
        assert "gap" not in result

    def test_display_grid_degraded_to_block(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="display: grid; grid-template-columns: 1fr 1fr">x</div>'
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 1
        assert "display: block" in result
        assert "grid-template-columns" not in result

    def test_display_inline_flex_degraded(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<span style="display: inline-flex; align-items: center">x</span>'
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 1
        assert "display: block" in result
        assert "align-items" not in result

    def test_display_inline_grid_degraded(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="display: inline-grid">x</div>'
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 1
        assert "display: block" in result

    def test_display_block_not_touched(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="display: block; margin: 1rem">x</div>'
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 0
        assert result == html

    def test_flex_specific_props_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = (
            '<div style="display: flex; flex-direction: row; justify-content: space-between;'
            ' flex-wrap: wrap; align-items: center; gap: 8px; order: 2">x</div>'
        )
        result, _, flex_count, _ = degrade_breaking_layout_css(html)
        assert flex_count == 1
        for prop in (
            "flex-direction",
            "justify-content",
            "flex-wrap",
            "align-items",
            "gap",
            "order",
        ):
            assert f"{prop}:" not in result

    def test_non_flex_props_preserved_after_flex_degradation(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="display: flex; color: blue; margin: 8px">x</div>'
        result, _, _, _ = degrade_breaking_layout_css(html)
        assert "color: blue" in result
        assert "margin: 8px" in result

    # ── column-count / columns ───────────────────────────────────────────────

    def test_column_count_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="column-count: 3; column-gap: 20px">x</div>'
        result, _, _, col_count = degrade_breaking_layout_css(html)
        assert col_count == 1
        assert "column-count" not in result
        assert "column-gap" not in result

    def test_columns_shorthand_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="columns: 3 200px">x</div>'
        result, _, _, col_count = degrade_breaking_layout_css(html)
        assert col_count == 1
        assert "columns" not in result

    def test_column_rule_props_removed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="column-count: 2; column-rule: 1px solid red; column-width: 200px">x</div>'
        result, _, _, _ = degrade_breaking_layout_css(html)
        for prop in ("column-count", "column-rule", "column-width"):
            assert f"{prop}:" not in result

    # ── edge cases ───────────────────────────────────────────────────────────

    def test_multiple_patterns_on_same_element(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="position: absolute; display: flex; top: 0">x</div>'
        result, abs_count, flex_count, _ = degrade_breaking_layout_css(html)
        assert abs_count == 1
        assert flex_count == 1
        assert "position" not in result
        assert "display: block" in result

    def test_pipeline_authored_element_skipped(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<details class="migration-accordion" style="position: absolute; display: flex">x</details>'
        result, abs_count, flex_count, _ = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert flex_count == 0
        assert result == html

    def test_safe_css_untouched(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<p style="color: red; font-size: 14px; margin: 0">text</p>'
        result, abs_count, flex_count, col_count = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert flex_count == 0
        assert col_count == 0
        assert result == html

    def test_float_layout_not_degraded(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<div style="float: left; margin: 0 12px">sidebar</div>'
        result, abs_count, flex_count, col_count = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert flex_count == 0
        assert col_count == 0
        assert result == html

    def test_returns_correct_multi_element_counts(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = (
            '<div style="position: absolute">A</div>'
            '<div style="display: flex">B</div>'
            '<div style="display: grid">C</div>'
            '<div style="column-count: 2">D</div>'
            '<p style="color: red">E</p>'
        )
        _, abs_count, flex_count, col_count = degrade_breaking_layout_css(html)
        assert abs_count == 1
        assert flex_count == 2
        assert col_count == 1

    def test_img_tag_not_processed(self):
        from lms_migration.css_parser import degrade_breaking_layout_css

        html = '<img src="x.png" style="position: absolute; display: flex">'
        result, abs_count, flex_count, _ = degrade_breaking_layout_css(html)
        assert abs_count == 0
        assert flex_count == 0
        assert result == html

    # ── apply_canvas_sanitizer integration ───────────────────────────────────

    def test_sanitizer_logs_applied_change_for_absolute(self):
        from lms_migration.html_tools import apply_canvas_sanitizer

        html = (
            '<html><body><div style="position: absolute; top: 0">x</div></body></html>'
        )
        _, changes = apply_canvas_sanitizer(html)
        descs = [c.description for c in changes]
        assert any("position: absolute" in d for d in descs)

    def test_sanitizer_logs_applied_change_for_flex(self):
        from lms_migration.html_tools import apply_canvas_sanitizer

        html = '<html><body><div style="display: flex; gap: 1rem">x</div></body></html>'
        _, changes = apply_canvas_sanitizer(html)
        descs = [c.description for c in changes]
        assert any("flex/grid" in d for d in descs)

    def test_sanitizer_logs_applied_change_for_multicolumn(self):
        from lms_migration.html_tools import apply_canvas_sanitizer

        html = '<html><body><div style="column-count: 3">x</div></body></html>'
        _, changes = apply_canvas_sanitizer(html)
        descs = [c.description for c in changes]
        assert any("multi-column" in d for d in descs)

    def test_sanitizer_no_change_logged_for_clean_html(self):
        from lms_migration.html_tools import apply_canvas_sanitizer

        html = '<html><body><div style="color: red; margin: 1rem">x</div></body></html>'
        _, changes = apply_canvas_sanitizer(html)
        descs = [c.description for c in changes]
        assert not any(
            "position: absolute" in d or "flex/grid" in d or "multi-column" in d
            for d in descs
        )

    def test_sanitizer_policy_flag_disables_degradation(self):
        from lms_migration.html_tools import (
            CanvasSanitizerPolicy,
            apply_canvas_sanitizer,
        )

        html = '<html><body><div style="display: flex">x</div></body></html>'
        policy = CanvasSanitizerPolicy(degrade_breaking_layout_css=False)
        result, changes = apply_canvas_sanitizer(html, policy)
        assert "display: flex" in result
        descs = [c.description for c in changes]
        assert not any("flex/grid" in d for d in descs)


class TestWrapFloatedBlocks:
    """Tests for wrap_floated_blocks() in css_parser.py."""

    from lms_migration.css_parser import wrap_floated_blocks

    def _wrap(self, html: str):
        from lms_migration.css_parser import wrap_floated_blocks

        return wrap_floated_blocks(html)

    # ── basic wrapping ────────────────────────────────────────────────────────

    def test_float_left_div_is_wrapped(self):
        html = '<div style="float: left; width: 40%">content</div>'
        result, count = self._wrap(html)
        assert count == 1
        assert 'class="migration-clearfix"' in result
        assert "overflow: hidden" in result
        # Original element is preserved inside
        assert '<div style="float: left; width: 40%">content</div>' in result

    def test_float_right_div_is_wrapped(self):
        html = '<div style="float: right">sidebar</div>'
        result, count = self._wrap(html)
        assert count == 1
        assert "migration-clearfix" in result

    def test_float_section_is_wrapped(self):
        html = '<section style="float: left">x</section>'
        result, count = self._wrap(html)
        assert count == 1
        assert "migration-clearfix" in result

    def test_float_article_is_wrapped(self):
        html = '<article style="float: left">x</article>'
        result, count = self._wrap(html)
        assert count == 1

    def test_float_aside_is_wrapped(self):
        html = '<aside style="float: right">x</aside>'
        result, count = self._wrap(html)
        assert count == 1

    def test_float_figure_is_wrapped(self):
        html = '<figure style="float: left">x</figure>'
        result, count = self._wrap(html)
        assert count == 1

    # ── non-floating elements not touched ─────────────────────────────────────

    def test_non_floating_div_not_wrapped(self):
        html = '<div style="color: red">content</div>'
        result, count = self._wrap(html)
        assert count == 0
        assert "migration-clearfix" not in result
        assert result == html

    def test_no_style_attr_not_wrapped(self):
        html = "<div>content</div>"
        result, count = self._wrap(html)
        assert count == 0
        assert result == html

    def test_float_none_not_wrapped(self):
        html = '<div style="float: none">content</div>'
        result, count = self._wrap(html)
        assert count == 0

    # ── excluded tag types ────────────────────────────────────────────────────

    def test_img_float_not_wrapped(self):
        html = '<img style="float: left" src="x.png">'
        result, count = self._wrap(html)
        assert count == 0
        assert result == html

    def test_span_float_not_wrapped(self):
        # span is not in the eligible tag list
        html = '<span style="float: left">x</span>'
        result, count = self._wrap(html)
        assert count == 0
        assert result == html

    def test_table_cell_float_not_wrapped(self):
        html = '<td style="float: left">cell</td>'
        result, count = self._wrap(html)
        assert count == 0

    # ── pipeline-authored elements skipped ────────────────────────────────────

    def test_migration_class_element_skipped(self):
        html = '<div class="migration-accordion" style="float: left">x</div>'
        result, count = self._wrap(html)
        assert count == 0
        assert result == html

    def test_migration_clearfix_not_double_wrapped(self):
        # An element already wrapped in a clearfix container should not be
        # re-wrapped on a second pass.
        html = (
            '<div class="migration-clearfix" style="overflow: hidden;">'
            '<div style="float: left">x</div>'
            "</div>"
        )
        result, count = self._wrap(html)
        assert count == 0
        assert result == html

    # ── multi-element and nested content ──────────────────────────────────────

    def test_two_floated_divs_both_wrapped(self):
        html = (
            '<div style="float: left">A</div>'
            "<p>middle</p>"
            '<div style="float: right">B</div>'
        )
        result, count = self._wrap(html)
        assert count == 2
        assert result.count("migration-clearfix") == 2

    def test_nested_float_div_not_double_counted(self):
        # Only the outermost floated div should be wrapped; the inner float is
        # inside the already-wrapped region and must not produce a second wrapper.
        html = (
            '<div style="float: left">'
            '<div style="float: right">inner</div>'
            "outer text"
            "</div>"
        )
        result, count = self._wrap(html)
        assert count == 1
        assert result.count("migration-clearfix") == 1

    def test_content_before_and_after_preserved(self):
        html = "<p>before</p>" '<div style="float: left">x</div>' "<p>after</p>"
        result, count = self._wrap(html)
        assert count == 1
        assert result.startswith("<p>before</p>")
        assert result.endswith("<p>after</p>")

    # ── count accuracy ────────────────────────────────────────────────────────

    def test_count_zero_for_clean_html(self):
        html = "<p>nothing to float here</p>"
        _, count = self._wrap(html)
        assert count == 0

    def test_count_matches_wrapped_elements(self):
        html = "".join(f'<div style="float: left">item {i}</div>' for i in range(4))
        _, count = self._wrap(html)
        assert count == 4

    # ── sanitizer integration ─────────────────────────────────────────────────

    def test_sanitizer_wraps_floated_div(self):
        from lms_migration.html_tools import apply_canvas_sanitizer

        html = '<html><body><div style="float: left; width: 30%">sidebar</div><p>main</p></body></html>'
        result, changes = apply_canvas_sanitizer(html)
        assert "migration-clearfix" in result
        descs = [c.description for c in changes]
        assert any("clearfix" in d.lower() for d in descs)

    def test_sanitizer_policy_flag_disables_wrapping(self):
        from lms_migration.html_tools import (
            CanvasSanitizerPolicy,
            apply_canvas_sanitizer,
        )

        html = '<html><body><div style="float: left">x</div></body></html>'
        policy = CanvasSanitizerPolicy(wrap_floated_content_blocks=False)
        result, changes = apply_canvas_sanitizer(html, policy)
        assert "migration-clearfix" not in result
        descs = [c.description for c in changes]
        assert not any("clearfix" in d.lower() for d in descs)

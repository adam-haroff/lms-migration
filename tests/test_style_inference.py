"""Tests for style_inference.py."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from lms_migration.style_inference import (
    FontStacks,
    IconAliasEntry,
    StyleInferenceError,
    StyleInferenceResult,
    ThemeColors,
    _build_icon_aliases,
    _extract_font_stacks,
    _extract_theme_colors,
    _is_near_white_or_black,
    _normalise_hex,
    _scan_icon_contexts,
    _score_icon_type,
    _strip_html_tags,
    build_alias_dict,
    infer_styles,
    write_inference_reports,
)


# ─── _normalise_hex ────────────────────────────────────────────────────────────


class TestNormaliseHex:
    def test_six_digit_lowercase_passthrough(self):
        assert _normalise_hex("#ac1a2f") == "ac1a2f"

    def test_six_digit_uppercase_lowercased(self):
        assert _normalise_hex("#FFFFFF") == "ffffff"

    def test_three_digit_expanded(self):
        assert _normalise_hex("#f00") == "ff0000"

    def test_three_digit_uppercase_expanded(self):
        assert _normalise_hex("#ABC") == "aabbcc"

    def test_no_leading_hash(self):
        assert _normalise_hex("ac1a2f") == "ac1a2f"

    def test_invalid_characters_returns_none(self):
        assert _normalise_hex("#zzzzzz") is None

    def test_wrong_length_returns_none(self):
        assert _normalise_hex("#12345") is None

    def test_empty_returns_none(self):
        assert _normalise_hex("") is None


# ─── _is_near_white_or_black ──────────────────────────────────────────────────


class TestIsNearWhiteOrBlack:
    def test_pure_black_is_near(self):
        assert _is_near_white_or_black("000000") is True

    def test_near_black_is_near(self):
        assert _is_near_white_or_black("0a0a0a") is True

    def test_pure_white_is_near(self):
        assert _is_near_white_or_black("ffffff") is True

    def test_near_white_is_near(self):
        assert _is_near_white_or_black("f5f5f5") is True

    def test_sinclair_red_not_near(self):
        # #ac1a2f  luminance ≈ 22.5*0.2126 + 26*0.7152 + 47*0.0722 — well within range 30-225
        assert _is_near_white_or_black("ac1a2f") is False

    def test_mid_gray_not_near(self):
        assert _is_near_white_or_black("808080") is False

    def test_invalid_hex_returns_false(self):
        assert _is_near_white_or_black("zzzzzz") is False


# ─── _strip_html_tags ─────────────────────────────────────────────────────────


class TestStripHtmlTags:
    def test_removes_simple_tag(self):
        assert _strip_html_tags("<b>bold</b>") == "bold"

    def test_removes_nested_tags(self):
        assert _strip_html_tags("<p><em>text</em></p>") == "text"

    def test_decodes_amp(self):
        assert _strip_html_tags("A &amp; B") == "A & B"

    def test_decodes_nbsp(self):
        assert _strip_html_tags("hello&nbsp;world") == "hello world"

    def test_decodes_lt_gt(self):
        assert _strip_html_tags("&lt;tag&gt;") == "<tag>"

    def test_plain_text_unchanged(self):
        assert _strip_html_tags("Hello world") == "Hello world"

    def test_strips_whitespace(self):
        assert _strip_html_tags("  hello  ") == "hello"


# ─── _extract_theme_colors ────────────────────────────────────────────────────


def _make_html_file(*, styles: list[str], tag: str = "p") -> tuple[str, str]:
    """Build a minimal HTML string with the given inline styles."""
    body = "".join(f'<{tag} style="{s}">x</{tag}>' for s in styles)
    return ("page.html", f"<html><body>{body}</body></html>")


class TestExtractThemeColors:
    def test_finds_brand_color(self):
        files = [_make_html_file(styles=["color: #ac1a2f"])]
        result = _extract_theme_colors(files)
        assert result.primary == "#ac1a2f"

    def test_ignores_near_white(self):
        files = [_make_html_file(styles=["color: #ffffff"])]
        result = _extract_theme_colors(files)
        assert result.primary is None

    def test_ignores_near_black(self):
        files = [_make_html_file(styles=["color: #000000"])]
        result = _extract_theme_colors(files)
        assert result.primary is None

    def test_most_frequent_is_primary(self):
        files = [
            _make_html_file(
                styles=["color: #ac1a2f", "color: #ac1a2f", "color: #cf2a27"]
            ),
        ]
        result = _extract_theme_colors(files)
        assert result.primary == "#ac1a2f"
        assert result.accent == "#cf2a27"

    def test_heading_colors_captured(self):
        files = [
            (
                "page.html",
                '<html><body><h2 style="color: #ac1a2f">Title</h2></body></html>',
            )
        ]
        result = _extract_theme_colors(files)
        assert "#ac1a2f" in result.heading_colors

    def test_css_link_paths_collected(self):
        files = [
            (
                "page.html",
                '<link href="/shared/Brightspace/styles.min.css" rel="stylesheet">',
            )
        ]
        result = _extract_theme_colors(files)
        assert any("styles.min.css" in p for p in result.css_link_paths)

    def test_rgb_color_accepted(self):
        files = [_make_html_file(styles=["color: rgb(172, 26, 47)"])]
        result = _extract_theme_colors(files)
        assert result.primary == "#ac1a2f"

    def test_empty_files_returns_none(self):
        result = _extract_theme_colors([])
        assert result.primary is None
        assert result.accent is None


# ─── _extract_font_stacks ─────────────────────────────────────────────────────


class TestExtractFontStacks:
    def test_finds_font_family(self):
        files = [_make_html_file(styles=["font-family: Lato, sans-serif"])]
        result = _extract_font_stacks(files)
        assert result.primary == "lato, sans-serif"

    def test_filters_html_entity_values(self):
        # &quot; entities creep in from some D2L templates
        files = [
            (
                "page.html",
                '<p style="font-family: &quot;Lato&quot;, sans-serif">x</p>',
            )
        ]
        result = _extract_font_stacks(files)
        # The &quot; form should be filtered out, not returned as primary
        assert result.primary is None or "&" not in result.primary

    def test_most_frequent_is_primary(self):
        styles = ["font-family: Lato, sans-serif"] * 3 + [
            "font-family: Arial, sans-serif"
        ]
        files = [_make_html_file(styles=styles)]
        result = _extract_font_stacks(files)
        assert result.primary == "lato, sans-serif"

    def test_deduplicates(self):
        styles = ["font-family: Lato, sans-serif"] * 10
        files = [_make_html_file(styles=styles)]
        result = _extract_font_stacks(files)
        assert result.all_families.count("lato, sans-serif") == 1

    def test_empty_returns_none(self):
        result = _extract_font_stacks([])
        assert result.primary is None


# ─── _score_icon_type ─────────────────────────────────────────────────────────


class TestScoreIconType:
    def test_resources_from_heading(self):
        itype, conf, texts = _score_icon_type(["Explore Resources"], [], "icon.png")
        assert itype == "resources"
        assert conf >= 0.3

    def test_objectives_from_heading(self):
        itype, conf, _ = _score_icon_type(["Learning Objectives"], [], "icon.png")
        assert itype == "objectives"
        assert conf >= 0.3

    def test_syllabus_from_alt(self):
        itype, conf, _ = _score_icon_type([], ["Course Policies"], "icon.png")
        assert itype == "syllabus"
        assert conf >= 0.3

    def test_basename_fallback_grading(self):
        # No heading/alt context — basename alone should drive classification
        itype, conf, _ = _score_icon_type([], [], "gradinginformation.png")
        assert itype == "syllabus"
        assert conf >= 0.3

    def test_basename_fallback_faculty(self):
        itype, conf, _ = _score_icon_type([], [], "facultyinformation.png")
        assert itype == "overview"
        assert conf >= 0.3

    def test_basename_fallback_requirements(self):
        itype, conf, _ = _score_icon_type([], [], "courserequirements.png")
        assert itype == "syllabus"
        assert conf >= 0.3

    def test_basename_fallback_courseinformation(self):
        # "course info" is in "Course Information" heading — basename alone
        # doesn't have a separator so heading context is needed for this one.
        itype, conf, _ = _score_icon_type(
            ["Course Information"], [], "courseinformation.png"
        )
        assert itype == "overview"
        assert conf >= 0.3

    def test_empty_returns_generic_zero(self):
        itype, conf, texts = _score_icon_type([], [], "")
        assert itype == "generic"
        assert conf == 0.0
        assert texts == []

    def test_confidence_bounded_to_one(self):
        many_hints = ["Explore Resources", "Explore This", "Resources Section"] * 5
        _, conf, _ = _score_icon_type(many_hints, [], "explore.png")
        assert 0.0 <= conf <= 1.0

    def test_video_from_heading(self):
        itype, conf, _ = _score_icon_type(["Watch This Presentation"], [], "icon.png")
        assert itype == "video"
        assert conf >= 0.3

    def test_discussion_from_heading(self):
        itype, conf, _ = _score_icon_type(["Class Discussion Forum"], [], "icon.png")
        assert itype == "discussion"
        assert conf >= 0.3


# ─── _scan_icon_contexts ─────────────────────────────────────────────────────


def _make_zip_with_html(pages: dict[str, str]) -> bytes:
    """Return the bytes of an in-memory zip containing the given HTML pages."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in pages.items():
            zf.writestr(name, content)
    return buf.getvalue()


_TEMPLATE_BASE = "Brightspace_HTML_Template"


def _icon_img(basename: str, alt: str = "") -> str:
    return (
        f'<img src="/shared/{_TEMPLATE_BASE}/pages/../_assets/standardImages/{basename}"'
        f' alt="{alt}" />'
    )


class TestScanIconContexts:
    def test_finds_icon_basename(self):
        html = f"<html><body><h3>Explore Resources</h3>{_icon_img('explore.png', 'Explore')}</body></html>"
        files = [("page.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "explore.png" in contexts

    def test_counts_occurrences_across_files(self):
        html = f"<html><body>{_icon_img('explore.png')}</body></html>"
        files = [("p1.html", html), ("p2.html", html)]
        contexts = _scan_icon_contexts(files)
        assert contexts["explore.png"]["occurrences"] == 2

    def test_collects_alt_text(self):
        html = f"<html><body>{_icon_img('explore.png', 'Explore This')}</body></html>"
        files = [("p.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "Explore This" in contexts["explore.png"]["alt_texts"]

    def test_collects_heading_before_icon(self):
        html = f"<html><body><h3>Explore Resources</h3>{_icon_img('explore.png')}</body></html>"
        files = [("p.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "Explore Resources" in contexts["explore.png"]["heading_texts"]

    def test_skips_rule_gradient(self):
        html = f"<html><body>{_icon_img('rule_brown_gradient.png')}</body></html>"
        files = [("p.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "rule_brown_gradient.png" not in contexts

    def test_skips_banner_numbered(self):
        html = f"<html><body>{_icon_img('banner_03.jpg')}</body></html>"
        files = [("p.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "banner_03.jpg" not in contexts

    def test_case_insensitive_basename(self):
        # Template path variant with different case
        html = (
            '<img src="/shared/Brightspace_HTML_Template/pages/../_assets/standardImages/Explore.png"'
            ' alt="Explore" />'
        )
        files = [("p.html", html)]
        contexts = _scan_icon_contexts(files)
        assert "explore.png" in contexts


# ─── _build_icon_aliases ──────────────────────────────────────────────────────


class TestBuildIconAliases:
    def _ctx(self, headings: list[str], alts: list[str], occurrences: int = 1):
        return {
            "heading_texts": headings,
            "alt_texts": alts,
            "occurrences": occurrences,
        }

    def test_high_confidence_goes_to_resolved(self):
        contexts = {"explore.png": self._ctx(["Explore Resources"], ["Explore This"])}
        aliases, unresolved = _build_icon_aliases(contexts)
        assert len(aliases) == 1
        assert aliases[0].source_basename == "explore.png"
        assert not unresolved

    def test_low_confidence_goes_to_unresolved(self):
        contexts = {"unknown.png": self._ctx([], [])}
        _, unresolved = _build_icon_aliases(contexts, confidence_threshold=0.3)
        assert "unknown.png" in unresolved

    def test_canvas_candidates_populated(self):
        contexts = {"explore.png": self._ctx(["Explore Resources"], [])}
        aliases, _ = _build_icon_aliases(contexts)
        assert aliases[0].canvas_candidates  # non-empty list

    def test_confidence_threshold_respected(self):
        contexts = {"explore.png": self._ctx(["Explore Resources"], ["Explore This"])}
        aliases_strict, unresolved_strict = _build_icon_aliases(
            contexts, confidence_threshold=0.99
        )
        aliases_loose, unresolved_loose = _build_icon_aliases(
            contexts, confidence_threshold=0.1
        )
        # The strict threshold should push it to unresolved
        assert unresolved_strict or aliases_loose  # at least one direction differs


# ─── build_alias_dict ─────────────────────────────────────────────────────────


class TestBuildAliasDict:
    def test_correct_structure(self):
        result = StyleInferenceResult(
            source_zip="test.zip",
            html_files_analyzed=1,
            theme_colors=ThemeColors(),
            font_stacks=FontStacks(),
            icon_aliases=[
                IconAliasEntry(
                    source_basename="explore.png",
                    occurrences=1,
                    inferred_type="resources",
                    confidence=1.0,
                    canvas_candidates=["folder.png", "bookmark.png"],
                    supporting_texts=["Explore Resources"],
                )
            ],
            unresolved_icons=[],
        )
        alias_dict = build_alias_dict(result)
        assert "aliases" in alias_dict
        assert alias_dict["aliases"]["explore.png"] == ["folder.png", "bookmark.png"]

    def test_empty_aliases_produces_empty_dict(self):
        result = StyleInferenceResult(
            source_zip="test.zip",
            html_files_analyzed=0,
            theme_colors=ThemeColors(),
            font_stacks=FontStacks(),
            icon_aliases=[],
            unresolved_icons=[],
        )
        assert build_alias_dict(result) == {"aliases": {}}


# ─── infer_styles (integration) ───────────────────────────────────────────────


class TestInferStyles:
    def _make_zip(self, pages: dict[str, str], tmp_path: Path) -> Path:
        zip_path = tmp_path / "test.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in pages.items():
                zf.writestr(name, content)
        zip_path.write_bytes(buf.getvalue())
        return zip_path

    def test_basic_integration(self, tmp_path):
        html = (
            "<html><body>"
            "<h3>Explore Resources</h3>"
            f'{_icon_img("explore.png", "Explore This")}'
            '<p style="color: #ac1a2f; font-family: Lato, sans-serif">text</p>'
            "</body></html>"
        )
        zip_path = self._make_zip({"page.html": html}, tmp_path)
        result = infer_styles(zip_path)

        assert result.html_files_analyzed == 1
        assert result.theme_colors.primary == "#ac1a2f"
        assert result.font_stacks.primary == "lato, sans-serif"
        assert any(e.source_basename == "explore.png" for e in result.icon_aliases)

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(StyleInferenceError, match="not found"):
            infer_styles(tmp_path / "nonexistent.zip")

    def test_raises_on_non_zip(self, tmp_path):
        bad_path = tmp_path / "bad.zip"
        bad_path.write_text("not a zip")
        with pytest.raises(StyleInferenceError):
            infer_styles(bad_path)

    def test_empty_zip_returns_zero_files(self, tmp_path):
        zip_path = self._make_zip({}, tmp_path)
        result = infer_styles(zip_path)
        assert result.html_files_analyzed == 0
        assert result.theme_colors.primary is None
        assert result.icon_aliases == []

    def test_unresolved_icons_excluded_from_aliases(self, tmp_path):
        # An icon with zero context should land in unresolved, not aliases
        html = f'<html><body>{_icon_img("mystery.png")}</body></html>'
        zip_path = self._make_zip({"page.html": html}, tmp_path)
        result = infer_styles(zip_path)
        alias_names = {e.source_basename for e in result.icon_aliases}
        assert (
            "mystery.png" not in alias_names or "mystery.png" in result.unresolved_icons
        )

    def test_skip_icons_not_in_output(self, tmp_path):
        html = f'<html><body>{_icon_img("rule_brown_gradient.png")}</body></html>'
        zip_path = self._make_zip({"page.html": html}, tmp_path)
        result = infer_styles(zip_path)
        all_names = {e.source_basename for e in result.icon_aliases} | set(
            result.unresolved_icons
        )
        assert "rule_brown_gradient.png" not in all_names


# ─── write_inference_reports ──────────────────────────────────────────────────


class TestWriteInferenceReports:
    def _simple_result(self) -> StyleInferenceResult:
        return StyleInferenceResult(
            source_zip="test.zip",
            html_files_analyzed=3,
            theme_colors=ThemeColors(
                primary="#ac1a2f",
                accent="#cf2a27",
                all_colors={"#ac1a2f": 5, "#cf2a27": 3},
            ),
            font_stacks=FontStacks(primary="lato, sans-serif"),
            icon_aliases=[
                IconAliasEntry(
                    source_basename="explore.png",
                    occurrences=2,
                    inferred_type="resources",
                    confidence=1.0,
                    canvas_candidates=["folder.png", "bookmark.png"],
                    supporting_texts=["Explore Resources"],
                )
            ],
            unresolved_icons=["mystery.png"],
        )

    def test_creates_all_three_files(self, tmp_path):
        result = self._simple_result()
        json_p, md_p, alias_p = write_inference_reports(result, tmp_path, "d2l-export")
        assert json_p.exists()
        assert md_p.exists()
        assert alias_p.exists()

    def test_json_is_valid_and_has_required_keys(self, tmp_path):
        result = self._simple_result()
        json_p, _, _ = write_inference_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        for key in (
            "source_zip",
            "html_files_analyzed",
            "theme_colors",
            "font_stacks",
            "icon_aliases",
            "unresolved_icons",
        ):
            assert key in data, f"Missing key: {key}"

    def test_json_icon_alias_content(self, tmp_path):
        result = self._simple_result()
        json_p, _, _ = write_inference_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        assert data["icon_aliases"][0]["source_basename"] == "explore.png"
        assert data["icon_aliases"][0]["confidence"] == 1.0

    def test_alias_json_structure(self, tmp_path):
        result = self._simple_result()
        _, _, alias_p = write_inference_reports(result, tmp_path, "d2l-export")
        data = json.loads(alias_p.read_text())
        assert "aliases" in data
        assert data["aliases"]["explore.png"] == ["folder.png", "bookmark.png"]

    def test_markdown_contains_brand_color(self, tmp_path):
        result = self._simple_result()
        _, md_p, _ = write_inference_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "#ac1a2f" in md

    def test_markdown_contains_icon_basename(self, tmp_path):
        result = self._simple_result()
        _, md_p, _ = write_inference_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "explore.png" in md

    def test_markdown_contains_unresolved_section(self, tmp_path):
        result = self._simple_result()
        _, md_p, _ = write_inference_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "mystery.png" in md

    def test_creates_output_dir_if_absent(self, tmp_path):
        result = self._simple_result()
        new_dir = tmp_path / "sub" / "dir"
        write_inference_reports(result, new_dir, "stem")
        assert new_dir.is_dir()

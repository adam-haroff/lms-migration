"""Tests for template_merger module-ordering and home-page auto-selection features.

Covers:
  _home_page_variant
  _course_prefix_from_manifest
  _build_module_meta_xml
  _read_d2l_module_titles
  _inject_home_page
  _write_module_meta
  _write_course_settings
"""

from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lms_migration.template_merger import (
    _MODULE_META_NS,
    _TEMPLATE_CONCLUSION_TITLE,
    _TEMPLATE_INSTRUCTOR_MODULE_TITLE,
    _TEMPLATE_START_HERE_TITLE,
    _build_module_meta_xml,
    _course_prefix_from_manifest,
    _home_page_variant,
    _inject_home_page,
    _read_d2l_module_titles,
    _write_course_settings,
    _write_module_meta,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NS = {"m": _MODULE_META_NS}


def _parse_modules(xml_str: str) -> list[ET.Element]:
    """Parse module_meta XML and return list of <module> elements."""
    root = ET.fromstring(xml_str)
    return list(root)


def _module_attr(module_el: ET.Element, tag: str) -> str:
    child = module_el.find(f"m:{tag}", _NS)
    assert child is not None, f"<{tag}> not found in module"
    return (child.text or "").strip()


# ---------------------------------------------------------------------------
# _home_page_variant
# ---------------------------------------------------------------------------


class TestHomePageVariant:
    def test_acc_maps_to_bps(self):
        assert _home_page_variant("acc") == "home-page-bps.html"

    def test_com_maps_to_lcs(self):
        assert _home_page_variant("com") == "home-page-lcs.html"

    def test_mat_maps_to_stem(self):
        assert _home_page_variant("mat") == "home-page-stem.html"

    def test_vet_maps_to_hs(self):
        assert _home_page_variant("vet") == "home-page.html"

    def test_unknown_prefix_defaults_to_hs(self):
        assert _home_page_variant("zzz") == "home-page.html"

    def test_empty_prefix_defaults_to_hs(self):
        assert _home_page_variant("") == "home-page.html"

    def test_case_insensitive(self):
        assert _home_page_variant("ACC") == "home-page-bps.html"
        assert _home_page_variant("COM") == "home-page-lcs.html"
        assert _home_page_variant("MAT") == "home-page-stem.html"

    def test_bis_maps_to_bps(self):
        assert _home_page_variant("bis") == "home-page-bps.html"

    def test_edu_maps_to_lcs(self):
        assert _home_page_variant("edu") == "home-page-lcs.html"

    def test_cis_maps_to_stem(self):
        assert _home_page_variant("cis") == "home-page-stem.html"

    def test_him_maps_to_hs(self):
        assert _home_page_variant("him") == "home-page.html"


# ---------------------------------------------------------------------------
# _course_prefix_from_manifest
# ---------------------------------------------------------------------------


class TestCoursePrefixFromManifest:
    def test_extracts_acc_prefix(self, tmp_path: Path):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            '<?xml version="1.0"?><manifest>'
            "<imsmd:langstring>ACC 2321 Federal Taxation - Online Master</imsmd:langstring>"
            "</manifest>",
            encoding="utf-8",
        )
        assert _course_prefix_from_manifest(manifest) == "acc"

    def test_extracts_mat_prefix(self, tmp_path: Path):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            "<root><langstring>MAT 1450 Trigonometry</langstring></root>",
            encoding="utf-8",
        )
        assert _course_prefix_from_manifest(manifest) == "mat"

    def test_returns_empty_on_missing_file(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.xml"
        assert _course_prefix_from_manifest(missing) == ""

    def test_returns_empty_when_no_langstring_matches(self, tmp_path: Path):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            "<root><title>No prefix here</title></root>", encoding="utf-8"
        )
        assert _course_prefix_from_manifest(manifest) == ""


# ---------------------------------------------------------------------------
# _build_module_meta_xml
# ---------------------------------------------------------------------------


class TestBuildModuleMetaXml:
    def _xml(self, titles: list[str]) -> str:
        return _build_module_meta_xml(titles)

    def test_no_namespace_prefix(self):
        xml = self._xml(["Module 1"])
        assert "ns0:" not in xml

    def test_has_default_namespace(self):
        xml = self._xml([])
        assert f'xmlns="{_MODULE_META_NS}"' in xml

    def test_declaration_present(self):
        xml = self._xml([])
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_instructor_module_first(self):
        modules = _parse_modules(self._xml([]))
        assert _module_attr(modules[0], "title") == _TEMPLATE_INSTRUCTOR_MODULE_TITLE
        assert _module_attr(modules[0], "position") == "1"

    def test_instructor_module_unpublished(self):
        modules = _parse_modules(self._xml([]))
        assert _module_attr(modules[0], "workflow_state") == "unpublished"

    def test_start_here_second(self):
        modules = _parse_modules(self._xml([]))
        assert _module_attr(modules[1], "title") == _TEMPLATE_START_HERE_TITLE
        assert _module_attr(modules[1], "position") == "2"

    def test_start_here_active(self):
        modules = _parse_modules(self._xml([]))
        assert _module_attr(modules[1], "workflow_state") == "active"

    def test_course_conclusion_last(self):
        modules = _parse_modules(self._xml(["Mod A", "Mod B"]))
        assert _module_attr(modules[-1], "title") == _TEMPLATE_CONCLUSION_TITLE

    def test_content_modules_in_middle(self):
        titles = ["Unit 1", "Unit 2"]
        modules = _parse_modules(self._xml(titles))
        # indices 2 and 3 are the content modules
        assert _module_attr(modules[2], "title") == "Unit 1"
        assert _module_attr(modules[3], "title") == "Unit 2"

    def test_positions_are_sequential(self):
        titles = ["A", "B", "C"]
        modules = _parse_modules(self._xml(titles))
        for expected, el in enumerate(modules, start=1):
            assert _module_attr(el, "position") == str(expected)

    def test_conclusion_position_is_n_plus_3(self):
        titles = ["M1", "M2", "M3"]  # 3 content modules → conclusion at pos 6
        modules = _parse_modules(self._xml(titles))
        assert _module_attr(modules[-1], "position") == str(len(titles) + 3)

    def test_content_modules_active(self):
        modules = _parse_modules(self._xml(["Topic A"]))
        # index 2 is the content module
        assert _module_attr(modules[2], "workflow_state") == "active"

    def test_empty_content_modules_still_valid(self):
        xml = self._xml([])
        modules = _parse_modules(xml)
        # Should have instructor + start here + conclusion = 3
        assert len(modules) == 3
        assert _module_attr(modules[-1], "title") == _TEMPLATE_CONCLUSION_TITLE

    def test_module_count(self):
        titles = ["X", "Y", "Z"]
        modules = _parse_modules(self._xml(titles))
        assert len(modules) == len(titles) + 3  # instructor + start here + conclusion

    def test_identifiers_are_stable(self):
        xml1 = self._xml(["Mod 1"])
        xml2 = self._xml(["Mod 1"])
        assert xml1 == xml2


# ---------------------------------------------------------------------------
# _read_d2l_module_titles
# ---------------------------------------------------------------------------

_D2L_MANIFEST_TEMPLATE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
      <organizations>
        <organization default="TOC1" structure="rooted-hierarchy">
          {items}
        </organization>
      </organizations>
    </manifest>
    """
)


def _make_item(title: str) -> str:
    return f'<item identifier="i1"><title>{title}</title></item>'


class TestReadD2lModuleTitles:
    def _write_manifest(self, tmp_path: Path, item_titles: list[str]) -> Path:
        items_xml = "\n          ".join(_make_item(t) for t in item_titles)
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            _D2L_MANIFEST_TEMPLATE.format(items=items_xml), encoding="utf-8"
        )
        return tmp_path

    def test_returns_empty_on_missing_manifest(self, tmp_path: Path):
        result = _read_d2l_module_titles(tmp_path)
        assert result == []

    def test_content_modules_included(self, tmp_path: Path):
        unpack = self._write_manifest(
            tmp_path, ["Module 1: Introduction", "Module 2: Core Concepts"]
        )
        result = _read_d2l_module_titles(unpack)
        assert result == ["Module 1: Introduction", "Module 2: Core Concepts"]

    def test_shell_module_start_here_excluded(self, tmp_path: Path):
        unpack = self._write_manifest(
            tmp_path, ["Start Here", "Module 1: Introduction"]
        )
        result = _read_d2l_module_titles(unpack)
        assert "Start Here" not in result
        assert "Module 1: Introduction" in result

    def test_shell_module_course_conclusion_excluded(self, tmp_path: Path):
        unpack = self._write_manifest(
            tmp_path, ["Module 1: Introduction", "Module 16: Course Conclusion"]
        )
        result = _read_d2l_module_titles(unpack)
        assert "Module 16: Course Conclusion" not in result

    def test_shell_module_instructor_module_excluded(self, tmp_path: Path):
        unpack = self._write_manifest(
            tmp_path, ["Instructor Module (Do Not Publish)", "Module 1"]
        )
        result = _read_d2l_module_titles(unpack)
        assert "Instructor Module (Do Not Publish)" not in result

    def test_course_overview_excluded(self, tmp_path: Path):
        unpack = self._write_manifest(
            tmp_path, ["Course Overview", "Module 1", "Preparing Your Course"]
        )
        result = _read_d2l_module_titles(unpack)
        assert "Course Overview" not in result
        assert "Preparing Your Course" not in result
        assert "Module 1" in result

    def test_order_preserved(self, tmp_path: Path):
        titles = ["Unit 3", "Unit 1", "Unit 2"]
        unpack = self._write_manifest(tmp_path, titles)
        result = _read_d2l_module_titles(unpack)
        assert result == titles


# ---------------------------------------------------------------------------
# _write_course_settings
# ---------------------------------------------------------------------------


class TestWriteCourseSettings:
    def test_creates_file(self, tmp_path: Path):
        _write_course_settings(tmp_path)
        cs = tmp_path / "course_settings" / "course_settings.xml"
        assert cs.exists()

    def test_contains_default_view_wiki(self, tmp_path: Path):
        _write_course_settings(tmp_path)
        content = (tmp_path / "course_settings" / "course_settings.xml").read_text()
        assert "<default_view>wiki</default_view>" in content

    def test_idempotent(self, tmp_path: Path):
        _write_course_settings(tmp_path)
        cs = tmp_path / "course_settings" / "course_settings.xml"
        first_mtime = cs.stat().st_mtime_ns
        _write_course_settings(tmp_path)
        assert cs.stat().st_mtime_ns == first_mtime, "File should not be rewritten"


# ---------------------------------------------------------------------------
# _inject_home_page
# ---------------------------------------------------------------------------

_STUB_HOME_PAGE = textwrap.dedent(
    """\
    <!DOCTYPE html>
    <html>
    <head><title>Home</title></head>
    <body><p>Welcome</p></body>
    </html>
    """
)


class TestInjectHomePage:
    def _make_template_pages(self, variants: list[str]) -> dict[str, str]:
        return {v: _STUB_HOME_PAGE for v in variants}

    def test_writes_home_page_html(self, tmp_path: Path):
        pages = self._make_template_pages(["home-page-bps.html"])
        _inject_home_page(tmp_path, pages, "acc")
        assert (tmp_path / "wiki_content" / "home-page.html").exists()

    def test_correct_variant_used_for_bps(self, tmp_path: Path):
        bps_html = _STUB_HOME_PAGE.replace("Welcome", "BPS Welcome")
        pages = {
            "home-page-bps.html": bps_html,
            "home-page.html": _STUB_HOME_PAGE,
        }
        _inject_home_page(tmp_path, pages, "acc")
        content = (tmp_path / "wiki_content" / "home-page.html").read_text()
        assert "BPS Welcome" in content

    def test_correct_variant_used_for_lcs(self, tmp_path: Path):
        lcs_html = _STUB_HOME_PAGE.replace("Welcome", "LCS Welcome")
        pages = {
            "home-page-lcs.html": lcs_html,
            "home-page.html": _STUB_HOME_PAGE,
        }
        _inject_home_page(tmp_path, pages, "com")
        content = (tmp_path / "wiki_content" / "home-page.html").read_text()
        assert "LCS Welcome" in content

    def test_sets_front_page_true(self, tmp_path: Path):
        pages = self._make_template_pages(["home-page.html"])
        _inject_home_page(tmp_path, pages, "zzz")
        content = (tmp_path / "wiki_content" / "home-page.html").read_text()
        assert 'name="front_page"' in content.lower()
        assert 'content="true"' in content.lower()

    def test_writes_course_settings_xml(self, tmp_path: Path):
        pages = self._make_template_pages(["home-page.html"])
        _inject_home_page(tmp_path, pages, "acc")
        cs = tmp_path / "course_settings" / "course_settings.xml"
        assert cs.exists()

    def test_returns_variant_basename(self, tmp_path: Path):
        pages = self._make_template_pages(["home-page-stem.html"])
        result = _inject_home_page(tmp_path, pages, "mat")
        assert result == "home-page-stem.html"

    def test_falls_back_to_default_when_variant_missing(self, tmp_path: Path):
        pages = {"home-page.html": _STUB_HOME_PAGE}  # bps variant not present
        result = _inject_home_page(tmp_path, pages, "acc")
        # Should fall back to home-page.html
        assert result == "home-page.html"
        assert (tmp_path / "wiki_content" / "home-page.html").exists()

    def test_returns_none_when_no_template_pages(self, tmp_path: Path):
        result = _inject_home_page(tmp_path, {}, "acc")
        assert result is None


# ---------------------------------------------------------------------------
# _write_module_meta
# ---------------------------------------------------------------------------


class TestWriteModuleMeta:
    def test_creates_module_meta_xml(self, tmp_path: Path):
        _write_module_meta(tmp_path, ["Unit 1"])
        meta = tmp_path / "course_settings" / "module_meta.xml"
        assert meta.exists()

    def test_file_in_course_settings_dir(self, tmp_path: Path):
        _write_module_meta(tmp_path, [])
        assert (tmp_path / "course_settings" / "module_meta.xml").exists()

    def test_content_is_valid_xml(self, tmp_path: Path):
        _write_module_meta(tmp_path, ["Module 1"])
        meta = tmp_path / "course_settings" / "module_meta.xml"
        ET.parse(str(meta))  # should not raise

    def test_idempotent_if_already_exists(self, tmp_path: Path):
        _write_module_meta(tmp_path, ["Module 1"])
        meta = tmp_path / "course_settings" / "module_meta.xml"
        first_mtime = meta.stat().st_mtime_ns
        _write_module_meta(tmp_path, ["Module 1", "Module 2"])
        assert (
            meta.stat().st_mtime_ns == first_mtime
        ), "Should not overwrite existing file"

    def test_contains_course_conclusion(self, tmp_path: Path):
        _write_module_meta(tmp_path, [])
        content = (tmp_path / "course_settings" / "module_meta.xml").read_text()
        assert _TEMPLATE_CONCLUSION_TITLE in content

    def test_contains_d2l_module_title(self, tmp_path: Path):
        _write_module_meta(tmp_path, ["Unit 1: Overview"])
        content = (tmp_path / "course_settings" / "module_meta.xml").read_text()
        assert "Unit 1: Overview" in content

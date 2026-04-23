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
    _fill_learning_activities_page,
    _fill_module_intro,
    _extract_do_this_items_from_learning_activities,
    _home_page_variant,
    _inject_home_page,
    _read_d2l_module_titles,
    _write_course_settings,
    _write_module_meta,
    classify_page,
    run_template_merge,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NS = {"m": _MODULE_META_NS}
_TEMPLATE_PACKAGE = (
    Path(__file__).resolve().parents[1]
    / "resources/examples/template/elearn-standard-template-export-20260324.imscc"
)


def _parse_modules(xml_str: str) -> list[ET.Element]:
    """Parse module_meta XML and return list of <module> elements."""
    root = ET.fromstring(xml_str)
    return list(root)


def _module_attr(module_el: ET.Element, tag: str) -> str:
    child = module_el.find(f"m:{tag}", _NS)
    assert child is not None, f"<{tag}> not found in module"
    return (child.text or "").strip()


def _write_minimal_d2l_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "imsmanifest.xml"
    manifest.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
              <organizations>
                <organization identifier="d2l_org">
                  <item identifier="module1">
                    <title>Module 1: Sample</title>
                  </item>
                </organization>
              </organizations>
              <resources />
            </manifest>
            """
        ),
        encoding="utf-8",
    )


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


class TestFillModuleIntro:
    def test_intro_shell_does_not_insert_space_after_icon(self):
        source = (
            "<html><head><title>Intro</title></head><body>"
            "<h1>Introduction</h1><p>Welcome.</p>"
            "<h2>Objectives</h2><ul><li>Learn it</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=1,
            chapter_title="Chapter 1",
            path_seed="module-1",
        )
        assert (
            'star.png" alt="" width="45" height="45" loading="lazy"><strong>Introduction</strong>'
            in result
        )
        assert (
            'bullseye.png" alt="" width="45" height="45" loading="lazy"><strong><span style="color: #ac1a2f;">Module Objectives</span></strong>'
            in result
        )
        assert (
            'checkmark.png" alt="" width="45" height="45" loading="lazy"><span style="color: #ac1a2f;"><strong>Module Checklist</strong></span>'
            in result
        )

    def test_intro_shell_uses_extracted_checklist_items_when_present(self):
        source = (
            "<html><head><title>Intro</title></head><body>"
            "<h1>Introduction</h1><p>Welcome.</p>"
            "<h2>Objectives</h2><ul><li>Learn it</li></ul>"
            "<h2>To meet the learning objectives</h2><ul><li>Read Chapter 1</li><li>Post to discussion</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=1,
            chapter_title="Chapter 1",
            path_seed="Introduction and Objectives.html",
        )
        assert "<ol>" in result
        assert "<li><strong>Read</strong>: Chapter 1</li>" in result
        assert "<li><strong>Post</strong>: to discussion</li>" in result
        assert "Complete the items listed below as you work through this module:" not in result

    def test_intro_shell_renames_page_title_to_introduction_and_checklist(self):
        source = (
            "<html><head><title>Intro</title></head><body>"
            "<h1>Introduction</h1><p>Welcome.</p>"
            "<h2>Objectives</h2><ul><li>Learn it</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=1,
            chapter_title="Chapter 1",
            path_seed="Introduction and Objectives.html",
        )
        assert "<title>Module 1: Chapter 1: Introduction and Checklist</title>" in result

    def test_intro_shell_inserts_title_tag_when_missing(self):
        source = (
            "<html><head></head><body>"
            "<h1>Introduction</h1><p>Welcome.</p>"
            "<h2>Objectives</h2><ul><li>Learn it</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=1,
            chapter_title="Chapter 1",
            path_seed="Introduction and Objectives.html",
        )
        assert "<title>Module 1: Chapter 1: Introduction and Checklist</title>" in result

    def test_intro_shell_merges_sibling_activities_checklist_page(self):
        source = (
            "<html><head><title>Intro</title></head><body>"
            "<h3>Chapter Two: Ordained by Nature</h3>"
            "<p>Welcome.</p>"
            "<h2>Objectives</h2><ul><li>Learn it</li></ul>"
            "</body></html>"
        )
        checklist = (
            "<html><head><title>Activities Checklist</title></head><body>"
            "<p>To meet the learning objectives for this topic, you will complete these activities.</p>"
            "<ul><li>Review the <strong>Introduction and Objectives</strong> page.</li>"
            "<li>Complete the <strong>Learning Activities</strong> page.</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=2,
            chapter_title="Biological Basis",
            path_seed="Introduction and Objectives.html",
            checklist_source_html=checklist,
        )
        assert "<h3>Chapter Two: Ordained by Nature</h3>" in result
        assert "Introduction and Checklist</strong> page" not in result
        assert (
            "<li><strong>Complete</strong>: <strong>Learning Activities</strong> page</li>"
            in result
        )
        assert (
            "<title>Module 2: Chapter Two: Ordained by Nature: Introduction and Checklist</title>"
            in result
        )

    def test_intro_shell_preserves_intro_text_and_objectives_preamble_with_image(self):
        source = (
            "<html><head><title>Introduction and Objectives</title></head><body>"
            "<h3>Introduction</h3>"
            "<h3>Chapter 3: Spanning the World</h3>"
            "<p>Anthropology is a valuable discipline to help us see gender differently.</p>"
            "<p>Anthropology also makes it clear that men and women are seen as different in most cultures.</p>"
            '<p><img src="../standardImages/Rule_brown_gradient.png" alt="Horizontal Rule"></p>'
            "<h3>Objectives</h3>"
            '<p>After completing the learning activities for this topic, you will be able to:<img src="images/MargaretMead.jpg" alt="Margaret Mead" style="float: right;" width="197" height="299"></p>'
            "<ul><li>Explain some of Margaret Mead's studies.</li></ul>"
            "</body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=3,
            chapter_title="Chapter 3",
            path_seed="Introduction and Objectives.html",
        )
        assert "Refer to the course materials for an introduction to this module." not in result
        assert "Anthropology" in result
        assert "different in most cultures" in result
        assert 'src="images/MargaretMead.jpg"' in result
        assert "learning activities for this topic" in result
        assert '<hr style="clear: both;">' in result
        assert "<pstyle=" not in result.lower()

    def test_intro_shell_filters_generic_intro_and_help_checklist_items(self):
        source = (
            "<html><head><title>Introduction and Objectives</title></head><body>"
            "<h3>Introduction</h3>"
            "<p>Intro text.</p>"
            "<h3>Objectives</h3>"
            "<p>After completing the learning activities for this topic, you will be able to:</p>"
            "<ul><li>Explain the chapter topic.</li></ul>"
            "</body></html>"
        )
        checklist = (
            "<html><body><h3>Activities Checklist</h3><ul>"
            "<li>Review the Introduction and Objectives page.</li>"
            "<li>Post any questions about the course or assignments in the Discussion | Help.</li>"
            "<li>Post any questions about the course or assignments in the Course Q&amp;A.</li>"
            "<li>Complete the Quiz | Chapter 2 Vocabulary.</li>"
            "</ul></body></html>"
        )
        result = _fill_module_intro(
            source,
            module_number=2,
            chapter_title="Chapter 2",
            path_seed="Introduction and Objectives.html",
            checklist_source_html=checklist,
        )
        assert "Introduction and Checklist page" not in result
        assert "Discussion | Help" not in result
        assert "Course Q&amp;A" not in result
        assert "<strong>Complete</strong>: Quiz | Chapter 2 Vocabulary" in result


class TestLearningActivitiesClassification:
    def test_learning_activities_page_role_detected(self):
        role, module_number, chapter_title = classify_page(
            "Learning Activities1.html",
            "Learning Activities",
        )
        assert role.value == "learning_activities"
        assert module_number is None
        assert chapter_title == "Learning Activities"


class TestLearningActivitiesRebuild:
    def test_extracts_do_this_items_from_legacy_page(self):
        body = (
            '<body><p><img src="standardImages/doThis.png" alt=""></p>'
            "<ul><li>Read Chapter 1</li><li>Take Quiz 1</li></ul></body>"
        )
        items = _extract_do_this_items_from_learning_activities(body)
        assert items == ["Read Chapter 1", "Take Quiz 1"]

    def test_rebuilds_legacy_learning_activities_sections(self):
        source = (
            "<html><head><title>Learning Activities</title></head><body>"
            '<p><img src="standardImages/doThis.png" alt=""></p>'
            "<ul><li>Read Chapter 1</li></ul>"
            '<p><img src="standardImages/exploreThis.png" alt=""></p>'
            '<p><a href="https://example.com">Example resource</a></p>'
            "</body></html>"
        )
        rebuilt = _fill_learning_activities_page(
            source,
            path_seed="Learning Activities.html",
        )
        assert rebuilt is not None
        assert "template-images/icons/paper.png" in rebuilt
        assert "template-images/icons/folder.png" in rebuilt
        assert "Do This" in rebuilt
        assert "Explore This" in rebuilt
        assert "<li>Read Chapter 1</li>" in rebuilt
        assert "Example resource" in rebuilt

    def test_rebuilds_view_this_marker_from_data_template_label(self):
        source = (
            "<html><head><title>Learning Activities</title></head><body>"
            '<p><strong><img src="TemplateAssets/bookmark.png" data-template-label="View This" alt=""></strong></p>'
            '<p><a href="https://example.com/video">Video Link</a></p>'
            "</body></html>"
        )
        rebuilt = _fill_learning_activities_page(
            source,
            path_seed="Learning Activities.html",
        )
        assert rebuilt is not None
        assert "View" in rebuilt
        assert "template-images/icons/video.png" in rebuilt


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


class TestFullTemplateShell:
    def test_full_template_shell_injects_shell_resources_into_manifest(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            full_template_shell=True,
        )

        manifest = (tmp_path / "imsmanifest.xml").read_text(encoding="utf-8")
        assert "Template: Image Customizations" in manifest
        assert "Canvas Resources for Instructors" in manifest
        assert "Syllabus Quiz" in manifest
        assert "Course Q&amp;A" in manifest
        assert "wiki_content/home-page.html" in manifest
        assert "course_settings/syllabus.html" in manifest

    def test_full_template_shell_injects_course_settings_dependencies(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            full_template_shell=True,
        )

        manifest = (tmp_path / "imsmanifest.xml").read_text(encoding="utf-8")
        assert "web_resources/course_image/course-card.png" in manifest
        assert "wiki_content/syllabus-2.html" in manifest

    def test_full_template_shell_copies_reference_template_pages(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            full_template_shell=True,
        )

        assert (tmp_path / "wiki_content" / "module-1-introduction-and-checklist.html").exists()
        assert (tmp_path / "wiki_content" / "module-1-learning-activities.html").exists()
        assert (tmp_path / "wiki_content" / "module-1-lesson-title.html").exists()
        assert (tmp_path / "wiki_content" / "module-1-review.html").exists()
        assert (tmp_path / "wiki_content" / "course-summary.html").exists()
        assert (tmp_path / "wiki_content" / "syllabus-f2f.html").exists()
        assert (tmp_path / "wiki_content" / "home-page-bps.html").exists()

    def test_full_template_shell_writes_real_shell_module_meta_and_web_resources(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            full_template_shell=True,
        )

        module_meta = (tmp_path / "course_settings" / "module_meta.xml").read_text(
            encoding="utf-8"
        )
        assert "Template: Image Customizations" in module_meta
        assert "Canvas Resources for Instructors" in module_meta
        assert "Syllabus Quiz" in module_meta
        assert "Course Q&amp;A" in module_meta
        assert "Module 1: [Title or Theme Here]" in module_meta
        assert "Module 16: Introduction and Checklist" in module_meta

        home_page = (tmp_path / "wiki_content" / "home-page.html").read_text(
            encoding="utf-8"
        )
        assert "../web_resources/" in home_page
        assert "TemplateAssets/" not in home_page


class TestSeededStarterCourse:
    def test_seeded_starter_course_skips_shell_pages_and_home_page(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)
        welcome_dir = tmp_path / "CourseOverview"
        welcome_dir.mkdir(parents=True, exist_ok=True)
        welcome_page = welcome_dir / "Welcome From the Instructor.html"
        welcome_page.write_text(
            textwrap.dedent(
                """\
                <html>
                  <head><title>Welcome From the Instructor</title></head>
                  <body><p>Welcome from faculty.</p></body>
                </html>
                """
            ),
            encoding="utf-8",
        )

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            seeded_starter_course=True,
        )

        assert not (tmp_path / "wiki_content" / "home-page.html").exists()
        assert not (tmp_path / "CourseOverview" / "About the Instructor.html").exists()
        assert "Welcome from faculty." in welcome_page.read_text(encoding="utf-8")

    def test_seeded_starter_course_module_meta_contains_only_content_modules(
        self, tmp_path: Path
    ):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            textwrap.dedent(
                """\
                <?xml version="1.0" encoding="UTF-8"?>
                <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
                  <organizations>
                    <organization identifier="d2l_org">
                      <item identifier="module1"><title>Module 1: Sample</title></item>
                      <item identifier="module2"><title>Module 2: Another</title></item>
                    </organization>
                  </organizations>
                  <resources />
                </manifest>
                """
            ),
            encoding="utf-8",
        )

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            seeded_starter_course=True,
        )

        module_meta = (tmp_path / "course_settings" / "module_meta.xml").read_text(
            encoding="utf-8"
        )
        assert "Module 1: Sample" in module_meta
        assert "Module 2: Another" in module_meta
        assert _TEMPLATE_INSTRUCTOR_MODULE_TITLE not in module_meta
        assert _TEMPLATE_START_HERE_TITLE not in module_meta
        assert _TEMPLATE_CONCLUSION_TITLE not in module_meta

    def test_seeded_starter_course_injects_unmanifested_courseoverview_pages(
        self, tmp_path: Path
    ):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            textwrap.dedent(
                """\
                <?xml version="1.0" encoding="UTF-8"?>
                <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
                  <organizations>
                    <organization identifier="d2l_org">
                      <item identifier="module1"><title>Module 1: Sample</title></item>
                    </organization>
                  </organizations>
                  <resources />
                </manifest>
                """
            ),
            encoding="utf-8",
        )
        courseoverview = tmp_path / "CourseOverview"
        courseoverview.mkdir(parents=True, exist_ok=True)
        (courseoverview / "Technology and Resources for Course.html").write_text(
            textwrap.dedent(
                """\
                <html>
                  <head><title>Technology and Resources for Course</title></head>
                  <body><p>Carryover content.</p></body>
                </html>
                """
            ),
            encoding="utf-8",
        )

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            seeded_starter_course=True,
        )

        updated_manifest = manifest.read_text(encoding="utf-8")
        assert "CourseOverview\\Technology and Resources for Course.html" in updated_manifest
        assert "<title>Technology and Resources for Course</title>" in updated_manifest


class TestTemplateMergeIntroTitleSync:
    def test_run_template_merge_updates_manifest_title_for_intro_page(
        self, tmp_path: Path
    ):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            textwrap.dedent(
                """\
                <?xml version="1.0" encoding="UTF-8"?>
                <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
                  <organizations>
                    <organization identifier="d2l_org">
                      <item identifier="module1">
                        <title>Module 1: Sample</title>
                        <item identifier="intro1" identifierref="RES_INTRO">
                          <title>Module 1: Introduction and Objectives</title>
                        </item>
                      </item>
                    </organization>
                  </organizations>
                  <resources>
                    <resource identifier="RES_INTRO" href="01-Sample/Introduction and Objectives.html" />
                  </resources>
                </manifest>
                """
            ),
            encoding="utf-8",
        )
        intro_dir = tmp_path / "01-Sample"
        intro_dir.mkdir(parents=True, exist_ok=True)
        (intro_dir / "Introduction and Objectives.html").write_text(
            textwrap.dedent(
                """\
                <html>
                  <head><title>Module 1: Introduction and Objectives</title></head>
                  <body>
                    <h1>Introduction</h1>
                    <p>Welcome.</p>
                    <h2>Objectives</h2>
                    <ul><li>Learn it</li></ul>
                  </body>
                </html>
                """
            ),
            encoding="utf-8",
        )

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            seeded_starter_course=True,
        )

        updated_manifest = manifest.read_text(encoding="utf-8")
        assert "Module 1: Sample: Introduction and Checklist" in updated_manifest

    def test_run_template_merge_removes_merged_activities_checklist_page(
        self, tmp_path: Path
    ):
        manifest = tmp_path / "imsmanifest.xml"
        manifest.write_text(
            textwrap.dedent(
                """\
                <?xml version="1.0" encoding="UTF-8"?>
                <manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
                  <organizations>
                    <organization identifier="d2l_org">
                      <item identifier="module1">
                        <title>Module 1: Sample</title>
                        <item identifier="intro1" identifierref="RES_INTRO">
                          <title>Introduction and Objectives</title>
                        </item>
                        <item identifier="check1" identifierref="RES_CHECK">
                          <title>Activities Checklist</title>
                        </item>
                      </item>
                    </organization>
                  </organizations>
                  <resources>
                    <resource identifier="RES_INTRO" href="01-Sample/Introduction and Objectives.html">
                      <file href="01-Sample/Introduction and Objectives.html" />
                    </resource>
                    <resource identifier="RES_CHECK" href="01-Sample/Activities Checklist.html">
                      <file href="01-Sample/Activities Checklist.html" />
                    </resource>
                  </resources>
                </manifest>
                """
            ),
            encoding="utf-8",
        )
        intro_dir = tmp_path / "01-Sample"
        intro_dir.mkdir(parents=True, exist_ok=True)
        (intro_dir / "Introduction and Objectives.html").write_text(
            textwrap.dedent(
                """\
                <html>
                  <head><title>Introduction and Objectives</title></head>
                  <body>
                    <h3>Chapter One: Sample Heading</h3>
                    <p>Welcome.</p>
                    <h2>Objectives</h2>
                    <ul><li>Learn it</li></ul>
                  </body>
                </html>
                """
            ),
            encoding="utf-8",
        )
        (intro_dir / "Activities Checklist.html").write_text(
            textwrap.dedent(
                """\
                <html>
                  <head><title>Activities Checklist</title></head>
                  <body>
                    <ul><li>Review the <strong>Introduction and Objectives</strong> page.</li></ul>
                  </body>
                </html>
                """
            ),
            encoding="utf-8",
        )

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            seeded_starter_course=True,
        )

        updated_manifest = manifest.read_text(encoding="utf-8")
        assert "Activities Checklist" not in updated_manifest
        assert "RES_CHECK" not in updated_manifest
        assert not (intro_dir / "Activities Checklist.html").exists()
        intro_html = (intro_dir / "Introduction and Objectives.html").read_text(
            encoding="utf-8"
        )
        assert "Introduction and Checklist" in intro_html
        assert "Introduction and Checklist</strong> page" not in intro_html
        assert (
            "<li><strong>Read</strong>: all assigned content and review lecture materials</li>"
            in intro_html
        )


class TestTemplateMergeModuleMeta:

    def test_full_template_shell_rebuilds_module_meta_with_d2l_modules(
        self, tmp_path: Path
    ):
        _write_minimal_d2l_manifest(tmp_path)

        run_template_merge(
            tmp_path,
            _TEMPLATE_PACKAGE,
            full_template_shell=True,
        )

        module_meta = (tmp_path / "course_settings" / "module_meta.xml").read_text(
            encoding="utf-8"
        )
        assert "Module 1: Sample" in module_meta
        assert "Module 16: Course Conclusion" in module_meta

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

"""
Tests for new features added in the 2026 audit session:
  - detect_d2l_media_library_embeds  (Task B)
  - quiz_audit.py                    (Task A)
  - fix_checklist.py new handlers    (Tasks B + C)
  - pipeline XML audit helpers       (Task C)
  - D2L quickLink LTI detection      (Task F)
  - Rubric audit                     (Task G)
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from lms_migration.html_tools import (
    detect_d2l_media_library_embeds,
    detect_lti_embed_issues,
)
from lms_migration.fix_checklist import _map_manual_review_group
from lms_migration.quiz_audit import (
    _parse_quiz_xml,
    audit_quizzes,
    write_json_report,
    write_markdown_report,
)


# ===========================================================================
# Task B — D2L media library URL detection
# ===========================================================================


class TestDetectD2LMediaLibraryEmbeds:
    """detect_d2l_media_library_embeds() should flag pages containing D2L
    media-library URLs (ouFileId, /d2l/lp/media/, /d2l/tools/mediaLibrary/)
    as a distinct P1 issue, separate from the generic legacy-D2L-link category.
    """

    def test_ouFileId_href_detected(self):
        html = '<a href="/d2l/common/viewFile.d2lfile?ouFileId=12345">Video</a>'
        issues = detect_d2l_media_library_embeds(html)
        assert len(issues) == 1
        assert "media library" in issues[0].reason.lower()

    def test_lp_media_href_detected(self):
        html = '<a href="/d2l/lp/media/123/View">Watch video</a>'
        issues = detect_d2l_media_library_embeds(html)
        assert len(issues) == 1

    def test_media_library_tool_detected(self):
        html = '<iframe src="/d2l/tools/mediaLibrary/embed?id=99"></iframe>'
        issues = detect_d2l_media_library_embeds(html)
        assert len(issues) == 1

    def test_clean_page_returns_empty(self):
        html = '<p>No media links here.</p><a href="https://example.com">link</a>'
        issues = detect_d2l_media_library_embeds(html)
        assert issues == []

    def test_regular_d2l_link_not_flagged_as_media(self):
        # A generic D2L link that is NOT a media library URL should not be flagged
        # by THIS function (it's handled by the generic D2L link neutralizer).
        html = '<a href="/d2l/le/content/123/viewContent/456/View">Content</a>'
        issues = detect_d2l_media_library_embeds(html)
        assert issues == []

    def test_at_most_one_issue_per_page(self):
        # Even with multiple media links, only one ManualReviewIssue per page.
        html = (
            '<a href="/d2l/lp/media/1/View">V1</a>'
            '<a href="/d2l/lp/media/2/View">V2</a>'
            '&lt;img src="?ouFileId=55"&gt;'
        )
        issues = detect_d2l_media_library_embeds(html)
        assert len(issues) == 1

    def test_reason_string_matches_checklist_handler(self):
        # The reason string emitted must trigger the correct fix_checklist handler.
        html = '<a href="?ouFileId=999">File</a>'
        issues = detect_d2l_media_library_embeds(html)
        assert len(issues) == 1
        priority, category, _owner, _action = _map_manual_review_group(
            "manual_review", issues[0].reason
        )
        assert priority == "P1"
        assert category == "d2l_media_library_migration"


# ===========================================================================
# Task A — Quiz audit
# ===========================================================================

# Minimal QTI XML for ACC-2321-style quiz
_QUIZ_XML_MINIMAL = """\
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment d2l_2p0:id="99" title="Test Quiz">
    <assess_procextension>
      <d2l_2p0:time_limit>30</d2l_2p0:time_limit>
      <d2l_2p0:enforce_time_limit>yes</d2l_2p0:enforce_time_limit>
      <d2l_2p0:attempts_allowed>2</d2l_2p0:attempts_allowed>
      <d2l_2p0:date_start>2026-01-10T00:00:00</d2l_2p0:date_start>
      <d2l_2p0:date_end>2026-05-01T23:59:59</d2l_2p0:date_end>
    </assess_procextension>
    <section ident="CONTAINER_SECTION">
      <item ident="Q1">
        <itemmetadata>
          <qtimetadata>
            <qti_metadatafield>
              <fieldlabel>qmd_questiontype</fieldlabel>
              <fieldentry>Multiple Choice</fieldentry>
            </qti_metadatafield>
          </qtimetadata>
        </itemmetadata>
      </item>
      <item ident="Q2">
        <itemmetadata>
          <qtimetadata>
            <qti_metadatafield>
              <fieldlabel>qmd_questiontype</fieldlabel>
              <fieldentry>Ordering</fieldentry>
            </qti_metadatafield>
          </qtimetadata>
        </itemmetadata>
      </item>
    </section>
  </assessment>
</questestinterop>
"""

_QUIZ_XML_CLEAN = """\
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment d2l_2p0:id="5" title="Simple Quiz">
    <assess_procextension>
      <d2l_2p0:attempts_allowed>0</d2l_2p0:attempts_allowed>
    </assess_procextension>
    <section ident="S">
      <item ident="A">
        <itemmetadata>
          <qtimetadata>
            <qti_metadatafield>
              <fieldlabel>qmd_questiontype</fieldlabel>
              <fieldentry>Multiple Choice</fieldentry>
            </qti_metadatafield>
          </qtimetadata>
        </itemmetadata>
      </item>
    </section>
  </assessment>
</questestinterop>
"""


class TestParseQuizXml:
    def test_title_extracted(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.title == "Test Quiz"

    def test_quiz_id_extracted(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.quiz_id == "99"

    def test_question_count(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.question_count == 2

    def test_question_types_correct(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.question_types.get("Multiple Choice") == 1
        assert qi.question_types.get("Ordering") == 1

    def test_time_limit_extracted(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.time_limit_minutes == 30

    def test_time_limit_enforced(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.enforce_time_limit is True

    def test_attempts_extracted(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.attempts_allowed == 2

    def test_availability_window_detected(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        assert qi.has_availability_window is True
        assert "2026-01-10" in qi.date_start
        assert "2026-05-01" in qi.date_end

    def test_ordering_type_flagged_p1(self):
        qi = _parse_quiz_xml("quiz_d2l_99.xml", _QUIZ_XML_MINIMAL)
        ordering_flags = [f for f in qi.compatibility_flags if f["type"] == "Ordering"]
        assert len(ordering_flags) == 1
        assert ordering_flags[0]["level"] == "P1"

    def test_clean_quiz_no_flags(self):
        qi = _parse_quiz_xml("quiz_d2l_5.xml", _QUIZ_XML_CLEAN)
        assert qi.compatibility_flags == []

    def test_unlimited_attempts_zero(self):
        qi = _parse_quiz_xml("quiz_d2l_5.xml", _QUIZ_XML_CLEAN)
        assert qi.attempts_allowed == 0

    def test_no_availability_when_dates_absent(self):
        qi = _parse_quiz_xml("quiz_d2l_5.xml", _QUIZ_XML_CLEAN)
        assert qi.has_availability_window is False


class TestAuditQuizzes:
    """audit_quizzes() reads quiz_d2l_*.xml from a zip and returns an audit report."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_quiz_audit.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def test_finds_quiz_files(self):
        zp = self._make_zip({"quiz_d2l_99.xml": _QUIZ_XML_MINIMAL})
        report = audit_quizzes(zp)
        assert report.quiz_count == 1

    def test_skips_non_quiz_xml(self):
        zp = self._make_zip(
            {
                "quiz_d2l_99.xml": _QUIZ_XML_MINIMAL,
                "imsmanifest.xml": "<manifest/>",
                "grades_d2l.xml": "<grades/>",
            }
        )
        report = audit_quizzes(zp)
        assert report.quiz_count == 1

    def test_empty_zip_gives_no_quizzes(self):
        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        report = audit_quizzes(zp)
        assert report.quiz_count == 0
        assert report.quizzes == []

    def test_summary_flags_aggregate_ordering(self):
        zp = self._make_zip({"quiz_d2l_99.xml": _QUIZ_XML_MINIMAL})
        report = audit_quizzes(zp)
        ordering = [f for f in report.summary_flags if f["type"] == "Ordering"]
        assert len(ordering) == 1
        assert ordering[0]["level"] == "P1"

    def test_write_json_report(self, tmp_path):
        zp = self._make_zip({"quiz_d2l_99.xml": _QUIZ_XML_MINIMAL})
        report = audit_quizzes(zp)
        out = tmp_path / "test.quiz-audit.json"
        write_json_report(report, out)
        import json

        data = json.loads(out.read_text())
        assert data["quiz_count"] == 1
        assert data["quizzes"][0]["title"] == "Test Quiz"

    def test_write_markdown_report(self, tmp_path):
        zp = self._make_zip({"quiz_d2l_99.xml": _QUIZ_XML_MINIMAL})
        report = audit_quizzes(zp)
        out = tmp_path / "test.quiz-audit.md"
        write_markdown_report(report, out)
        md = out.read_text()
        assert "Test Quiz" in md
        assert "New Quizzes" in md
        assert "Ordering" in md


# ===========================================================================
# Task B — fix_checklist.py handlers
# ===========================================================================


class TestFixChecklistNewHandlers:
    def test_media_library_handler(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "D2L media library content detected — move to Canvas Studio or Files",
        )
        assert priority == "P1"
        assert category == "d2l_media_library_migration"
        assert "Canvas Studio" in action

    def test_graded_discussion_handler(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Graded discussion detected — enable scoring in Canvas Discussions",
        )
        assert priority == "P1"
        assert category == "graded_discussion_setup"
        assert "grading" in action.lower()

    def test_dropbox_assignment_handler(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "D2L Dropbox assignment detected — verify Canvas imported as Assignment and configure submission settings",
        )
        assert priority == "P1"
        assert category == "dropbox_assignment_setup"
        assert "Canvas" in action
        assert "assignment" in action.lower()

    def test_availability_window_handler(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Availability window detected in gradebook item — re-enter dates in Canvas",
        )
        assert priority == "P2"
        assert category == "assignment_availability_window"
        assert "Canvas" in action


# ===========================================================================
# Task C — pipeline XML audit helpers
# ===========================================================================


class TestPipelineXmlAuditHelpers:
    """_audit_graded_discussions and _audit_availability_windows parse D2L XML."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_xml_audit.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def test_graded_discussion_detected(self):
        from lms_migration.pipeline import _audit_graded_discussions

        disc_xml = """\
<discussion xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <forum id="1" resource_code="abc">
    <content><title>Course Forum</title></content>
    <topic id="10">
      <content><title>Week 1 Reflection</title></content>
      <grade>10</grade>
    </topic>
  </forum>
</discussion>
"""
        zp = self._make_zip({"discussion_d2l_1.xml": disc_xml})
        rows = _audit_graded_discussions(zp)
        assert len(rows) == 1
        assert "graded discussion" in rows[0]["reason"].lower()
        assert "Week 1 Reflection" in rows[0]["evidence"]

    def test_ungraded_discussion_not_flagged(self):
        from lms_migration.pipeline import _audit_graded_discussions

        disc_xml = """\
<discussion xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <forum id="1" resource_code="abc">
    <content><title>Discussion</title></content>
    <topic id="20">
      <content><title>Open Discussion</title></content>
    </topic>
  </forum>
</discussion>
"""
        zp = self._make_zip({"discussion_d2l_1.xml": disc_xml})
        rows = _audit_graded_discussions(zp)
        assert rows == []

    def test_availability_window_detected(self):
        from lms_migration.pipeline import _audit_availability_windows

        grades_xml = """\
<grades>
  <items>
    <item id="1" identifier="X">
      <name>Chapter 1 Quiz</name>
      <date_start>2026-01-15T08:00:00</date_start>
      <date_end>2026-02-01T23:59:00</date_end>
    </item>
    <item id="2" identifier="Y">
      <name>Chapter 2 Quiz</name>
    </item>
  </items>
</grades>
"""
        zp = self._make_zip({"grades_d2l.xml": grades_xml})
        rows = _audit_availability_windows(zp)
        assert len(rows) == 1
        assert "Chapter 1 Quiz" in rows[0]["evidence"]
        assert "2026-01-15" in rows[0]["evidence"]

    def test_no_availability_windows_returns_empty(self):
        from lms_migration.pipeline import _audit_availability_windows

        grades_xml = """\
<grades>
  <items>
    <item id="1"><name>Quiz</name></item>
  </items>
</grades>
"""
        zp = self._make_zip({"grades_d2l.xml": grades_xml})
        rows = _audit_availability_windows(zp)
        assert rows == []

    def test_no_discussion_xml_returns_empty(self):
        from lms_migration.pipeline import _audit_graded_discussions

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_graded_discussions(zp)
        assert rows == []


class TestAuditDropboxFolders:
    """_audit_dropbox_folders() detects D2L Dropbox assignment folders."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_dropbox_audit.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def _dropbox_xml(self, folders_xml: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<dropbox xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">'
            + folders_xml
            + "</dropbox>"
        )

    def test_basic_folder_detected(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="Essay Assignment" id="1" out_of="50.000000" '
            'grade_item="Sinclair-12345" is_hidden="false">'
            "<date_due>2026-03-15T23:59:00</date_due>"
            "</folder>"
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert len(rows) == 1
        row = rows[0]
        assert "d2l dropbox assignment" in row["reason"].lower()
        assert "Essay Assignment" in row["evidence"]
        assert "50" in row["evidence"]
        assert "2026-03-15" in row["evidence"]

    def test_folder_with_availability_window(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="Report" id="2" out_of="30" grade_item="abc" is_hidden="false">'
            "<availability_start><availability_date>2026-01-10T00:00:00</availability_date>"
            "<availability_type>0</availability_type></availability_start>"
            "<availability_end><availability_date>2026-05-01T23:59:00</availability_date>"
            "<availability_type>0</availability_type></availability_end>"
            "</folder>"
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert len(rows) == 1
        assert "open 2026-01-10" in rows[0]["evidence"]
        assert "close 2026-05-01" in rows[0]["evidence"]

    def test_folder_with_rubric(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="Project" id="3" out_of="100" grade_item="xyz" is_hidden="false">'
            '<d2l_2p0:associations><d2l_2p0:rubric isDefault="false">7</d2l_2p0:rubric>'
            "</d2l_2p0:associations>"
            "</folder>"
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert len(rows) == 1
        assert "rubric" in rows[0]["evidence"].lower()

    def test_multiple_folders(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="A1" id="1" out_of="10" grade_item="g1" is_hidden="false"/>'
            '<folder name="A2" id="2" out_of="20" grade_item="g2" is_hidden="false"/>'
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert len(rows) == 2

    def test_hidden_folder_flagged(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="Hidden" id="5" out_of="0" grade_item="" is_hidden="true"/>'
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert len(rows) == 1
        assert "hidden" in rows[0]["evidence"].lower()

    def test_no_dropbox_file_returns_empty(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_dropbox_folders(zp)
        assert rows == []

    def test_row_type_is_d2l_xml_audit(self):
        from lms_migration.pipeline import _audit_dropbox_folders

        xml = self._dropbox_xml(
            '<folder name="A" id="1" out_of="10" grade_item="g" is_hidden="false"/>'
        )
        zp = self._make_zip({"dropbox_d2l.xml": xml})
        rows = _audit_dropbox_folders(zp)
        assert rows[0]["type"] == "d2l_xml_audit"


# ===========================================================================
# Task D — Course date-shift advisory  (_audit_date_shift_items)
# Task E — Gradebook groups + drop rules  (_audit_gradebook_groups)
# ===========================================================================


class TestAuditDateShiftItems:
    """_audit_date_shift_items() inventories date-bearing objects and always
    emits the 'Course start date not in D2L export' P1 advisory row."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_date_shift.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def test_always_emits_course_start_advisory(self):
        from lms_migration.pipeline import _audit_date_shift_items

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_date_shift_items(zp)
        advisory = [r for r in rows if "course start date" in r["reason"].lower()]
        assert len(advisory) == 1
        assert advisory[0]["type"] == "d2l_xml_audit"

    def test_advisory_reason_triggers_checklist_handler(self):
        from lms_migration.pipeline import _audit_date_shift_items

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_date_shift_items(zp)
        advisory_row = next(
            r for r in rows if "course start date" in r["reason"].lower()
        )
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit", advisory_row["reason"]
        )
        assert priority == "P1"
        assert category == "canvas_date_shift_setup"

    def test_news_dates_surfaced_in_evidence(self):
        from lms_migration.pipeline import _audit_date_shift_items

        news_xml = """\
<news>
  <item id="1">
    <headline>Welcome</headline>
    <date_start>2026-01-05T09:00:00</date_start>
  </item>
  <item id="2">
    <headline>Reminder</headline>
    <date_start>2026-04-20T09:00:00</date_start>
  </item>
</news>
"""
        zp = self._make_zip({"news_d2l.xml": news_xml})
        rows = _audit_date_shift_items(zp)
        advisory = next(r for r in rows if "course start date" in r["reason"].lower())
        assert "2026-01-05" in advisory["evidence"]
        assert "2026-04-20" in advisory["evidence"]

    def test_no_news_yields_fallback_evidence(self):
        from lms_migration.pipeline import _audit_date_shift_items

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_date_shift_items(zp)
        advisory = next(r for r in rows if "course start date" in r["reason"].lower())
        assert advisory["evidence"]  # some message present
        assert "No course offering date" in advisory["evidence"]

    def test_quiz_window_detected(self):
        from lms_migration.pipeline import _audit_date_shift_items

        quiz_xml = """\
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment d2l_2p0:id="5" title="Midterm Exam">
    <assess_procextension>
      <d2l_2p0:date_start>2026-03-01T08:00:00</d2l_2p0:date_start>
      <d2l_2p0:date_end>2026-03-10T23:59:00</d2l_2p0:date_end>
    </assess_procextension>
  </assessment>
</questestinterop>
"""
        zp = self._make_zip({"quiz_d2l_5.xml": quiz_xml})
        rows = _audit_date_shift_items(zp)
        window_rows = [
            r for r in rows if "quiz availability window" in r["reason"].lower()
        ]
        assert len(window_rows) == 1
        assert "Midterm Exam" in window_rows[0]["evidence"]
        assert "2026-03-01" in window_rows[0]["evidence"]

    def test_quiz_window_reason_triggers_checklist_handler(self):
        from lms_migration.pipeline import _audit_date_shift_items

        quiz_xml = """\
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment d2l_2p0:id="5" title="Quiz">
    <assess_procextension>
      <d2l_2p0:date_start>2026-03-01T08:00:00</d2l_2p0:date_start>
    </assess_procextension>
  </assessment>
</questestinterop>
"""
        zp = self._make_zip({"quiz_d2l_5.xml": quiz_xml})
        rows = _audit_date_shift_items(zp)
        window_row = next(
            r for r in rows if "quiz availability window" in r["reason"].lower()
        )
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit", window_row["reason"]
        )
        assert priority == "P1"
        assert category == "quiz_date_window_verification"

    def test_quiz_without_dates_not_flagged(self):
        from lms_migration.pipeline import _audit_date_shift_items

        quiz_xml = """\
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment d2l_2p0:id="7" title="No Dates Quiz">
    <assess_procextension>
      <d2l_2p0:time_limit>60</d2l_2p0:time_limit>
      <d2l_2p0:date_start></d2l_2p0:date_start>
      <d2l_2p0:date_end></d2l_2p0:date_end>
    </assess_procextension>
  </assessment>
</questestinterop>
"""
        zp = self._make_zip({"quiz_d2l_7.xml": quiz_xml})
        rows = _audit_date_shift_items(zp)
        window_rows = [
            r for r in rows if "quiz availability window" in r["reason"].lower()
        ]
        assert window_rows == []


class TestAuditGradebookGroups:
    """_audit_gradebook_groups() surfaces drop rules, weights, and bonus items."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_gradebook_groups.zip")
        tmp.write_bytes(buf.read())
        return tmp

    _GRADES_XML = """\
<grades>
  <categories>
    <category id="1">
      <name>Homework</name>
      <scoring>
        <weight>20</weight>
        <low_non_bonus_drop>2</low_non_bonus_drop>
        <high_non_bonus_drop>0</high_non_bonus_drop>
      </scoring>
    </category>
    <category id="2">
      <name>Exams</name>
      <scoring>
        <weight>60</weight>
        <low_non_bonus_drop>0</low_non_bonus_drop>
        <high_non_bonus_drop>0</high_non_bonus_drop>
      </scoring>
    </category>
    <category id="3">
      <name>Quizzes</name>
      <scoring>
        <weight>20</weight>
        <low_non_bonus_drop>1</low_non_bonus_drop>
        <high_non_bonus_drop>1</high_non_bonus_drop>
      </scoring>
    </category>
  </categories>
  <items>
    <item id="99"><name>Extra Credit Project</name><is_bonus>true</is_bonus></item>
    <item id="100"><name>Regular Assignment</name><is_bonus>false</is_bonus></item>
  </items>
</grades>
"""

    def test_drop_rule_rows_emitted(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        drop_rows = [r for r in rows if "drop rule" in r["reason"].lower()]
        assert len(drop_rows) == 2  # Homework and Quizzes

    def test_homework_drop_evidence(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        hw_row = next(r for r in rows if "Homework" in r["evidence"])
        assert "drop 2 lowest" in hw_row["evidence"]
        assert "weight=20%" in hw_row["evidence"]

    def test_quiz_category_both_drops(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        quiz_row = next(r for r in rows if "Quizzes" in r["evidence"])
        assert "drop 1 lowest" in quiz_row["evidence"]
        assert "drop 1 highest" in quiz_row["evidence"]

    def test_exams_no_drop_emits_weight_row(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        weight_rows = [r for r in rows if "category weight" in r["reason"].lower()]
        assert any("Exams" in r["evidence"] for r in weight_rows)

    def test_bonus_item_flagged(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        bonus_rows = [r for r in rows if "bonus" in r["reason"].lower()]
        assert len(bonus_rows) == 1
        assert "Extra Credit Project" in bonus_rows[0]["evidence"]

    def test_non_bonus_item_not_flagged(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        bonus_rows = [r for r in rows if "bonus" in r["reason"].lower()]
        assert not any("Regular Assignment" in r["evidence"] for r in bonus_rows)

    def test_empty_grades_xml_returns_no_rows(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": "<grades/>"})
        rows = _audit_gradebook_groups(zp)
        assert rows == []

    def test_drop_rule_reason_triggers_p1_handler(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        drop_row = next(r for r in rows if "drop rule" in r["reason"].lower())
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit", drop_row["reason"]
        )
        assert priority == "P1"
        assert category == "gradebook_drop_rule_setup"

    def test_weight_reason_triggers_p1_handler(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        weight_row = next(r for r in rows if "category weight" in r["reason"].lower())
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit", weight_row["reason"]
        )
        assert priority == "P1"
        assert category == "gradebook_group_weights"

    def test_bonus_reason_triggers_p1_handler(self):
        from lms_migration.pipeline import _audit_gradebook_groups

        zp = self._make_zip({"grades_d2l.xml": self._GRADES_XML})
        rows = _audit_gradebook_groups(zp)
        bonus_row = next(r for r in rows if "bonus" in r["reason"].lower())
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit", bonus_row["reason"]
        )
        assert priority == "P1"
        assert category == "extra_credit_setup"


class TestFixChecklistGradebookAndDateHandlers:
    """Unit tests for the new fix_checklist handlers (Tasks D & E)."""

    def test_drop_rule_handler_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Gradebook category with drop rule — configure in Canvas assignment group",
        )
        assert priority == "P1"
        assert category == "gradebook_drop_rule_setup"
        assert "Rules" in action

    def test_weight_handler_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Gradebook category weight — verify in Canvas assignment group",
        )
        assert priority == "P1"
        assert category == "gradebook_group_weights"
        assert "weight" in action.lower()

    def test_extra_credit_handler_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Bonus/extra-credit grade item detected — configure in Canvas as extra credit",
        )
        assert priority == "P1"
        assert category == "extra_credit_setup"
        assert "extra credit" in action.lower()

    def test_canvas_date_shift_handler_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Course start date not in D2L export — set manually during Canvas import",
        )
        assert priority == "P1"
        assert category == "canvas_date_shift_setup"
        assert "date-shift" in action.lower() or "Adjust Events" in action

    def test_quiz_window_handler_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Quiz availability window detected — verify dates after Canvas date-shift",
        )
        assert priority == "P1"
        assert category == "quiz_date_window_verification"
        assert "date" in action.lower()


class TestPreflightChecklistXmlAuditSection:
    """_write_preflight_checklist includes D2L XML audit rows read from the CSV."""

    def test_xml_audit_section_appears_in_checklist(self, tmp_path):
        from lms_migration.pipeline import _write_preflight_checklist
        from lms_migration.policy_profiles import PolicyProfile
        import csv

        profile = PolicyProfile(
            profile_id="strict",
            description="Test",
            template_checks_enabled=False,
            sanitize_brightspace_assets=True,
            neutralize_legacy_d2l_links=True,
            use_alt_text_for_removed_template_images=False,
            repair_missing_local_references=False,
            check_instructor_notes=False,
            check_template_placeholders=False,
            check_legacy_quiz_wording=False,
            require_mc_closing_bullet=False,
            preflight_items=(),
        )

        # Write a minimal manual-review CSV with xml audit rows
        csv_path = tmp_path / "review.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["file", "type", "reason", "evidence"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "file": "grades_d2l.xml",
                    "type": "d2l_xml_audit",
                    "reason": "Gradebook category with drop rule — configure in Canvas assignment group",
                    "evidence": "Homework | drop 2 lowest | weight=20%",
                }
            )
            writer.writerow(
                {
                    "file": "news_d2l.xml",
                    "type": "d2l_xml_audit",
                    "reason": "Course start date not in D2L export — set manually during Canvas import",
                    "evidence": "No course offering date in IMSCC export.",
                }
            )
            writer.writerow(
                {
                    "file": "some.html",
                    "type": "manual_review",
                    "reason": "Legacy script blocks detected",
                    "evidence": "",
                }
            )

        # Minimal report dict
        report = {
            "input_zip": "test.zip",
            "output_zip": "test.canvas-ready.zip",
            "summary": {
                "html_files_scanned": 2,
                "html_files_changed": 1,
                "manual_review_issues": 0,
                "accessibility_issues": 0,
            },
            "files": [],
        }

        checklist_path = tmp_path / "checklist.md"
        _write_preflight_checklist(
            report, profile, checklist_path, manual_review_csv=csv_path
        )

        content = checklist_path.read_text(encoding="utf-8")
        assert "D2L XML Audit Items" in content
        assert "drop rule" in content.lower()
        assert "course start date" in content.lower()
        # The manual_review row should NOT be in the xml audit section
        assert "Legacy script" not in content

    def test_no_csv_still_writes_checklist(self, tmp_path):
        from lms_migration.pipeline import _write_preflight_checklist
        from lms_migration.policy_profiles import PolicyProfile

        profile = PolicyProfile(
            profile_id="strict",
            description="Test",
            template_checks_enabled=False,
            sanitize_brightspace_assets=True,
            neutralize_legacy_d2l_links=True,
            use_alt_text_for_removed_template_images=False,
            repair_missing_local_references=False,
            check_instructor_notes=False,
            check_template_placeholders=False,
            check_legacy_quiz_wording=False,
            require_mc_closing_bullet=False,
            preflight_items=(),
        )

        report = {
            "input_zip": "test.zip",
            "output_zip": "test.canvas-ready.zip",
            "summary": {
                "html_files_scanned": 0,
                "html_files_changed": 0,
                "manual_review_issues": 0,
                "accessibility_issues": 0,
            },
            "files": [],
        }
        checklist_path = tmp_path / "checklist.md"
        _write_preflight_checklist(report, profile, checklist_path)
        assert checklist_path.exists()
        assert "# Migration Preflight Checklist" in checklist_path.read_text(
            encoding="utf-8"
        )


# ===========================================================================
# Task F — D2L quickLink LTI detection  (detect_lti_embed_issues)
# ===========================================================================


class TestDetectD2LQuickLinkLti:
    """detect_lti_embed_issues() must also catch D2L quickLink LTI hrefs."""

    def test_quicklink_lti_href_detected(self):
        html = (
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=12345&amp;type=lti'
            '&amp;rCode=sinclairc-9999">Open Tool</a>'
        )
        issues = detect_lti_embed_issues(html)
        lti_issues = [i for i in issues if "D2L QuickLink" in i.reason]
        assert len(lti_issues) == 1

    def test_quicklink_lti_reason_matches_handler(self):
        html = (
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti'
            '&rCode=sinclairc-0001">Launch</a>'
        )
        issues = detect_lti_embed_issues(html)
        lti_issue = next(i for i in issues if "D2L QuickLink" in i.reason)
        priority, category, _owner, action = _map_manual_review_group(
            "manual_review", lti_issue.reason
        )
        assert priority == "P1"
        assert category == "lti_quicklink_reconfiguration"
        assert "rCode" in action

    def test_multiple_quicklinks_emit_one_issue_per_rcode(self):
        """Each unique rCode produces its own issue with title and rCode in the reason."""
        html = (
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti&rCode=s-1">T1</a>'
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti&rCode=s-2">T2</a>'
        )
        issues = detect_lti_embed_issues(html)
        lti_issues = [i for i in issues if "D2L QuickLink" in i.reason]
        assert len(lti_issues) == 2
        reasons = [i.reason for i in lti_issues]
        assert any("s-1" in r and "T1" in r for r in reasons)
        assert any("s-2" in r and "T2" in r for r in reasons)

    def test_duplicate_rcode_emits_only_one_issue(self):
        """The same rCode appearing twice on a page produces only one issue."""
        html = (
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti&rCode=s-1">T1</a>'
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti&rCode=s-1">T1 again</a>'
        )
        issues = detect_lti_embed_issues(html)
        lti_issues = [i for i in issues if "D2L QuickLink" in i.reason]
        assert len(lti_issues) == 1

    def test_quicklink_non_lti_not_flagged(self):
        # type=coursefile should NOT trigger the LTI detector
        html = (
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=coursefile'
            '&fileId=somefile.pdf">PDF</a>'
        )
        issues = detect_lti_embed_issues(html)
        lti_issues = [i for i in issues if "D2L QuickLink" in i.reason]
        assert lti_issues == []

    def test_clean_page_no_lti(self):
        html = "<p>No LTI links here.</p>"
        issues = detect_lti_embed_issues(html)
        assert issues == []

    def test_iframe_panopto_still_detected(self):
        html = '<iframe src="https://sinclair.panopto.com/embed?id=abc123"></iframe>'
        issues = detect_lti_embed_issues(html)
        assert any("Panopto" in i.reason for i in issues)

    def test_quicklink_lti_and_iframe_both_detected(self):
        html = (
            '<iframe src="https://sinclair.panopto.com/embed?id=abc"></iframe>'
            '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=lti&rCode=s-5">T</a>'
        )
        issues = detect_lti_embed_issues(html)
        assert any("Panopto" in i.reason for i in issues)
        assert any("D2L QuickLink" in i.reason for i in issues)

    def test_fix_checklist_quicklink_handler_beats_generic(self):
        # "D2L QuickLink" in reason → lti_quicklink_reconfiguration, not lti_embed_reconfiguration
        reason = "LTI tool embed (D2L QuickLink) — reconfigure as Canvas LTI external tool after migration"
        priority, category, _owner, action = _map_manual_review_group(
            "manual_review", reason
        )
        assert category == "lti_quicklink_reconfiguration"
        assert priority == "P1"

    def test_fix_checklist_generic_lti_still_works(self):
        reason = "LTI tool embed (Panopto) — verify launch URL after migration"
        priority, category, _owner, action = _map_manual_review_group(
            "manual_review", reason
        )
        assert category == "lti_embed_reconfiguration"
        assert "Panopto" in action


# ===========================================================================
# Task G — Rubric audit  (_audit_rubrics)
# ===========================================================================


_RUBRIC_XML_SIMPLE = """\
<rubrics schemaversion="v2011">
  <rubric id="1" resource_code="sinclairc-123" name="Essay Rubric"
          type="1" scoring_method="3" display_levels_in_des_order="False"
          state="1" visibility="0" uses_overall_score="True">
    <criteria_groups>
      <criteria_group name="Criteria" sort_order="1">
        <level_set>
          <levels>
            <level name="Excellent" sort_order="1" level_id="10" />
            <level name="Good" sort_order="2" level_id="11" />
            <level name="Poor" sort_order="3" level_id="12" />
          </levels>
        </level_set>
        <criteria>
          <criterion name="Thesis" sort_order="1">
            <cells>
              <cell level_id="10" cell_value="10.000000000"><description text_type="text/html"><text>Excellent thesis</text></description></cell>
              <cell level_id="11" cell_value="7.000000000"><description text_type="text/html"><text>Good thesis</text></description></cell>
              <cell level_id="12" cell_value="4.000000000"><description text_type="text/html"><text>Poor thesis</text></description></cell>
            </cells>
          </criterion>
          <criterion name="Evidence" sort_order="2">
            <cells>
              <cell level_id="10" cell_value="10.000000000" />
              <cell level_id="11" cell_value="7.000000000" />
              <cell level_id="12" cell_value="3.000000000" />
            </cells>
          </criterion>
        </criteria>
      </criteria_group>
    </criteria_groups>
  </rubric>
</rubrics>
"""

_RUBRIC_XML_LEVEL_BASED = """\
<rubrics schemaversion="v2011">
  <rubric id="2" resource_code="sinclairc-456" name="Discussion Rubric"
          type="1" scoring_method="2" display_levels_in_des_order="True"
          state="0" visibility="0">
    <criteria_groups>
      <criteria_group name="Criteria" sort_order="1">
        <level_set>
          <levels>
            <level name="Level 3" sort_order="1" level_id="20" level_value="3.000000000" />
            <level name="Level 2" sort_order="2" level_id="21" level_value="2.000000000" />
            <level name="Level 1" sort_order="3" level_id="22" level_value="1.000000000" />
          </levels>
        </level_set>
        <criteria>
          <criterion name="Participation" sort_order="1">
            <cells>
              <cell level_id="20" cell_value=""><description text_type="text/html"><text /></description></cell>
              <cell level_id="21" cell_value=""><description text_type="text/html"><text /></description></cell>
              <cell level_id="22" cell_value=""><description text_type="text/html"><text /></description></cell>
            </cells>
          </criterion>
        </criteria>
      </criteria_group>
    </criteria_groups>
  </rubric>
</rubrics>
"""


class TestAuditRubrics:

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_rubrics.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def test_rubric_detected(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        assert len(rows) == 1
        assert "d2l rubric detected" in rows[0]["reason"].lower()

    def test_rubric_name_in_evidence(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        assert "Essay Rubric" in rows[0]["evidence"]

    def test_criteria_count_in_evidence(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        assert "2 criteria" in rows[0]["evidence"]

    def test_custom_points_scoring_label(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        assert "custom points" in rows[0]["evidence"].lower()

    def test_level_based_rubric_flagged_for_range(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_LEVEL_BASED})
        rows = _audit_rubrics(zp)
        assert len(rows) == 1
        assert "Range" in rows[0]["evidence"] or "range" in rows[0]["evidence"].lower()

    def test_multiple_rubrics_multiple_rows(self):
        from lms_migration.pipeline import _audit_rubrics

        combined = _RUBRIC_XML_SIMPLE.replace(
            "</rubrics>", ""
        ) + _RUBRIC_XML_LEVEL_BASED.replace('<rubrics schemaversion="v2011">', "")
        zp = self._make_zip({"rubrics_d2l.xml": combined})
        rows = _audit_rubrics(zp)
        assert len(rows) == 2

    def test_no_rubrics_file_returns_empty(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_rubrics(zp)
        assert rows == []

    def test_reason_triggers_p1_handler(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        priority, category, _owner, action = _map_manual_review_group(
            "d2l_xml_audit", rows[0]["reason"]
        )
        assert priority == "P1"
        assert category == "rubric_import_setup"
        assert "Canvas" in action

    def test_rubric_row_type_is_xml_audit(self):
        from lms_migration.pipeline import _audit_rubrics

        zp = self._make_zip({"rubrics_d2l.xml": _RUBRIC_XML_SIMPLE})
        rows = _audit_rubrics(zp)
        assert rows[0]["type"] == "d2l_xml_audit"

    def test_fix_checklist_rubric_handler_direct(self):
        priority, category, _owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "D2L rubric detected — recreate in Canvas and attach to assignment",
        )
        assert priority == "P1"
        assert category == "rubric_import_setup"
        assert "Rubric" in action or "rubric" in action.lower()


class TestNewHandlers:
    """Unit tests for layout_css and instructor_note handlers added 2026-03-23."""

    def test_layout_css_handler_category(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "Layout CSS may render differently in Canvas",
        )
        assert priority == "P2"
        assert category == "layout_css_rendering_review"
        assert owner == "ID"

    def test_layout_css_handler_action_mentions_fixed_width(self):
        _p, _c, _o, action = _map_manual_review_group(
            "manual_review",
            "Layout CSS may render differently in Canvas",
        )
        assert "fixed" in action.lower() or "pixel" in action.lower()

    def test_layout_css_action_references_evidence(self):
        _p, _c, _o, action = _map_manual_review_group(
            "manual_review",
            "Layout CSS may render differently in Canvas",
        )
        assert "evidence" in action.lower()

    def test_instructor_note_handler_category(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "Instructor Note placeholder remains in content",
        )
        assert priority == "P1"
        assert category == "instructor_note_cleanup"
        assert owner == "Faculty/Course Coordinator"

    def test_instructor_note_action_mentions_placeholder(self):
        _p, _c, _o, action = _map_manual_review_group(
            "manual_review",
            "Instructor Note placeholder remains in content",
        )
        assert "placeholder" in action.lower() or "instructor" in action.lower()

    def test_quiz_window_action_references_quiz_audit_report(self):
        _p, _c, _o, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Quiz availability window detected — verify dates after Canvas date-shift",
        )
        assert "quiz-audit" in action or "quiz audit" in action.lower()

    def test_gradebook_weight_now_p1(self):
        priority, category, _owner, _action = _map_manual_review_group(
            "d2l_xml_audit",
            "Gradebook category weight — verify in Canvas assignment group",
        )
        assert priority == "P1"
        assert category == "gradebook_group_weights"


class TestDetectIframeIssues:
    """Tests for domain-aware detect_iframe_issues() and related fix_checklist handlers."""

    def test_youtube_iframe_detected(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        issues = detect_iframe_issues(html)
        assert len(issues) == 1
        assert "YouTube" in issues[0].reason

    def test_youtube_reason_mentions_captions(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        issues = detect_iframe_issues(html)
        assert (
            "caption" in issues[0].reason.lower()
            or "transcript" in issues[0].reason.lower()
        )

    def test_vimeo_iframe_detected(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = '<iframe src="https://player.vimeo.com/video/999"></iframe>'
        issues = detect_iframe_issues(html)
        assert len(issues) == 1
        assert "Vimeo" in issues[0].reason

    def test_unknown_domain_uses_generic_reason(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = '<iframe src="https://history.com/embed/12345"></iframe>'
        issues = detect_iframe_issues(html)
        assert len(issues) == 1
        assert "history.com" in issues[0].reason

    def test_multiple_youtube_embeds_grouped_as_one(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = (
            '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
            '<iframe src="https://www.youtube.com/embed/def"></iframe>'
            '<iframe src="https://www.youtube.com/embed/ghi"></iframe>'
        )
        issues = detect_iframe_issues(html)
        assert len(issues) == 1
        assert "3" in issues[0].reason or "embeds" in issues[0].reason

    def test_multiple_domains_emit_one_issue_each(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = (
            '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
            '<iframe src="https://player.vimeo.com/video/1"></iframe>'
        )
        issues = detect_iframe_issues(html)
        assert len(issues) == 2
        domains = {i.reason.split()[1].lower() for i in issues}
        assert "youtube" in domains
        assert "vimeo" in domains

    def test_no_iframe_returns_empty(self):
        from lms_migration.html_tools import detect_iframe_issues

        html = "<p>No iframes here.</p>"
        issues = detect_iframe_issues(html)
        assert issues == []

    def test_youtube_fix_checklist_p2_captions(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "Embedded YouTube video (3 embeds) — verify closed captions or provide a transcript",
        )
        assert priority == "P2"
        assert category == "a11y_video_captions"
        assert "caption" in action.lower() or "transcript" in action.lower()

    def test_vimeo_fix_checklist_p2_captions(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "Embedded Vimeo video — verify closed captions or provide a transcript",
        )
        assert priority == "P2"
        assert category == "a11y_video_captions"

    def test_generic_iframe_fix_checklist_p1(self):
        priority, category, owner, action = _map_manual_review_group(
            "manual_review",
            "Embedded iframe (history.com) — review for accessibility, security, and responsive behavior",
        )
        assert priority == "P1"
        assert category == "embedded_iframe_review"


# ===========================================================================
# _audit_quiz_question_types  (pipeline helper)
# ===========================================================================

# Minimal D2L QTI XML stub containing question-type declarations
_QTI_HEADER = """\
<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <assessment title="{title}" ident="quiz-001">
    <assess_procextension xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
      <d2l_2p0:time_limit>30</d2l_2p0:time_limit>
      <d2l_2p0:enforce_time_limit>yes</d2l_2p0:enforce_time_limit>
      <d2l_2p0:attempts_allowed>2</d2l_2p0:attempts_allowed>
    </assess_procextension>
    <section>
{questions}
    </section>
  </assessment>
</questestinterop>
"""

_QUESTION_STUB = """\
      <item>
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>qmd_questiontype</fieldlabel>
              <fieldentry>{qtype}</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
      </item>"""


def _make_quiz_zip(quiz_files: dict[str, str]) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in quiz_files.items():
            zf.writestr(name, content)
    buf.seek(0)
    tmp = Path("/tmp/test_quiz_qt_audit.zip")
    tmp.write_bytes(buf.read())
    return tmp


def _qti_xml(title: str, qtypes: list[str]) -> str:
    questions = "\n".join(_QUESTION_STUB.format(qtype=q) for q in qtypes)
    return _QTI_HEADER.format(title=title, questions=questions)


class TestAuditQuizQuestionTypes:
    """_audit_quiz_question_types() emits rows for at-risk question types."""

    def test_p1_ordering_question_detected(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Quiz 1", ["Multiple Choice", "Ordering"])
        zp = _make_quiz_zip({"quiz_d2l_001.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 1
        assert "ordering" in rows[0]["evidence"].lower()

    def test_p1_arithmetic_question_detected(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Math Quiz", ["Arithmetic", "Multiple Choice"])
        zp = _make_quiz_zip({"quiz_d2l_002.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 1
        assert "arithmetic" in rows[0]["evidence"].lower()

    def test_p1_calculated_question_detected(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Calc Quiz", ["Calculated", "True/False"])
        zp = _make_quiz_zip({"quiz_d2l_003.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 1
        assert "calculated" in rows[0]["evidence"].lower()

    def test_p2_likert_question_detected(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Survey Quiz", ["Likert", "Multiple Choice"])
        zp = _make_quiz_zip({"quiz_d2l_004.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 1
        assert "likert" in rows[0]["evidence"].lower()

    def test_clean_quiz_not_flagged(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Clean Quiz", ["Multiple Choice", "True/False", "Matching"])
        zp = _make_quiz_zip({"quiz_d2l_005.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert rows == []

    def test_multiple_risky_types_in_one_quiz(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Mixed Quiz", ["Ordering", "Arithmetic", "Multiple Choice"])
        zp = _make_quiz_zip({"quiz_d2l_006.xml": xml})
        rows = _audit_quiz_question_types(zp)
        # One row per quiz, not per question type
        assert len(rows) == 1
        evidence = rows[0]["evidence"].lower()
        assert "ordering" in evidence
        assert "arithmetic" in evidence

    def test_multiple_quizzes_each_get_own_row(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml1 = _qti_xml("Quiz A", ["Ordering"])
        xml2 = _qti_xml("Quiz B", ["Arithmetic"])
        zp = _make_quiz_zip(
            {
                "quiz_d2l_007.xml": xml1,
                "quiz_d2l_008.xml": xml2,
            }
        )
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 2

    def test_no_quiz_files_returns_empty(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        zp = _make_quiz_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_quiz_question_types(zp)
        assert rows == []

    def test_row_type_is_d2l_xml_audit(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Q1", ["Ordering"])
        zp = _make_quiz_zip({"quiz_d2l_009.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert rows[0]["type"] == "d2l_xml_audit"

    def test_reason_contains_compatibility_risk(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Q1", ["Ordering"])
        zp = _make_quiz_zip({"quiz_d2l_010.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert "new quizzes" in rows[0]["reason"].lower()
        assert "compatibility risk" in rows[0]["reason"].lower()

    def test_p1_in_reason_when_p1_type_present(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("Q1", ["Ordering"])
        zp = _make_quiz_zip({"quiz_d2l_011.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert "(P1)" in rows[0]["reason"] or "p1" in rows[0]["reason"].lower()

    def test_significant_figures_detected(self):
        from lms_migration.pipeline import _audit_quiz_question_types

        xml = _qti_xml("SigFig Quiz", ["Significant Figures"])
        zp = _make_quiz_zip({"quiz_d2l_012.xml": xml})
        rows = _audit_quiz_question_types(zp)
        assert len(rows) == 1
        assert "significant figures" in rows[0]["evidence"].lower()


# ===========================================================================
# fix_checklist.py — new_quizzes_question_type_rebuild handler
# ===========================================================================


class TestFixChecklistNewQuizzesQuestionTypeRebuild:
    """_map_manual_review_group handles New Quizzes question-type risk rows."""

    def test_p1_category_returned_for_p1_risk(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "New Quizzes question-type compatibility risk (P1) — "
            "manual rebuild required for unsupported question types",
        )
        assert priority == "P1"
        assert category == "new_quizzes_question_type_rebuild"

    def test_p2_category_returned_for_p2_risk(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "New Quizzes question-type compatibility risk (P2) — "
            "manual rebuild required for unsupported question types",
        )
        assert priority == "P2"
        assert category == "new_quizzes_question_type_rebuild"

    def test_action_mentions_ordering_substitution(self):
        _, _, _, action = _map_manual_review_group(
            "d2l_xml_audit",
            "New Quizzes question-type compatibility risk (P1) — "
            "manual rebuild required for unsupported question types",
        )
        assert "ordering" in action.lower()

    def test_action_mentions_arithmetic_substitution(self):
        _, _, _, action = _map_manual_review_group(
            "d2l_xml_audit",
            "New Quizzes question-type compatibility risk (P1) — "
            "manual rebuild required for unsupported question types",
        )
        assert "arithmetic" in action.lower() or "calculated" in action.lower()

    def test_owner_is_faculty_or_coordinator(self):
        _, _, owner, _ = _map_manual_review_group(
            "d2l_xml_audit",
            "New Quizzes question-type compatibility risk (P1) — "
            "manual rebuild required for unsupported question types",
        )
        assert "faculty" in owner.lower() or "coordinator" in owner.lower()


# ===========================================================================
# Phase 4 item 2 — Unresolvable grade item detection
# ===========================================================================


class TestAuditUnresolvableGradeItems:
    """_audit_unresolvable_grade_items() flags grade items with no D2L submission object."""

    def _make_zip(self, files: dict[str, str]) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        tmp = Path("/tmp/test_unresolvable_grade_items.zip")
        tmp.write_bytes(buf.read())
        return tmp

    def _grades_xml(self, categories_xml: str = "", items_xml: str = "") -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<grades>"
            f"<categories>{categories_xml}</categories>"
            f"<items>{items_xml}</items>"
            "</grades>"
        )

    def test_grade_item_with_no_submission_object_is_flagged(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-999" dates_in_calendar="false">'
                "<name>Chapter 01 Homework</name>"
                "<scoring><out_of>10</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert len(rows) == 1
        row = rows[0]
        assert "unresolvable grade item" in row["reason"].lower()
        assert "Chapter 01 Homework" in row["evidence"]
        assert row["type"] == "d2l_xml_audit"

    def test_points_included_in_evidence(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-001">'
                "<name>Exam 1</name>"
                "<scoring><out_of>100</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert "100" in rows[0]["evidence"]

    def test_category_name_included_when_present(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            categories_xml=(
                '<category id="5" identifier="CAT-5">'
                "<name>Cengage Homework</name>"
                "</category>"
            ),
            items_xml=(
                '<item resource_code="sinclairc-002">'
                "<name>Ch01 HW</name>"
                "<category_id>CAT-5</category_id>"
                "<scoring><out_of>10</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            ),
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert len(rows) == 1
        assert "Cengage Homework" in rows[0]["evidence"]

    def test_bonus_item_is_skipped(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-003">'
                "<name>Bonus Points</name>"
                "<scoring><out_of>2</out_of><is_bonus>true</is_bonus></scoring>"
                "</item>"
            )
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []

    def test_item_without_resource_code_is_skipped(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item dates_in_calendar="false">'
                "<name>Final Grade</name>"
                "<scoring><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []

    def test_dropbox_linked_grade_item_is_skipped(self):
        """Grade items whose resource_code appears as grade_item in dropbox XML are already audited."""
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-dropbox-gi">'
                "<name>Essay Assignment</name>"
                "<scoring><out_of>50</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        dropbox = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<dropbox xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">'
            '<folder name="Essay" id="1" out_of="50" grade_item="sinclairc-dropbox-gi" '
            'resource_code="sinclairc-folder-rc" is_hidden="false"/>'
            "</dropbox>"
        )
        zp = self._make_zip({"grades_d2l.xml": grades, "dropbox_d2l.xml": dropbox})
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []

    def test_quiz_linked_grade_item_is_skipped(self):
        """Grade items matching a manifest quiz title are imported by Canvas QTI."""
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-quiz-grade-id">'
                "<name>Chapter 1 Quiz</name>"
                "<scoring><out_of>20</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        manifest_xml = (
            '<?xml version="1.0"?>'
            '<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"'
            ' xmlns:cp="http://www.imsglobal.org/xsd/imscp_v1p1"'
            ' xmlns:imsmd="http://ltsc.ieee.org/xsd/LOM">'
            "<organizations><organization>"
            '<item identifier="i1" resource_type_key="D2L.LE.Quizzing.Quiz">'
            "<title>Chapter 1 Quiz</title>"
            "</item>"
            "</organization></organizations>"
            "<resources/>"
            "</manifest>"
        )
        zp = self._make_zip(
            {"grades_d2l.xml": grades, "imsmanifest.xml": manifest_xml}
        )
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []

    def test_discussion_linked_grade_item_is_skipped(self):
        """Grade items matching a manifest discussion title are imported by Canvas."""
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-disc-grade-id">'
                "<name>Week 3 Discussion</name>"
                "<scoring><out_of>15</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        manifest_xml = (
            '<?xml version="1.0"?>'
            '<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"'
            ' xmlns:cp="http://www.imsglobal.org/xsd/imscp_v1p1">'
            "<organizations><organization>"
            '<item identifier="i2" resource_type_key="D2L.LE.Discussions.DiscussionTopic">'
            "<title>Week 3 Discussion</title>"
            "</item>"
            "</organization></organizations>"
            "<resources/>"
            "</manifest>"
        )
        zp = self._make_zip(
            {"grades_d2l.xml": grades, "imsmanifest.xml": manifest_xml}
        )
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []

    def test_multiple_unresolvable_items_all_returned(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        grades = self._grades_xml(
            items_xml=(
                '<item resource_code="sinclairc-a01">'
                "<name>Assignment A</name>"
                "<scoring><out_of>10</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
                '<item resource_code="sinclairc-a02">'
                "<name>Assignment B</name>"
                "<scoring><out_of>20</out_of><is_bonus>false</is_bonus></scoring>"
                "</item>"
            )
        )
        zp = self._make_zip({"grades_d2l.xml": grades})
        rows = _audit_unresolvable_grade_items(zp)
        assert len(rows) == 2

    def test_no_grades_file_returns_empty(self):
        from lms_migration.pipeline import _audit_unresolvable_grade_items

        zp = self._make_zip({"imsmanifest.xml": "<manifest/>"})
        rows = _audit_unresolvable_grade_items(zp)
        assert rows == []


class TestFixChecklistUnresolvableGradeItem:
    """_map_manual_review_group handles unresolvable grade item rows."""

    def test_correct_category_returned(self):
        priority, category, owner, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Unresolvable grade item — no D2L submission object "
            "found; create Canvas Assignment and connect to gradebook after import",
        )
        assert priority == "P1"
        assert category == "unresolvable_grade_item_setup"

    def test_action_mentions_external_tool(self):
        _, _, _, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Unresolvable grade item — no D2L submission object "
            "found; create Canvas Assignment and connect to gradebook after import",
        )
        assert "external" in action.lower() or "lti" in action.lower()

    def test_action_mentions_assignment_group(self):
        _, _, _, action = _map_manual_review_group(
            "d2l_xml_audit",
            "Unresolvable grade item — no D2L submission object "
            "found; create Canvas Assignment and connect to gradebook after import",
        )
        assert "assignment group" in action.lower()

    def test_owner_includes_faculty_or_id(self):
        _, _, owner, _ = _map_manual_review_group(
            "d2l_xml_audit",
            "Unresolvable grade item — no D2L submission object "
            "found; create Canvas Assignment and connect to gradebook after import",
        )
        assert "id" in owner.lower() or "faculty" in owner.lower()

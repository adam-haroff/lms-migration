"""Tests for blueprint_audit.py — Phase 3 Item 4."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lms_migration.blueprint_audit import (
    BlueprintAuditError,
    BlueprintAuditResult,
    DiscussionWithReplies,
    PublishMismatch,
    audit_blueprint_sync,
    write_blueprint_reports,
)
from lms_migration.canvas_api import CanvasAPIError


# ─── Fixtures ────────────────────────────────────────────────────────────────

BP_PAGES = [
    {"url": "module-1-intro", "title": "Module 1 Intro", "published": True},
    {"url": "syllabus", "title": "Syllabus", "published": True},
    {"url": "graded-policy", "title": "Grading Policy", "published": False},
]

CHILD_PAGES = [
    {"url": "module-1-intro", "title": "Module 1 Intro", "published": True},
    {"url": "syllabus", "title": "Syllabus", "published": False},  # mismatch
    {"url": "instructor-bio", "title": "Instructor Bio", "published": True},  # child-only
]

BP_ASSIGNMENTS = [
    {"name": "Homework 1", "points_possible": 20},
    {"name": "Midterm Exam", "points_possible": 100},
]

CHILD_ASSIGNMENTS = [
    {"name": "Homework 1", "points_possible": 20},
    {"name": "Extra Credit Quiz", "points_possible": 10},  # child-only
]

BP_DISCUSSIONS = [
    {"id": 1, "title": "Week 1 Discussion", "published": True, "discussion_subentry_count": 0},
]

CHILD_DISCUSSIONS = [
    {
        "id": 101,
        "title": "Week 1 Discussion",
        "published": True,
        "discussion_subentry_count": 5,
    },
    {
        "id": 102,
        "title": "Week 2 Discussion",
        "published": True,
        "discussion_subentry_count": 0,  # no replies — safe
    },
]


def _full_patch(
    bp_pages=None,
    child_pages=None,
    bp_asgn=None,
    child_asgn=None,
    bp_disc=None,
    child_disc=None,
):
    """Return a context manager that patches all three API calls with given lists."""
    bp_pages = bp_pages if bp_pages is not None else BP_PAGES
    child_pages = child_pages if child_pages is not None else CHILD_PAGES
    bp_asgn = bp_asgn if bp_asgn is not None else BP_ASSIGNMENTS
    child_asgn = child_asgn if child_asgn is not None else CHILD_ASSIGNMENTS
    bp_disc = bp_disc if bp_disc is not None else BP_DISCUSSIONS
    child_disc = child_disc if child_disc is not None else CHILD_DISCUSSIONS

    call_map: dict[str, list] = {}

    def fake_pages(*, base_url, course_id, token):
        return bp_pages if course_id == "100" else child_pages

    def fake_assignments(*, base_url, course_id, token):
        return bp_asgn if course_id == "100" else child_asgn

    def fake_discussions(*, base_url, course_id, token):
        return bp_disc if course_id == "100" else child_disc

    from unittest.mock import patch as _patch

    return (fake_pages, fake_assignments, fake_discussions)


def _run_audit(
    bp_pages=None,
    child_pages=None,
    bp_asgn=None,
    child_asgn=None,
    bp_disc=None,
    child_disc=None,
) -> BlueprintAuditResult:
    fake_pages, fake_asgn, fake_disc = _full_patch(
        bp_pages, child_pages, bp_asgn, child_asgn, bp_disc, child_disc
    )
    with (
        patch("lms_migration.blueprint_audit.fetch_course_pages", side_effect=fake_pages),
        patch("lms_migration.blueprint_audit.fetch_course_assignments", side_effect=fake_asgn),
        patch(
            "lms_migration.blueprint_audit.fetch_course_discussion_topics",
            side_effect=fake_disc,
        ),
    ):
        return audit_blueprint_sync(
            blueprint_course_id="100",
            child_course_id="200",
            base_url="https://canvas.example.edu",
            token="tok",
        )


# ─── Page comparison tests ────────────────────────────────────────────────────


class TestPageComparison:
    def test_child_only_pages_detected(self):
        result = _run_audit()
        assert "instructor-bio" in result.pages_only_in_child

    def test_child_only_does_not_include_shared(self):
        result = _run_audit()
        assert "module-1-intro" not in result.pages_only_in_child
        assert "syllabus" not in result.pages_only_in_child

    def test_blueprint_only_pages_detected(self):
        # "graded-policy" is in blueprint but not in child
        result = _run_audit()
        assert "graded-policy" in result.pages_only_in_blueprint

    def test_blueprint_only_does_not_include_shared(self):
        result = _run_audit()
        assert "module-1-intro" not in result.pages_only_in_blueprint

    def test_no_child_only_pages_when_identical(self):
        same = [{"url": "page-a", "title": "Page A", "published": True}]
        result = _run_audit(bp_pages=same, child_pages=same)
        assert result.pages_only_in_child == []

    def test_pages_only_in_child_sorted(self):
        child = [
            {"url": "zebra", "title": "Z", "published": True},
            {"url": "alpha", "title": "A", "published": True},
        ]
        result = _run_audit(bp_pages=[], child_pages=child)
        assert result.pages_only_in_child == ["alpha", "zebra"]

    def test_page_url_comparison_is_case_insensitive(self):
        bp = [{"url": "Syllabus", "title": "Syllabus", "published": True}]
        child = [{"url": "syllabus", "title": "Syllabus", "published": True}]
        result = _run_audit(bp_pages=bp, child_pages=child)
        assert result.pages_only_in_child == []
        assert result.pages_only_in_blueprint == []


# ─── Publish mismatch tests ───────────────────────────────────────────────────


class TestPublishMismatch:
    def test_mismatch_detected(self):
        result = _run_audit()
        slugs = [m.page_url for m in result.publish_mismatches]
        assert "syllabus" in slugs

    def test_no_mismatch_for_same_state(self):
        result = _run_audit()
        slugs = [m.page_url for m in result.publish_mismatches]
        assert "module-1-intro" not in slugs

    def test_mismatch_direction_bp_published_child_not(self):
        result = _run_audit()
        mismatch = next(m for m in result.publish_mismatches if m.page_url == "syllabus")
        assert mismatch.blueprint_published is True
        assert mismatch.child_published is False
        assert "auto-publish" in mismatch.risk_description.lower()

    def test_mismatch_direction_bp_unpublished_child_published(self):
        bp = [{"url": "hidden-page", "title": "Hidden", "published": False}]
        child = [{"url": "hidden-page", "title": "Hidden", "published": True}]
        result = _run_audit(bp_pages=bp, child_pages=child)
        assert len(result.publish_mismatches) == 1
        m = result.publish_mismatches[0]
        assert m.blueprint_published is False
        assert m.child_published is True
        assert "unpublish" in m.risk_description.lower()

    def test_no_mismatches_when_states_match(self):
        pages = [{"url": "x", "title": "X", "published": True}]
        result = _run_audit(bp_pages=pages, child_pages=pages)
        assert result.publish_mismatches == []


# ─── Discussion reply tests ───────────────────────────────────────────────────


class TestDiscussionReplies:
    def test_discussion_with_replies_flagged(self):
        result = _run_audit()
        titles = [d.title for d in result.discussions_with_replies]
        assert "Week 1 Discussion" in titles

    def test_discussion_without_replies_not_flagged(self):
        result = _run_audit()
        titles = [d.title for d in result.discussions_with_replies]
        assert "Week 2 Discussion" not in titles

    def test_reply_count_recorded(self):
        result = _run_audit()
        d = next(x for x in result.discussions_with_replies if x.title == "Week 1 Discussion")
        assert d.reply_count == 5

    def test_blueprint_has_topic_flag_true_when_present(self):
        result = _run_audit()
        d = next(x for x in result.discussions_with_replies if x.title == "Week 1 Discussion")
        assert d.blueprint_has_topic is True

    def test_blueprint_has_topic_flag_false_when_absent(self):
        child_disc = [
            {
                "id": 999,
                "title": "Child-Only Discussion",
                "published": True,
                "discussion_subentry_count": 3,
            }
        ]
        result = _run_audit(child_disc=child_disc)
        d = next(x for x in result.discussions_with_replies if x.title == "Child-Only Discussion")
        assert d.blueprint_has_topic is False

    def test_zero_subentry_count_not_flagged(self):
        child_disc = [
            {
                "id": 1,
                "title": "No Replies",
                "published": True,
                "discussion_subentry_count": 0,
            }
        ]
        result = _run_audit(child_disc=child_disc)
        assert result.discussions_with_replies == []

    def test_missing_subentry_count_field_treated_as_zero(self):
        child_disc = [{"id": 1, "title": "No Count Field", "published": True}]
        result = _run_audit(child_disc=child_disc)
        assert result.discussions_with_replies == []


# ─── Assignment comparison tests ─────────────────────────────────────────────


class TestAssignmentComparison:
    def test_child_only_assignment_detected(self):
        result = _run_audit()
        assert "Extra Credit Quiz" in result.assignments_only_in_child

    def test_shared_assignment_not_child_only(self):
        result = _run_audit()
        assert "Homework 1" not in result.assignments_only_in_child

    def test_blueprint_only_assignment_detected(self):
        # "Midterm Exam" is in blueprint but not in child
        result = _run_audit()
        assert "Midterm Exam" in result.assignments_only_in_blueprint

    def test_empty_assignments_produce_empty_lists(self):
        result = _run_audit(bp_asgn=[], child_asgn=[])
        assert result.assignments_only_in_child == []
        assert result.assignments_only_in_blueprint == []


# ─── total_risks property ─────────────────────────────────────────────────────


class TestTotalRisks:
    def test_total_risks_counts_correctly(self):
        result = _run_audit()
        expected = (
            len(result.publish_mismatches)
            + len(result.discussions_with_replies)
            + len(result.pages_only_in_blueprint)
            + len(result.assignments_only_in_blueprint)
        )
        assert result.total_risks == expected

    def test_total_risks_zero_when_clean(self):
        pages = [{"url": "page", "title": "Page", "published": True}]
        disc = [{"id": 1, "title": "D", "published": True, "discussion_subentry_count": 0}]
        asgn = [{"name": "HW1", "points_possible": 10}]
        result = _run_audit(
            bp_pages=pages,
            child_pages=pages,
            bp_asgn=asgn,
            child_asgn=asgn,
            bp_disc=disc,
            child_disc=disc,
        )
        assert result.total_risks == 0

    def test_total_risks_does_not_count_child_only_pages(self):
        """Child-only pages are informational, not risky."""
        child_extra = CHILD_PAGES + [
            {"url": "extra1", "title": "Extra1", "published": True},
            {"url": "extra2", "title": "Extra2", "published": True},
        ]
        result = _run_audit(child_pages=child_extra)
        # extra child pages should NOT inflate total_risks
        assert result.total_risks == (
            len(result.publish_mismatches)
            + len(result.discussions_with_replies)
            + len(result.pages_only_in_blueprint)
            + len(result.assignments_only_in_blueprint)
        )


# ─── API error handling ───────────────────────────────────────────────────────


class TestAPIErrors:
    def test_canvas_api_error_raises_blueprint_audit_error(self):
        with patch(
            "lms_migration.blueprint_audit.fetch_course_pages",
            side_effect=CanvasAPIError("403 Forbidden"),
        ):
            with pytest.raises(BlueprintAuditError, match="Canvas API error"):
                audit_blueprint_sync(
                    blueprint_course_id="100",
                    child_course_id="200",
                    base_url="https://canvas.example.edu",
                    token="tok",
                )


# ─── Report writing tests ─────────────────────────────────────────────────────


class TestWriteReports:
    def test_write_reports_creates_both_files(self, tmp_path):
        result = _run_audit()
        json_p, md_p = write_blueprint_reports(result, tmp_path, "d2l-export")
        assert json_p.exists()
        assert md_p.exists()

    def test_json_structure(self, tmp_path):
        result = _run_audit()
        json_p, _ = write_blueprint_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        assert "blueprint_course_id" in data
        assert "child_course_id" in data
        assert "total_risks" in data
        assert "publish_mismatches" in data
        assert "discussions_with_replies" in data
        assert "pages_only_in_child" in data
        assert "pages_only_in_blueprint" in data
        assert "assignments_only_in_child" in data
        assert "assignments_only_in_blueprint" in data

    def test_json_total_risks_matches_result(self, tmp_path):
        result = _run_audit()
        json_p, _ = write_blueprint_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        assert data["total_risks"] == result.total_risks

    def test_markdown_contains_discussions_section(self, tmp_path):
        result = _run_audit()
        _, md_p = write_blueprint_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "Discussions With Student Replies" in md

    def test_markdown_contains_publish_mismatch_section(self, tmp_path):
        result = _run_audit()
        _, md_p = write_blueprint_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "Publish-State Mismatches" in md

    def test_clean_result_shows_no_risks_message(self, tmp_path):
        pages = [{"url": "page", "title": "Page", "published": True}]
        disc = [{"id": 1, "title": "D", "published": True, "discussion_subentry_count": 0}]
        asgn = [{"name": "HW1", "points_possible": 10}]
        result = _run_audit(
            bp_pages=pages,
            child_pages=pages,
            bp_asgn=asgn,
            child_asgn=asgn,
            bp_disc=disc,
            child_disc=disc,
        )
        _, md_p = write_blueprint_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "No sync risks detected" in md

    def test_output_dir_created_if_absent(self, tmp_path):
        nested = tmp_path / "subdir" / "nested"
        result = _run_audit()
        write_blueprint_reports(result, nested, "stem")
        assert nested.exists()

    def test_stem_controls_filename(self, tmp_path):
        result = _run_audit()
        json_p, md_p = write_blueprint_reports(result, tmp_path, "my-course")
        assert json_p.name == "my-course.blueprint-presync.json"
        assert md_p.name == "my-course.blueprint-presync.md"

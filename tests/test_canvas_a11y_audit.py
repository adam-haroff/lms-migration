"""Tests for canvas_a11y_audit.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lms_migration.canvas_a11y_audit import (
    A11yAuditError,
    A11yAuditResult,
    PageA11yResult,
    audit_course_pages,
    compute_regressions,
    write_a11y_reports,
)
from lms_migration.canvas_api import CanvasAPIError


# ─── Fixtures ─────────────────────────────────────────────────────────────────


BASE_URL = "https://canvas.example.edu"
COURSE_ID = "999"
TOKEN = "test-token"


def _make_page_meta(url: str, title: str) -> dict:
    return {
        "url": url,
        "title": title,
        "html_url": f"{BASE_URL}/courses/{COURSE_ID}/pages/{url}",
    }


def _make_page_data(title: str, body: str) -> dict:
    return {"title": title, "body": body}


# ─── audit_course_pages ───────────────────────────────────────────────────────


class TestAuditCoursePages:
    def test_no_issues_on_clean_page(self):
        pages = [_make_page_meta("intro", "Introduction")]
        page_data = _make_page_data("Introduction", "<h1>Introduction</h1><p>Hello.</p>")

        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch("lms_migration.canvas_a11y_audit.fetch_course_page", return_value=page_data),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.pages_audited == 1
        assert result.pages_with_issues == 0
        assert result.total_issues == 0

    def test_detects_missing_alt_text(self):
        pages = [_make_page_meta("page1", "Images")]
        page_data = _make_page_data(
            "Images", '<p><img src="photo.jpg" /></p>'  # no alt
        )

        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch("lms_migration.canvas_a11y_audit.fetch_course_page", return_value=page_data),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.pages_with_issues == 1
        assert result.total_issues >= 1
        reasons = [i["reason"] for r in result.results for i in r.issues]
        assert any("alt" in r.lower() for r in reasons)

    def test_detects_heading_jump(self):
        pages = [_make_page_meta("page1", "Headers")]
        page_data = _make_page_data("Headers", "<h1>Title</h1><h3>Skip</h3>")

        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch("lms_migration.canvas_a11y_audit.fetch_course_page", return_value=page_data),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        reasons = [i["reason"] for r in result.results for i in r.issues]
        assert any("heading" in r.lower() for r in reasons)

    def test_multiple_pages(self):
        pages = [
            _make_page_meta("p1", "Clean"),
            _make_page_meta("p2", "With Issues"),
        ]
        p1_data = _make_page_data("Clean", "<p>Fine.</p>")
        p2_data = _make_page_data("With Issues", '<img src="x.jpg" />')

        def _fetch_page(*, base_url, course_id, page_url, token):
            return p1_data if page_url == "p1" else p2_data

        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch("lms_migration.canvas_a11y_audit.fetch_course_page", side_effect=_fetch_page),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.pages_audited == 2
        assert result.pages_with_issues == 1

    def test_api_fetch_pages_error_raises(self):
        with patch(
            "lms_migration.canvas_a11y_audit.fetch_course_pages",
            side_effect=CanvasAPIError("network error"),
        ):
            with pytest.raises(A11yAuditError, match="network error"):
                audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

    def test_page_fetch_failure_skips_body(self):
        pages = [_make_page_meta("bad", "Bad Page")]
        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch(
                "lms_migration.canvas_a11y_audit.fetch_course_page",
                side_effect=CanvasAPIError("forbidden"),
            ),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.pages_audited == 1
        assert result.results[0].issue_count == 0  # skipped, not crashed

    def test_empty_course(self):
        with patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=[]):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.pages_audited == 0
        assert result.total_issues == 0

    def test_page_without_url_skipped(self):
        pages = [{"title": "Orphan", "html_url": ""}]  # no "url" key
        with patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)
        assert result.pages_audited == 0

    def test_result_fields_populated(self):
        pages = [_make_page_meta("intro", "Intro")]
        data = _make_page_data("Intro", "<p>ok</p>")
        with (
            patch("lms_migration.canvas_a11y_audit.fetch_course_pages", return_value=pages),
            patch("lms_migration.canvas_a11y_audit.fetch_course_page", return_value=data),
        ):
            result = audit_course_pages(base_url=BASE_URL, course_id=COURSE_ID, token=TOKEN)

        assert result.course_id == COURSE_ID
        assert result.base_url == BASE_URL
        r0 = result.results[0]
        assert r0.page_url == "intro"
        assert r0.page_title == "Intro"


# ─── compute_regressions ─────────────────────────────────────────────────────


class TestComputeRegressions:
    def _make_result(self, issues_by_page: dict[str, list[str]]) -> A11yAuditResult:
        results = []
        for slug, reasons in issues_by_page.items():
            results.append(
                PageA11yResult(
                    page_url=slug,
                    page_title=slug.replace("-", " ").title(),
                    canvas_url=f"{BASE_URL}/pages/{slug}",
                    issues=[{"reason": r, "evidence": "..."} for r in reasons],
                )
            )
        total = sum(len(v) for v in issues_by_page.values())
        return A11yAuditResult(
            course_id=COURSE_ID,
            base_url=BASE_URL,
            pages_audited=len(results),
            pages_with_issues=sum(1 for v in issues_by_page.values() if v),
            total_issues=total,
            results=results,
        )

    def _make_pre_import_json(self, tmp_path: Path, files: dict[str, list[str]]) -> Path:
        data = {
            "files": [
                {
                    "file": f"{stem}.html",
                    "accessibility_issues": [{"reason": r, "evidence": "..."} for r in reasons],
                }
                for stem, reasons in files.items()
            ]
        }
        p = tmp_path / "migration-report.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_no_regressions_when_same_issues(self, tmp_path):
        result = self._make_result({"intro": ["Image missing alt attribute"]})
        pre = self._make_pre_import_json(tmp_path, {"intro": ["Image missing alt attribute"]})
        regressions = compute_regressions(result, pre)
        assert regressions == []

    def test_new_issue_is_regression(self, tmp_path):
        result = self._make_result({"intro": ["Image missing alt attribute", "Heading level jump detected"]})
        pre = self._make_pre_import_json(tmp_path, {"intro": ["Image missing alt attribute"]})
        regressions = compute_regressions(result, pre)
        reasons = [r["reason"] for r in regressions]
        assert "Heading level jump detected" in reasons

    def test_empty_pre_import_all_are_regressions(self, tmp_path):
        result = self._make_result({"intro": ["Image missing alt attribute"]})
        pre = self._make_pre_import_json(tmp_path, {})
        regressions = compute_regressions(result, pre)
        assert len(regressions) == 1

    def test_missing_pre_import_file_returns_empty(self, tmp_path):
        result = self._make_result({"intro": ["Image missing alt attribute"]})
        regressions = compute_regressions(result, tmp_path / "nonexistent.json")
        assert regressions == []

    def test_invalid_json_returns_empty(self, tmp_path):
        pre = tmp_path / "bad.json"
        pre.write_text("not json", encoding="utf-8")
        result = self._make_result({"intro": ["Image missing alt attribute"]})
        regressions = compute_regressions(result, pre)
        assert regressions == []

    def test_regressions_stored_on_result(self, tmp_path):
        result = self._make_result({"intro": ["New Issue"]})
        pre = self._make_pre_import_json(tmp_path, {})
        compute_regressions(result, pre)
        assert len(result.regressions) == 1


# ─── write_a11y_reports ───────────────────────────────────────────────────────


class TestWriteA11yReports:
    def _simple_result(self) -> A11yAuditResult:
        return A11yAuditResult(
            course_id="999",
            base_url=BASE_URL,
            pages_audited=2,
            pages_with_issues=1,
            total_issues=2,
            results=[
                PageA11yResult(
                    page_url="intro",
                    page_title="Introduction",
                    canvas_url=f"{BASE_URL}/pages/intro",
                    issues=[
                        {"reason": "Image missing alt attribute", "evidence": "<img src=x>"},
                        {"reason": "Table missing caption", "evidence": "<table>"},
                    ],
                ),
                PageA11yResult(
                    page_url="clean",
                    page_title="Clean Page",
                    canvas_url=f"{BASE_URL}/pages/clean",
                    issues=[],
                ),
            ],
            regressions=[
                {
                    "page_url": "intro",
                    "page_title": "Introduction",
                    "reason": "Table missing caption",
                    "evidence": "<table>",
                }
            ],
        )

    def test_creates_both_files(self, tmp_path):
        result = self._simple_result()
        json_p, md_p = write_a11y_reports(result, tmp_path, "d2l-export")
        assert json_p.exists()
        assert md_p.exists()

    def test_json_structure(self, tmp_path):
        result = self._simple_result()
        json_p, _ = write_a11y_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        for key in ("course_id", "base_url", "pages_audited", "pages_with_issues",
                    "total_issues", "regressions_count", "regressions", "results"):
            assert key in data

    def test_json_contains_issues(self, tmp_path):
        result = self._simple_result()
        json_p, _ = write_a11y_reports(result, tmp_path, "d2l-export")
        data = json.loads(json_p.read_text())
        assert data["total_issues"] == 2
        assert data["regressions_count"] == 1

    def test_markdown_contains_regressions(self, tmp_path):
        result = self._simple_result()
        _, md_p = write_a11y_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "Regressions" in md
        assert "Table missing caption" in md

    def test_markdown_contains_course_id(self, tmp_path):
        result = self._simple_result()
        _, md_p = write_a11y_reports(result, tmp_path, "d2l-export")
        md = md_p.read_text()
        assert "999" in md

    def test_clean_result_shows_no_issues_message(self, tmp_path):
        result = A11yAuditResult(
            course_id="1",
            base_url=BASE_URL,
            pages_audited=3,
            pages_with_issues=0,
            total_issues=0,
            results=[],
        )
        _, md_p = write_a11y_reports(result, tmp_path, "stem")
        md = md_p.read_text()
        assert "No accessibility issues" in md

    def test_creates_output_dir(self, tmp_path):
        result = self._simple_result()
        new_dir = tmp_path / "sub" / "dir"
        write_a11y_reports(result, new_dir, "stem")
        assert new_dir.is_dir()

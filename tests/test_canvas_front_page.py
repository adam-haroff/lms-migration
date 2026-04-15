from __future__ import annotations

import json
from pathlib import Path

from lms_migration.canvas_front_page import auto_set_course_front_page


def test_auto_set_course_front_page_chooses_division_variant(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.fetch_course_pages",
        lambda **_: [
            {"url": "home-page", "title": "Home Page", "published": True},
            {"url": "home-page-lcs", "title": "Home Page - LCS", "published": True},
            {"url": "home-page-stem", "title": "Home Page - STEM", "published": True},
        ],
    )
    applied: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.set_course_front_page",
        lambda **kwargs: applied.append(("front", kwargs["page_url"]))
        or {"url": kwargs["page_url"], "front_page": True},
    )
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.update_course_default_view",
        lambda **kwargs: applied.append(("view", kwargs["default_view"]))
        or {"default_view": kwargs["default_view"]},
    )

    output_path = tmp_path / "front-page-report.json"
    auto_set_course_front_page(
        base_url="https://canvas.example.com",
        course_id="17382",
        token="tok",
        course_code="COM 2220",
        output_json_path=output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selection"]["selected_page_url"] == "home-page-lcs"
    assert payload["selection"]["selected_page_title"] == "Home Page - LCS"
    assert payload["summary"]["front_page_set"] is True
    assert ("front", "home-page-lcs") in applied
    assert ("view", "wiki") in applied


def test_auto_set_course_front_page_tolerates_double_space_title_and_fallbacks(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.fetch_course_pages",
        lambda **_: [
            {"url": "home-page", "title": "Home Page", "published": True},
            {"url": "home-page-lcs-2", "title": "Home Page  - LCS", "published": True},
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.set_course_front_page",
        lambda **kwargs: {"url": kwargs["page_url"], "front_page": True},
    )
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.update_course_default_view",
        lambda **kwargs: {"default_view": kwargs["default_view"]},
    )

    output_path = tmp_path / "front-page-report.json"
    auto_set_course_front_page(
        base_url="https://canvas.example.com",
        course_id="17382",
        token="tok",
        course_code="COM 2220",
        output_json_path=output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selection"]["selected_page_url"] == "home-page-lcs-2"
    assert payload["summary"]["used_fallback"] is False


def test_auto_set_course_front_page_uses_default_home_page_fallback(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.fetch_course_pages",
        lambda **_: [
            {"url": "home-page", "title": "Home Page", "published": True},
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.set_course_front_page",
        lambda **kwargs: {"url": kwargs["page_url"], "front_page": True},
    )
    monkeypatch.setattr(
        "lms_migration.canvas_front_page.update_course_default_view",
        lambda **kwargs: {"default_view": kwargs["default_view"]},
    )

    output_path = tmp_path / "front-page-report.json"
    auto_set_course_front_page(
        base_url="https://canvas.example.com",
        course_id="17382",
        token="tok",
        course_code="COM 2220",
        output_json_path=output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selection"]["selected_page_url"] == "home-page"
    assert payload["summary"]["used_fallback"] is True

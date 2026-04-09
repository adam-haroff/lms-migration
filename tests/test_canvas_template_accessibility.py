from __future__ import annotations

import json

from lms_migration.canvas_template_accessibility import auto_fix_template_accessibility


def test_auto_fix_template_accessibility_updates_pages_assignments_and_discussions(
    monkeypatch, tmp_path
) -> None:
    page_updates: list[tuple[str, str]] = []
    assignment_updates: list[tuple[str, str]] = []
    discussion_updates: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.fetch_course_pages",
        lambda **_: [
            {"url": "home-page", "title": "Home Page"},
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.fetch_course_page",
        lambda **_: {
            "url": "home-page",
            "title": "Home Page",
            "body": (
                '<h2><img src="/courses/1/files/22/preview" alt="Overview icon" '
                'width="45" height="45"><strong>Overview</strong></h2>'
            ),
        },
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.fetch_course_assignments",
        lambda **_: [
            {"id": 10, "name": "Module 1 Assignment", "description": "<h2>Overview</h2><h4>Instructions</h4>"},
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.fetch_course_discussion_topics",
        lambda **_: [
            {
                "id": 77,
                "title": "Week 1 Discussion",
                "message": '<p><img alt="" data-decorative="true"></p>',
            }
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.update_course_page_body",
        lambda page_url, body_html, **_: page_updates.append((page_url, body_html)),
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.update_course_assignment_description",
        lambda assignment_id, description_html, **_: assignment_updates.append(
            (str(assignment_id), description_html)
        ),
    )
    monkeypatch.setattr(
        "lms_migration.canvas_template_accessibility.update_discussion_topic_message",
        lambda topic_id, message_html, **_: discussion_updates.append(
            (str(topic_id), message_html)
        ),
    )

    report_path = auto_fix_template_accessibility(
        base_url="https://example.instructure.com",
        course_id="17038",
        token="token",
        output_json_path=tmp_path / "a11y-report.json",
        dry_run=False,
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["pages_updated"] == 1
    assert payload["summary"]["assignments_updated"] == 1
    assert payload["summary"]["discussions_updated"] == 1
    assert page_updates and 'role="presentation"' in page_updates[0][1]
    assert assignment_updates and "<h3>Instructions</h3>" in assignment_updates[0][1]
    assert discussion_updates and "<img" not in discussion_updates[0][1]

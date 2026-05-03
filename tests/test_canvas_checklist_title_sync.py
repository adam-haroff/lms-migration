from __future__ import annotations

import json

from lms_migration.canvas_checklist_title_sync import sync_intro_checklist_titles


def test_sync_intro_checklist_titles_updates_only_intro_pages(
    monkeypatch, tmp_path
) -> None:
    updates: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "lms_migration.canvas_checklist_title_sync.fetch_course_modules",
        lambda **_: [
            {
                "name": "Module 3: Sample Module",
                "items": [
                    {
                        "type": "Page",
                        "title": "Module 3: Introduction and Checklist",
                        "page_url": "module-3-introduction-and-checklist",
                    },
                    {
                        "type": "Assignment",
                        "title": "Module 3: Assignment: Final Essay",
                        "content_id": 55,
                    },
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_checklist_title_sync.fetch_course_pages",
        lambda **_: [
            {
                "url": "module-3-introduction-and-checklist",
                "title": "Module 3: Introduction and Checklist",
                "page_id": 10,
            },
            {
                "url": "ordinary-page",
                "title": "Ordinary Page",
                "page_id": 11,
            },
        ],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_checklist_title_sync.fetch_course_page",
        lambda page_url, **_: {
            "page_id": 10 if page_url == "module-3-introduction-and-checklist" else 11,
            "body": (
                "<ol><li>Submit to the Dropbox | Final Essay.</li></ol>"
                if page_url == "module-3-introduction-and-checklist"
                else "<p>No changes</p>"
            ),
        },
    )
    monkeypatch.setattr(
        "lms_migration.canvas_checklist_title_sync.update_course_page_body",
        lambda page_url, body_html, **_: updates.append((page_url, body_html)),
    )

    report = sync_intro_checklist_titles(
        base_url="https://example.instructure.com",
        course_id="123",
        token="token",
        output_json_path=tmp_path / "checklist-sync.json",
        dry_run=False,
    )

    assert report["summary"]["intro_checklist_pages_scanned"] == 1
    assert report["summary"]["pages_updated"] == 1
    assert report["summary"]["text_reference_rewrites"] == 1
    assert updates == [
        (
            "module-3-introduction-and-checklist",
            "<ol><li>Submit to the Assignment: Final Essay.</li></ol>",
        )
    ]
    payload = json.loads((tmp_path / "checklist-sync.json").read_text(encoding="utf-8"))
    assert payload["summary"]["pages_updated"] == 1

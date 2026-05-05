from __future__ import annotations

import json

from lms_migration import canvas_operations


def test_bulk_replace_page_text_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_pages",
        lambda **_: [
            {"title": "Module 1: Introduction and Checklist", "url": "module-1"},
            {"title": "Module 2: Learning Activities", "url": "module-2"},
        ],
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_page",
        lambda **kwargs: {
            "body": "<p>Submit to the Dropbox | Reflection.</p>"
            if kwargs["page_url"] == "module-1"
            else "<p>No change needed.</p>"
        },
    )

    updated_pages: list[str] = []

    def _update_page(**kwargs):
        updated_pages.append(kwargs["page_url"])
        return {"url": kwargs["page_url"]}

    monkeypatch.setattr(canvas_operations, "update_course_page", _update_page)

    output_json = tmp_path / "page-replace.json"
    output_md = tmp_path / "page-replace.md"
    report = canvas_operations.bulk_replace_page_text(
        base_url="https://canvas.example.com",
        course_id="42",
        token="tok",
        title_pattern="Introduction and Checklist",
        match_mode="contains",
        case_sensitive=False,
        find_text="Dropbox",
        replace_text="Assignment",
        regex=False,
        dry_run=True,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )

    assert report["summary"]["pages_scanned"] == 2
    assert report["summary"]["pages_matched"] == 1
    assert report["summary"]["pages_with_replacements"] == 1
    assert report["summary"]["pages_updated"] == 0
    assert updated_pages == []
    assert json.loads(output_json.read_text(encoding="utf-8"))["operation"] == (
        "page_text_replace"
    )


def test_bulk_update_assignment_settings_apply(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_assignments",
        lambda **_: [
            {
                "id": 10,
                "name": "Module 3: Assignment: Essay",
                "points_possible": 10,
                "submission_types": ["online_text_entry"],
            }
        ],
    )

    calls: list[dict] = []

    def _update_assignment(**kwargs):
        calls.append(kwargs)
        return {"id": kwargs["assignment_id"]}

    monkeypatch.setattr(canvas_operations, "update_course_assignment", _update_assignment)

    output_json = tmp_path / "assignment-settings.json"
    output_md = tmp_path / "assignment-settings.md"
    report = canvas_operations.bulk_update_assignment_settings(
        base_url="https://canvas.example.com",
        course_id="42",
        token="tok",
        title_pattern="Essay",
        match_mode="contains",
        case_sensitive=False,
        points_possible=15,
        submission_preset="file-upload-only",
        dry_run=False,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )

    assert report["summary"]["assignments_matched"] == 1
    assert report["summary"]["assignments_needing_changes"] == 1
    assert report["summary"]["assignments_updated"] == 1
    assert len(calls) == 1
    assert calls[0]["points_possible"] == 15
    assert calls[0]["submission_types"] == ["online_upload"]


def test_bulk_replace_description_text_updates_assignment_and_discussion(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_assignments",
        lambda **_: [{"id": 10, "name": "Module 3: Quiz: Gender Review"}],
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_assignment",
        lambda **_: {"description": "<p>Submit to the Dropbox.</p>"},
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_discussion_topics",
        lambda **_: [{"id": 20, "title": "Discussion: Reflection"}],
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_discussion_topic",
        lambda **_: {"message": "<p>Post to the Dropbox.</p>"},
    )

    assignment_calls: list[dict] = []
    discussion_calls: list[dict] = []

    monkeypatch.setattr(
        canvas_operations,
        "update_course_assignment",
        lambda **kwargs: assignment_calls.append(kwargs) or {"id": kwargs["assignment_id"]},
    )
    monkeypatch.setattr(
        canvas_operations,
        "update_discussion_topic",
        lambda **kwargs: discussion_calls.append(kwargs) or {"id": kwargs["topic_id"]},
    )

    output_json = tmp_path / "description-replace.json"
    output_md = tmp_path / "description-replace.md"
    report = canvas_operations.bulk_replace_description_text(
        base_url="https://canvas.example.com",
        course_id="42",
        token="tok",
        title_pattern="Module 3|Reflection",
        match_mode="regex",
        case_sensitive=False,
        find_text="Dropbox",
        replace_text="assignment submission area",
        regex=False,
        include_assignments=True,
        include_discussions=True,
        dry_run=False,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )

    assert report["summary"]["items_matched"] == 2
    assert report["summary"]["items_with_replacements"] == 2
    assert report["summary"]["items_updated"] == 2
    assert report["summary"]["total_replacements"] == 2
    assert len(assignment_calls) == 1
    assert len(discussion_calls) == 1
    assert "assignment submission area" in assignment_calls[0]["description_html"]
    assert "assignment submission area" in discussion_calls[0]["message_html"]


def test_bulk_set_publish_state_pages_and_discussions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_pages",
        lambda **_: [{"title": "Course Summary", "url": "course-summary", "published": True}],
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_assignments",
        lambda **_: [],
    )
    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_discussion_topics",
        lambda **_: [{"id": 20, "title": "Course Q&A", "published": True}],
    )

    page_calls: list[dict] = []
    discussion_calls: list[dict] = []

    monkeypatch.setattr(
        canvas_operations,
        "update_course_page",
        lambda **kwargs: page_calls.append(kwargs) or {"url": kwargs["page_url"]},
    )
    monkeypatch.setattr(
        canvas_operations,
        "update_discussion_topic",
        lambda **kwargs: discussion_calls.append(kwargs) or {"id": kwargs["topic_id"]},
    )

    output_json = tmp_path / "publish-state.json"
    output_md = tmp_path / "publish-state.md"
    report = canvas_operations.bulk_set_publish_state(
        base_url="https://canvas.example.com",
        course_id="42",
        token="tok",
        title_pattern="Course",
        match_mode="contains",
        case_sensitive=False,
        include_pages=True,
        include_assignments=False,
        include_discussions=True,
        publish=False,
        dry_run=False,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )

    assert report["summary"]["items_scanned"] == 2
    assert report["summary"]["items_matched"] == 2
    assert report["summary"]["items_needing_changes"] == 2
    assert report["summary"]["items_updated"] == 2
    assert len(page_calls) == 1
    assert len(discussion_calls) == 1


def test_scaffold_modules_from_csv_apply(tmp_path, monkeypatch) -> None:
    csv_path = tmp_path / "scaffold.csv"
    csv_path.write_text(
        "module_name,module_position,module_published,page_title,page_kind,introduction_html,checklist_items,item_indent\n"
        "Module 1: Start Here,1,false,Module 1: Introduction and Checklist,intro_checklist,<p>Intro</p>,Review syllabus||Complete quiz,1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        canvas_operations,
        "fetch_course_modules",
        lambda **_: [],
    )

    module_calls: list[dict] = []
    page_calls: list[dict] = []
    module_item_calls: list[dict] = []

    monkeypatch.setattr(
        canvas_operations,
        "create_course_module",
        lambda **kwargs: module_calls.append(kwargs) or {"id": 100, "name": kwargs["name"]},
    )
    monkeypatch.setattr(
        canvas_operations,
        "create_or_update_course_page",
        lambda **kwargs: page_calls.append(kwargs) or {"url": "module-1-introduction-and-checklist"},
    )
    monkeypatch.setattr(
        canvas_operations,
        "create_course_module_item",
        lambda **kwargs: module_item_calls.append(kwargs) or {"id": 101},
    )

    output_json = tmp_path / "scaffold.json"
    output_md = tmp_path / "scaffold.md"
    report = canvas_operations.scaffold_modules_from_csv(
        base_url="https://canvas.example.com",
        course_id="42",
        token="tok",
        csv_path=csv_path,
        dry_run=False,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )

    assert report["summary"]["rows_processed"] == 1
    assert report["summary"]["modules_created"] == 1
    assert report["summary"]["pages_written"] == 1
    assert report["summary"]["module_items_created"] == 1
    assert len(module_calls) == 1
    assert len(page_calls) == 1
    assert len(module_item_calls) == 1
    assert "<h2>Module Checklist</h2>" in page_calls[0]["body_html"]
    assert "Review syllabus" in page_calls[0]["body_html"]


def test_parse_points_possible_blank_returns_none() -> None:
    assert canvas_operations._parse_points_possible("") is None


def test_resolve_submission_preset_keep_current() -> None:
    assert canvas_operations._resolve_submission_preset("keep-current") is None

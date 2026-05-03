from __future__ import annotations

from lms_migration.canvas_import_artifact_cleanup import (
    _classify_unused_file,
    cleanup_import_artifacts,
)


def test_classify_unused_file_identifies_html_and_xml_artifacts() -> None:
    html_candidate = _classify_unused_file(
        {"id": "1", "name": "Introduction and Objectives.html", "folder_path": "course files"}
    )
    xml_candidate = _classify_unused_file(
        {"id": "2", "name": "imsmanifest.xml", "folder_path": "course files"}
    )
    protected = _classify_unused_file(
        {"id": "3", "name": "worksheet.docx", "folder_path": "course files"}
    )

    assert html_candidate is not None
    assert html_candidate["reason"] == "unused_source_html_import_artifact"
    assert xml_candidate is not None
    assert xml_candidate["reason"] == "known_canvas_import_artifact"
    assert protected is None


def test_cleanup_import_artifacts_reports_candidates_without_deleting(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course",
        lambda **_: {"id": 1, "syllabus_body": ""},
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_pages",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_modules",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_assignments",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_discussion_topics",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_announcements",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_files",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.fetch_course_folders",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "lms_migration.canvas_import_artifact_cleanup.audit_course_cleanup_data",
        lambda **_: {
            "unused_files": [
                {
                    "id": "10",
                    "name": "imsmanifest.xml",
                    "folder_path": "course files",
                },
                {
                    "id": "11",
                    "name": "Module 1.html",
                    "folder_path": "course files",
                },
                {
                    "id": "12",
                    "name": "worksheet.docx",
                    "folder_path": "course files",
                },
            ],
            "empty_folders": [{"id": "99", "full_name": "course files/Uploaded Media"}],
        },
    )

    report = cleanup_import_artifacts(
        base_url="https://example.instructure.com",
        course_id="123",
        token="token",
        output_json_path=tmp_path / "artifact-cleanup.json",
        apply_deletes=False,
    )

    assert report["summary"]["artifact_file_candidates"] == 2
    assert report["summary"]["empty_folder_candidates"] == 1
    assert report["summary"]["deleted_files"] == 0
    assert {item["name"] for item in report["artifact_file_candidates"]} == {
        "imsmanifest.xml",
        "Module 1.html",
    }

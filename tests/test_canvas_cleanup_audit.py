from __future__ import annotations

from lms_migration.canvas_cleanup_audit import audit_course_cleanup_data


def test_cleanup_audit_detects_duplicate_modules_and_pages() -> None:
    report = audit_course_cleanup_data(
        course={},
        pages=[
            {
                "page_id": 1,
                "title": "Policies and Support",
                "url": "policies-and-support",
                "published": True,
                "front_page": False,
                "body": "",
            },
            {
                "page_id": 2,
                "title": "Policies and Support",
                "url": "policies-and-support-2",
                "published": False,
                "front_page": False,
                "body": "",
            },
            {
                "page_id": 3,
                "title": "Start Here",
                "url": "start-here",
                "published": True,
                "front_page": False,
                "body": '<p><a href="/courses/17038/pages/policies-and-support">Policies</a></p>',
            },
        ],
        modules=[
            {"id": 10, "name": "Module 1", "published": True, "items": []},
            {"id": 11, "name": "Module 1", "published": False, "items": []},
        ],
        assignments=[],
        discussions=[],
        announcements=[],
        files=[],
        folders=[],
    )

    assert report["summary"]["duplicate_modules"] == 1
    assert report["summary"]["duplicate_page_titles"] == 1
    assert report["summary"]["published_unlinked_pages"] == 1
    assert report["published_unlinked_pages"][0]["title"] == "Start Here"


def test_cleanup_audit_treats_module_and_html_refs_as_usage() -> None:
    report = audit_course_cleanup_data(
        course={"syllabus_body": '<a href="/courses/17038/pages/policies-and-support">Policies</a>'},
        pages=[
            {
                "page_id": 1,
                "title": "Policies and Support",
                "url": "policies-and-support",
                "published": True,
                "front_page": False,
                "body": "",
            }
        ],
        modules=[
            {
                "id": 10,
                "name": "Start Here",
                "published": True,
                "items": [{"type": "Page", "page_url": "policies-and-support"}],
            }
        ],
        assignments=[],
        discussions=[],
        announcements=[],
        files=[],
        folders=[],
    )

    assert report["summary"]["published_unlinked_pages"] == 0


def test_cleanup_audit_detects_duplicate_files_empty_folders_and_unused_files() -> None:
    report = audit_course_cleanup_data(
        course={},
        pages=[
            {
                "page_id": 1,
                "title": "Intro",
                "url": "intro",
                "published": True,
                "front_page": False,
                "body": '<p><img src="/courses/17038/files/100/preview"></p>',
            }
        ],
        modules=[
            {
                "id": 10,
                "name": "Module 1",
                "published": True,
                "items": [{"type": "File", "content_id": 102}],
            }
        ],
        assignments=[],
        discussions=[],
        announcements=[],
        files=[
            {"id": 100, "display_name": "book.png", "folder_id": 1},
            {"id": 101, "display_name": "book.png", "folder_id": 2},
            {"id": 102, "display_name": "slides.pptx", "folder_id": 3},
            {"id": 103, "display_name": "unused.pdf", "folder_id": 3},
        ],
        folders=[
            {"id": 1, "name": "icons", "full_name": "course files/template-images/icons"},
            {"id": 2, "name": "icons", "full_name": "course files/web_resources/template-images/icons"},
            {"id": 3, "name": "PowerPoints", "full_name": "course files/PowerPoints"},
            {"id": 4, "name": "empty", "full_name": "course files/Uploaded Media"},
        ],
    )

    assert report["summary"]["duplicate_file_basenames"] == 1
    assert report["duplicate_file_basenames"][0]["preferred_file_id"] == "100"
    assert report["summary"]["empty_folders"] == 1
    assert report["empty_folders"][0]["full_name"] == "course files/Uploaded Media"
    assert report["summary"]["unused_files"] == 2
    assert {row["id"] for row in report["unused_files"]} == {"101", "103"}

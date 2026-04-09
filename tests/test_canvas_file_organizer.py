from __future__ import annotations

from lms_migration.canvas_file_organizer import (
    plan_canvas_file_organization,
    plan_empty_folder_deletions,
)


def test_plan_canvas_file_organization_routes_course_card_and_netiquette() -> None:
    folders = [
        {"id": 1, "full_name": "course files"},
        {"id": 2, "full_name": "course files/course_image", "parent_folder_id": 1},
        {"id": 3, "full_name": "course files/course documents", "parent_folder_id": 1},
    ]
    files = [
        {"id": 10, "display_name": "course-card.png", "folder_id": 2},
        {"id": 11, "display_name": "Netiquette.pdf", "folder_id": 3},
        {"id": 12, "display_name": "other.pdf", "folder_id": 3},
    ]

    plan = plan_canvas_file_organization(files=files, folders=folders)

    assert plan["moves"] == [
        {
            "file_id": "10",
            "name": "course-card.png",
            "current_folder": "course files/course_image",
            "target_folder": "course files/course-content/course-images",
        },
        {
            "file_id": "11",
            "name": "Netiquette.pdf",
            "current_folder": "course files/course documents",
            "target_folder": "course files/course-content",
        },
    ]
    assert plan["skipped"] == []


def test_plan_canvas_file_organization_skips_when_target_has_same_name() -> None:
    folders = [
        {"id": 1, "full_name": "course files"},
        {"id": 2, "full_name": "course files/course_image", "parent_folder_id": 1},
        {
            "id": 3,
            "full_name": "course files/course-content/course-images",
            "parent_folder_id": 1,
        },
    ]
    files = [
        {"id": 10, "display_name": "course-card.png", "folder_id": 2},
        {"id": 11, "display_name": "course-card.png", "folder_id": 3},
    ]

    plan = plan_canvas_file_organization(files=files, folders=folders)

    assert plan["moves"] == []
    assert plan["skipped"] == [
        {
            "file_id": "10",
            "name": "course-card.png",
            "current_folder": "course files/course_image",
            "target_folder": "course files/course-content/course-images",
            "reason": "target-already-contains-same-name",
            "conflicting_file_ids": ["11"],
        }
    ]


def test_plan_empty_folder_deletions_prunes_legacy_empties_but_not_protected() -> None:
    folders = [
        {"id": 1, "name": "course files", "full_name": "course files"},
        {"id": 2, "name": "course_image", "full_name": "course files/course_image", "parent_folder_id": 1},
        {"id": 3, "name": "template-images", "full_name": "course files/template-images", "parent_folder_id": 1},
        {"id": 4, "name": "icons", "full_name": "course files/template-images/icons", "parent_folder_id": 3},
        {"id": 5, "name": "Uploaded Media", "full_name": "course files/Uploaded Media", "parent_folder_id": 1},
        {"id": 6, "name": "course-content", "full_name": "course files/course-content", "parent_folder_id": 1},
        {"id": 7, "name": "legacy", "full_name": "course files/legacy", "parent_folder_id": 1},
        {"id": 8, "name": "child", "full_name": "course files/legacy/child", "parent_folder_id": 7},
    ]
    files = []

    deletions = plan_empty_folder_deletions(files=files, folders=folders)

    assert deletions == [
        {
            "id": "8",
            "name": "child",
            "full_name": "course files/legacy/child",
        },
        {
            "id": "2",
            "name": "course_image",
            "full_name": "course files/course_image",
        },
        {
            "id": "7",
            "name": "legacy",
            "full_name": "course files/legacy",
        },
    ]

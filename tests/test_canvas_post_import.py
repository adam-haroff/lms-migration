from __future__ import annotations

from lms_migration.canvas_post_import import (
    _build_file_index,
    _build_folder_path_index,
    _rewrite_page_body,
)


def test_build_file_index_prefers_canonical_template_folder() -> None:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/template-images/icons"},
            {"id": 20, "full_name": "course files/web_resources/template-images/icons"},
        ]
    )

    file_index, collisions = _build_file_index(
        [
            {"id": 101, "display_name": "book.png", "folder_id": 20},
            {"id": 202, "display_name": "book.png", "folder_id": 10},
        ],
        folder_paths=folder_paths,
    )

    matches = file_index["book.png"]
    assert len(matches) == 1
    assert matches[0].file_id == "202"
    assert matches[0].folder_path == "course files/template-images/icons"
    assert collisions == {"book.png": 2}


def test_build_file_index_prefers_course_image_folder() -> None:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/course_image"},
            {"id": 20, "full_name": "course files/web_resources/course_image"},
        ]
    )

    file_index, collisions = _build_file_index(
        [
            {"id": 101, "display_name": "course-card.png", "folder_id": 20},
            {"id": 202, "display_name": "course-card.png", "folder_id": 10},
        ],
        folder_paths=folder_paths,
    )

    matches = file_index["course-card.png"]
    assert len(matches) == 1
    assert matches[0].file_id == "202"
    assert matches[0].folder_path == "course files/course_image"
    assert collisions == {"course-card.png": 2}


def test_build_file_index_prefers_course_content_folder() -> None:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/course-content/course-images"},
            {"id": 20, "full_name": "course files/Student Files"},
        ]
    )

    file_index, collisions = _build_file_index(
        [
            {"id": 101, "display_name": "banner.png", "folder_id": 20},
            {"id": 202, "display_name": "banner.png", "folder_id": 10},
        ],
        folder_paths=folder_paths,
    )

    matches = file_index["banner.png"]
    assert len(matches) == 1
    assert matches[0].file_id == "202"
    assert matches[0].folder_path == "course files/course-content/course-images"
    assert collisions == {"banner.png": 2}


def test_build_file_index_keeps_ambiguous_duplicates_unresolved() -> None:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/PowerPoints"},
            {"id": 20, "full_name": "course files/Student Files"},
        ]
    )

    file_index, collisions = _build_file_index(
        [
            {"id": 101, "display_name": "shared.pdf", "folder_id": 10},
            {"id": 202, "display_name": "shared.pdf", "folder_id": 20},
        ],
        folder_paths=folder_paths,
    )

    matches = file_index["shared.pdf"]
    assert len(matches) == 2
    assert {match.file_id for match in matches} == {"101", "202"}
    assert collisions == {"shared.pdf": 2}


def test_rewrite_page_body_uses_preferred_duplicate_resolution() -> None:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/template-images/icons"},
            {"id": 20, "full_name": "course files/web_resources/template-images/icons"},
        ]
    )
    file_index, _ = _build_file_index(
        [
            {"id": 101, "display_name": "book.png", "folder_id": 20},
            {"id": 202, "display_name": "book.png", "folder_id": 10},
        ],
        folder_paths=folder_paths,
    )

    updated, rewrites, unresolved, alias_rewrites, alias_keys_used = _rewrite_page_body(
        body_html='<p><img src="../web_resources/template-images/icons/book.png" alt=""></p>',
        file_index=file_index,
        course_id="17038",
        alias_map={},
    )

    assert 'src="/courses/17038/files/202/preview"' in updated
    assert rewrites == 1
    assert unresolved == 0
    assert alias_rewrites == 0
    assert alias_keys_used == set()

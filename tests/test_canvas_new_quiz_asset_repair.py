from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from lms_migration.canvas_new_quiz_asset_repair import (
    _build_source_zip_index,
    _resolve_source_member,
    _rewrite_quiz_item_body,
    extract_quiz_asset_refs,
)
from lms_migration.canvas_post_import import _FileRef


def test_extract_quiz_asset_refs_finds_local_and_file_contents_refs() -> None:
    html = (
        '<p><img src="PastedImage_abc.png"></p>'
        '<p><a href="/courses/21825/file_contents/course%20files/Handout.pdf">Handout</a></p>'
        '<p><img src="/courses/21825/files/999/preview"></p>'
    )

    refs = extract_quiz_asset_refs(html)

    assert [(ref.attr, ref.basename) for ref in refs] == [
        ("src", "pastedimage_abc.png"),
        ("href", "handout.pdf"),
    ]


def test_rewrite_quiz_item_body_uses_preview_for_images_and_download_for_links() -> None:
    html = (
        '<p><img src="PastedImage_abc.png"></p>'
        '<p><a href="Handout.pdf">Open</a></p>'
    )
    file_index = {
        "pastedimage_abc.png": [
            _FileRef(file_id="123", name="PastedImage_abc.png", folder_path="course files/course-content/course-images")
        ],
        "handout.pdf": [
            _FileRef(file_id="456", name="Handout.pdf", folder_path="course files/course-content")
        ],
    }

    updated, rewrites, unresolved, unresolved_basenames = _rewrite_quiz_item_body(
        item_body_html=html,
        file_index=file_index,
        course_id="21825",
    )

    assert '/courses/21825/files/123/preview' in updated
    assert '/courses/21825/files/456/download?wrap=1' in updated
    assert rewrites == 2
    assert unresolved == 0
    assert unresolved_basenames == []


def test_build_source_zip_index_and_resolve_source_member(tmp_path: Path) -> None:
    zip_path = tmp_path / "sample.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("folder/PastedImage_abc.png", b"image-bytes")
        zf.writestr("other/Handout.pdf", b"pdf-bytes")

    index = _build_source_zip_index(zip_path)

    member_name, status = _resolve_source_member(index, "pastedimage_abc.png")
    assert status == "ok"
    assert member_name == "folder/PastedImage_abc.png"

    missing_name, missing_status = _resolve_source_member(index, "missing.png")
    assert missing_status == "missing"
    assert missing_name is None

from __future__ import annotations

from pathlib import Path

from lms_migration.pipeline import (
    _normalize_loose_course_content_layout,
    _recommended_course_content_destination,
    _rewrite_manifest_hrefs_for_moved_files,
)


def test_recommended_course_content_destination_routes_loose_support_files() -> None:
    assert (
        _recommended_course_content_destination("Netiquette.pdf")
        == "course-content/Netiquette.pdf"
    )
    assert (
        _recommended_course_content_destination("course-card.png")
        == "course-content/course-images/course-card.png"
    )
    assert (
        _recommended_course_content_destination("Chapter 3 Slides.pptx")
        == "course-content/powerpoints/Chapter 3 Slides.pptx"
    )
    assert (
        _recommended_course_content_destination("Mystery Shop Rubric.pdf")
        == "course-content/Mystery Shop Rubric.pdf"
    )
    assert _recommended_course_content_destination("Welcome.html") is None
    assert _recommended_course_content_destination("folder/guide.pdf") is None


def test_normalize_loose_course_content_layout_moves_files_and_skips_collisions(
    tmp_path: Path,
) -> None:
    (tmp_path / "guide.pdf").write_text("guide", encoding="utf-8")
    (tmp_path / "banner.png").write_text("image", encoding="utf-8")
    (tmp_path / "slides.pptx").write_text("ppt", encoding="utf-8")
    (tmp_path / "Mystery Shop Example.pdf").write_text("mystery", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    collision_target = tmp_path / "course-content" / "Netiquette.pdf"
    collision_target.parent.mkdir(parents=True, exist_ok=True)
    collision_target.write_text("existing", encoding="utf-8")
    (tmp_path / "Netiquette.pdf").write_text("duplicate", encoding="utf-8")

    moved, summary = _normalize_loose_course_content_layout(tmp_path)

    assert moved == {
        "banner.png": "course-content/course-images/banner.png",
        "guide.pdf": "course-content/guide.pdf",
        "Mystery Shop Example.pdf": "course-content/Mystery Shop Example.pdf",
        "slides.pptx": "course-content/powerpoints/slides.pptx",
    }
    assert summary["files_relocated"] == 4
    assert summary["collisions_skipped"] == 1
    assert (tmp_path / "course-content" / "guide.pdf").exists()
    assert (tmp_path / "course-content" / "course-images" / "banner.png").exists()
    assert (tmp_path / "course-content" / "powerpoints" / "slides.pptx").exists()
    assert (tmp_path / "course-content" / "Mystery Shop Example.pdf").exists()
    assert (tmp_path / "Netiquette.pdf").exists()
    assert (tmp_path / "index.html").exists()


def test_rewrite_manifest_hrefs_for_moved_files_updates_resource_and_file_hrefs(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "imsmanifest.xml"
    manifest.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
  <resources>
    <resource identifier="R1" type="webcontent" href="Netiquette.pdf">
      <file href="Netiquette.pdf" />
    </resource>
    <resource identifier="R2" type="webcontent" href="course-content/already-there.pdf">
      <file href="course-content/already-there.pdf" />
    </resource>
  </resources>
</manifest>
""",
        encoding="utf-8",
    )

    summary = _rewrite_manifest_hrefs_for_moved_files(
        tmp_path,
        {"Netiquette.pdf": "course-content/Netiquette.pdf"},
    )

    updated = manifest.read_text(encoding="utf-8")
    assert 'href="course-content/Netiquette.pdf"' in updated
    assert summary == {"manifest_files_changed": 1, "manifest_hrefs_rewritten": 2}

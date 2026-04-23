from __future__ import annotations

from pathlib import Path

from lms_migration.pipeline import (
    _normalize_loose_course_content_layout,
    _recommended_course_content_destination,
    _rewrite_manifest_hrefs_for_moved_files,
    _trim_unreferenced_package_files,
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


def test_trim_unreferenced_package_files_keeps_html_pages_but_prunes_orphan_binaries(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "imsmanifest.xml"
    manifest.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
  <resources>
    <resource identifier="R1" type="webcontent" href="pages/lesson.html">
      <file href="pages/lesson.html" />
    </resource>
  </resources>
</manifest>
""",
        encoding="utf-8",
    )
    lesson_dir = tmp_path / "pages"
    lesson_dir.mkdir(parents=True, exist_ok=True)
    (lesson_dir / "lesson.html").write_text(
        '<html><body><img src="../images/used.png"><a href="../docs/guide.pdf">Guide</a></body></html>',
        encoding="utf-8",
    )
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "used.png").write_text("img", encoding="utf-8")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "guide.pdf").write_text("guide", encoding="utf-8")
    (tmp_path / "orphan.pdf").write_text("orphan", encoding="utf-8")
    (lesson_dir / "orphan.html").write_text("<html>orphan</html>", encoding="utf-8")

    template_dir = tmp_path / "web_resources" / "template-images" / "icons"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "book.png").write_text("template", encoding="utf-8")

    summary = _trim_unreferenced_package_files(tmp_path)

    assert summary["files_pruned"] == 1
    assert "orphan.pdf" in summary["pruned_paths_sample"]
    assert manifest.exists()
    assert (lesson_dir / "lesson.html").exists()
    assert (lesson_dir / "orphan.html").exists()
    assert (images_dir / "used.png").exists()
    assert (docs_dir / "guide.pdf").exists()
    assert (template_dir / "book.png").exists()
    assert not (tmp_path / "orphan.pdf").exists()


def test_trim_unreferenced_package_files_keeps_metadata_linked_assets(
    tmp_path: Path,
) -> None:
    (tmp_path / "imsmanifest.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
  <resources>
    <resource identifier="R1" type="webcontent" href="pages/start.html">
      <file href="pages/start.html" />
    </resource>
  </resources>
</manifest>
""",
        encoding="utf-8",
    )
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pages" / "start.html").write_text("<html>start</html>", encoding="utf-8")
    (tmp_path / "quiz_d2l_123.xml").write_text(
        '<quiz><matimage uri="quizimages/q1.png" /></quiz>',
        encoding="utf-8",
    )
    (tmp_path / "quizimages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "quizimages" / "q1.png").write_text("img", encoding="utf-8")
    (tmp_path / "unused.docx").write_text("unused", encoding="utf-8")

    summary = _trim_unreferenced_package_files(tmp_path)

    assert summary["files_pruned"] == 1
    assert "unused.docx" in summary["pruned_paths_sample"]
    assert (tmp_path / "quiz_d2l_123.xml").exists()
    assert (tmp_path / "quizimages" / "q1.png").exists()


def test_trim_unreferenced_package_files_keeps_neutralized_original_href_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "imsmanifest.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
  <resources>
    <resource identifier="R1" type="webcontent" href="pages/video-lessons.html">
      <file href="pages/video-lessons.html" />
    </resource>
  </resources>
</manifest>
""",
        encoding="utf-8",
    )
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pages" / "video-lessons.html").write_text(
        '<html><body><a href="#" data-migration-link-status="needs-review" '
        'data-migration-original-href="../Notes and Handouts/Section 6.1 Notes.pdf">'
        "Section 6.1 Notes</a></body></html>",
        encoding="utf-8",
    )
    handouts_dir = tmp_path / "Notes and Handouts"
    handouts_dir.mkdir(parents=True, exist_ok=True)
    (handouts_dir / "Section 6.1 Notes.pdf").write_text("notes", encoding="utf-8")
    (tmp_path / "unused.pdf").write_text("unused", encoding="utf-8")

    summary = _trim_unreferenced_package_files(tmp_path)

    assert summary["files_pruned"] == 1
    assert "unused.pdf" in summary["pruned_paths_sample"]
    assert (tmp_path / "pages" / "video-lessons.html").exists()
    assert (handouts_dir / "Section 6.1 Notes.pdf").exists()
    assert not (tmp_path / "unused.pdf").exists()


def test_trim_unreferenced_package_files_keeps_urlencoded_local_image_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "imsmanifest.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
  <resources>
    <resource identifier="R1" type="webcontent" href="pages/intro.html">
      <file href="pages/intro.html" />
    </resource>
  </resources>
</manifest>
""",
        encoding="utf-8",
    )
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pages" / "intro.html").write_text(
        '<html><body><p><img src="../images/Margaret%20Mead.jpg" alt="Margaret Mead"></p></body></html>',
        encoding="utf-8",
    )
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "Margaret Mead.jpg").write_text("img", encoding="utf-8")
    (tmp_path / "unused.jpg").write_text("unused", encoding="utf-8")

    summary = _trim_unreferenced_package_files(tmp_path)

    assert summary["files_pruned"] == 1
    assert "unused.jpg" in summary["pruned_paths_sample"]
    assert (tmp_path / "pages" / "intro.html").exists()
    assert (images_dir / "Margaret Mead.jpg").exists()
    assert not (tmp_path / "unused.jpg").exists()

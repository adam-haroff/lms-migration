from __future__ import annotations

import csv
from pathlib import Path
from zipfile import ZipFile

from lms_migration.lor_saved_pages import build_saved_lor_pages_recovery_package


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_inventory(csv_path: Path) -> None:
    rows = [
        {
            "module_section": "Spiral of Silence Theory",
            "item_title": "Introduction and Objectives",
            "material_type": "contentlink",
            "href": "https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30060",
        },
        {
            "module_section": "Spiral of Silence Theory",
            "item_title": "Activities Checklist",
            "material_type": "contentlink",
            "href": "https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30061",
        },
        {
            "module_section": "Narrative Paradigm",
            "item_title": "Activities Checklist",
            "material_type": "contentlink",
            "href": "https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30031",
        },
        {
            "module_section": "Spiral of Silence Theory",
            "item_title": "Practice Quiz",
            "material_type": "contentlink",
            "href": "https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30065",
        },
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["module_section", "item_title", "material_type", "href"],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_build_saved_lor_pages_recovery_package_groups_sections_with_simple_titles(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "saved-pages"
    inventory_csv = tmp_path / "inventory.csv"
    output_dir = tmp_path / "out"
    _build_inventory(inventory_csv)

    _write(
        input_dir / "Spiral of Silence Theory" / "Activities Checklist.html",
        """
        <html><head><title>Activities Checklist</title></head>
        <body>
          <a href="https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30061">source</a>
          <h1>Activities Checklist</h1>
          <p>Spiral checklist body.</p>
        </body></html>
        """,
    )
    _write(
        input_dir / "Narrative Paradigm" / "Activities Checklist.html",
        """
        <html><head><title>Activities Checklist</title></head>
        <body>
          <a href="https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30031">source</a>
          <h1>Activities Checklist</h1>
          <p>Narrative checklist body.</p>
        </body></html>
        """,
    )
    _write(
        input_dir / "Spiral of Silence Theory" / "Practice Quiz.html",
        """
        <html><head><title>Practice Quiz</title></head>
        <body>
          <a href="https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30065">source</a>
          <h1>Practice Quiz</h1>
          <p>Quiz directions.</p>
        </body></html>
        """,
    )

    result = build_saved_lor_pages_recovery_package(
        input_dir=input_dir,
        output_dir=output_dir,
        inventory_csv=inventory_csv,
        package_title="COM 2220 Saved LOR Page Recovery",
        module_title="COM 2220 LOR Recovery",
    )

    assert result.output_zip.exists()
    report = result.report_json.read_text(encoding="utf-8")
    assert '"pages_found": 3' in report
    assert '"pages_matched_to_inventory": 3' in report

    with ZipFile(result.output_zip) as zf:
        manifest = zf.read("imsmanifest.xml").decode("utf-8", errors="ignore")
        assert "COM 2220 LOR Recovery" in manifest
        assert "Spiral of Silence Theory" in manifest
        assert "Narrative Paradigm" in manifest
        assert ">Activities Checklist<" in manifest
        assert "Practice Quiz" in manifest
        names = set(zf.namelist())
        assert "Spiral of Silence Theory/Activities Checklist.html" in names
        assert "Narrative Paradigm/Activities Checklist.html" in names


def test_build_saved_lor_pages_recovery_package_centralizes_and_deduplicates_images(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "saved-pages"
    output_dir = tmp_path / "out"

    _write(
        input_dir / "Spiral of Silence Theory" / "Practice Quiz.html",
        """
        <html><head><title>Practice Quiz</title></head>
        <body>
          <h1>Practice Quiz</h1>
          <p><img src="Practice Quiz_files/chart.png" alt="Chart"></p>
        </body></html>
        """,
    )
    _write(
        input_dir / "Narrative Paradigm" / "Practice Quiz.html",
        """
        <html><head><title>Practice Quiz</title></head>
        <body>
          <h1>Practice Quiz</h1>
          <p><img src="Practice Quiz_files/chart.png" alt="Chart"></p>
        </body></html>
        """,
    )
    _write_bytes(
        input_dir
        / "Spiral of Silence Theory"
        / "Practice Quiz_files"
        / "chart.png",
        b"fake-image",
    )
    _write_bytes(
        input_dir
        / "Narrative Paradigm"
        / "Practice Quiz_files"
        / "chart.png",
        b"fake-image",
    )

    result = build_saved_lor_pages_recovery_package(
        input_dir=input_dir,
        output_dir=output_dir,
    )

    with ZipFile(result.output_zip) as zf:
        names = set(zf.namelist())
        assert "Spiral of Silence Theory/Practice Quiz.html" in names
        assert "Narrative Paradigm/Practice Quiz.html" in names
        assert "course-content/course-images/chart.png" in names
        assert "Spiral of Silence Theory/Practice Quiz_files/chart.png" not in names
        html_doc = zf.read("Spiral of Silence Theory/Practice Quiz.html").decode(
            "utf-8", errors="ignore"
        )
        assert "../course-content/course-images/chart.png" in html_doc
        assert "max-width: 100%; height: auto;" in html_doc
        html_doc_2 = zf.read("Narrative Paradigm/Practice Quiz.html").decode(
            "utf-8", errors="ignore"
        )
        assert "../course-content/course-images/chart.png" in html_doc_2


def test_build_saved_lor_pages_recovery_package_pairs_wrapper_downloads_and_merges_intro_checklist(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "saved-pages"
    inventory_csv = tmp_path / "inventory.csv"
    output_dir = tmp_path / "out"
    _build_inventory(inventory_csv)

    _write(
        input_dir / "Activities Checklist - Example.html",
        """
        <html><body>
          <a href="https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30061">source</a>
        </body></html>
        """,
    )
    _write(
        input_dir / "Activities Checklist - Example_files" / "ActivitiesChecklist10.html",
        """
        <html><body>
          <p>To meet the learning objectives for this topic, you will complete these activities.</p>
          <ul>
            <li>Read the Introduction and Objectives page.</li>
            <li>Complete the Practice Quiz.</li>
          </ul>
        </body></html>
        """,
    )
    _write(
        input_dir / "Introduction and Objectives - Example.html",
        """
        <html><body>
          <a href="https://elearn.sinclair.edu/d2l/lor/viewer/view.d2l?ou=105593&loIdentId=30060">source</a>
        </body></html>
        """,
    )
    _write(
        input_dir
        / "Introduction and Objectives - Example_files"
        / "IntroductionandObjectives17.html",
        """
        <html><body>
          <h3>Introduction</h3>
          <p>Spiral intro body.</p>
          <h3>Objectives</h3>
          <ul>
            <li>Explain spiral of silence.</li>
          </ul>
        </body></html>
        """,
    )

    result = build_saved_lor_pages_recovery_package(
        input_dir=input_dir,
        output_dir=output_dir,
        inventory_csv=inventory_csv,
        module_title="COM 2220 LOR Recovery",
    )

    report = result.report_json.read_text(encoding="utf-8")
    assert '"pages_found": 1' in report
    assert '"checklist_source_path": "Activities Checklist - Example_files/ActivitiesChecklist10.html"' in report

    with ZipFile(result.output_zip) as zf:
        manifest = zf.read("imsmanifest.xml").decode("utf-8", errors="ignore")
        assert "Introduction and Checklist" in manifest
        assert "Activities Checklist" not in manifest
        html_doc = zf.read("Spiral of Silence Theory/Introduction and Checklist.html").decode(
            "utf-8", errors="ignore"
        )
        assert "Module Checklist" in html_doc
        assert "Explain spiral of silence." in html_doc
        assert "Complete the Practice Quiz." in html_doc

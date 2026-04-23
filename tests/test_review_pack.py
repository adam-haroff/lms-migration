from __future__ import annotations

import csv
import html as html_stdlib
import json
import re
import zipfile
from pathlib import Path

import lms_migration.review_pack as review_pack_module
from lms_migration.review_pack import build_review_pack


def _write_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


class TestReviewPackLayoutSignals:
    def test_layout_transforms_surface_in_report(self, tmp_path: Path):
        original_zip = tmp_path / "original.zip"
        converted_zip = tmp_path / "converted.zip"
        migration_json = tmp_path / "migration-report.json"
        visual_json = tmp_path / "visual-audit.json"

        html = "<html><body><h1>Page</h1><p>Body copy for review.</p></body></html>"
        _write_zip(original_zip, {"pages/example.html": html})
        _write_zip(converted_zip, {"pages/example.html": html})
        migration_json.write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "path": "pages/example.html",
                            "manual_review_issues": [],
                            "accessibility_issues": [],
                            "applied_changes": [
                                {
                                    "category": "sanitizer",
                                    "description": "Degraded display: flex/grid to display: block for Canvas compatibility",
                                    "count": 2,
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        visual_json.write_text(json.dumps({"files": []}), encoding="utf-8")

        json_path, _md_path, _html_path = build_review_pack(
            original_zip=original_zip,
            converted_zip=converted_zip,
            migration_report_json=migration_json,
            visual_audit_json=visual_json,
            output_json_path=tmp_path / "review-pack.json",
            output_markdown_path=tmp_path / "review-pack.md",
            output_html_path=tmp_path / "review-pack.html",
        )

        report = json.loads(json_path.read_text(encoding="utf-8"))
        assert report["summary"]["files_with_layout_sanitizer_flags"] == 1
        assert report["files"][0]["layout_sanitizer_flags"] == ["flex/grid layout degraded"]
        assert report["files"][0]["review_focus"] == ["layout-risk"]
        assert report["files"][0]["review_reason_summary"] == ["flex/grid layout degraded"]
        assert report["files"][0]["layout_risk_score"] >= 3
        assert report["files"][0]["review_score"] >= 1
        assert report["top_layout_risk_pages"][0]["path"] == "pages/example.html"

        shortlist_csv = json_path.with_name(f"{json_path.stem}-shortlist.csv")
        assert shortlist_csv.exists()
        shortlist_rows = list(csv.DictReader(shortlist_csv.read_text(encoding="utf-8").splitlines()))
        assert shortlist_rows[0]["path"] == "pages/example.html"
        assert shortlist_rows[0]["why_flagged"] == "flex/grid layout degraded"
        assert shortlist_rows[0]["original_dividers"] == "0"
        assert shortlist_rows[0]["converted_dividers"] == "0"

        html_text = (_html_path).read_text(encoding="utf-8")
        preview_match = re.search(
            r'<h3>Canvas Layout Preview</h3>\s*<iframe class="preview-frame"[^>]*srcdoc="([^"]+)"',
            html_text,
        )
        assert preview_match is not None
        preview_srcdoc = html_stdlib.unescape(preview_match.group(1))
        assert "border-top: 10px solid #AC1A2F" not in preview_srcdoc

        editor_match = re.search(
            r'<script type="application/json" class="editor-preview-html">(.*?)</script>',
            html_text,
            re.DOTALL,
        )
        assert editor_match is not None
        editor_preview_html = json.loads(editor_match.group(1))
        assert "border-top: 10px solid #AC1A2F" in editor_preview_html


class TestReviewPackTemplatePreviewAssets:
    def test_review_preview_inlines_template_icon_from_fallback_package(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        original_zip = tmp_path / "original.zip"
        converted_zip = tmp_path / "converted.zip"
        template_zip = tmp_path / "template.imscc"
        migration_json = tmp_path / "migration-report.json"
        visual_json = tmp_path / "visual-audit.json"

        html = (
            '<html><body><h2><img src="../template-images/icons/book.png" alt="">'
            "Read</h2><p>Body copy for review.</p></body></html>"
        )
        _write_zip(original_zip, {"pages/example.html": html})
        _write_zip(converted_zip, {"pages/example.html": html})
        _write_zip(
            template_zip,
            {"web_resources/template-images/icons/book.png": "not-a-real-png-but-good-enough"},
        )
        migration_json.write_text(
            json.dumps({"files": [{"path": "pages/example.html"}]}),
            encoding="utf-8",
        )
        visual_json.write_text(json.dumps({"files": []}), encoding="utf-8")

        monkeypatch.setattr(
            review_pack_module,
            "_default_template_package_candidates",
            lambda: (template_zip,),
        )
        review_pack_module._fallback_template_preview_asset_catalog.cache_clear()
        review_pack_module._load_template_preview_asset_catalog.cache_clear()

        try:
            _json_path, _md_path, html_path = build_review_pack(
                original_zip=original_zip,
                converted_zip=converted_zip,
                migration_report_json=migration_json,
                visual_audit_json=visual_json,
                output_json_path=tmp_path / "review-pack.json",
                output_markdown_path=tmp_path / "review-pack.md",
                output_html_path=tmp_path / "review-pack.html",
            )

            html_text = html_path.read_text(encoding="utf-8")
            preview_match = re.search(
                r'<h3>Canvas Layout Preview</h3>\s*<iframe class="preview-frame"[^>]*srcdoc="([^"]+)"',
                html_text,
            )
            assert preview_match is not None
            preview_srcdoc = html_stdlib.unescape(preview_match.group(1))
            assert "data:image/png;base64," in preview_srcdoc
            assert "../template-images/icons/book.png" not in preview_srcdoc
        finally:
            review_pack_module._fallback_template_preview_asset_catalog.cache_clear()
            review_pack_module._load_template_preview_asset_catalog.cache_clear()

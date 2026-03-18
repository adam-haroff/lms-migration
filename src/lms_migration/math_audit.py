from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from .math_tools import (
    count_absolute_equation_image_urls,
    count_display_math_blocks,
    count_empty_mathml_stubs,
    count_equation_images,
    count_equation_images_missing_alt,
    count_equation_images_missing_source,
    count_mathml,
    count_raw_tex_delimiters,
    count_wiris_annotations,
    math_modes_present,
)


_HTML_EXTENSIONS = {".html", ".htm"}


def _load_html_files(zip_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _HTML_EXTENSIONS:
                continue
            files[name] = zf.read(name).decode("utf-8", errors="ignore")
    return files


def build_math_audit(*, original_zip: Path, converted_zip: Path) -> dict:
    original_html = _load_html_files(original_zip)
    converted_html = _load_html_files(converted_zip)
    all_paths = sorted(set(original_html) | set(converted_html))
    rows: list[dict] = []

    for path in all_paths:
        original = original_html.get(path, "")
        converted = converted_html.get(path, "")
        original_modes = math_modes_present(original)
        converted_modes = math_modes_present(converted)

        row = {
            "path": path,
            "original_mathml_count": count_mathml(original),
            "converted_mathml_count": count_mathml(converted),
            "converted_wiris_annotation_count": count_wiris_annotations(converted),
            "converted_equation_image_count": count_equation_images(converted),
            "converted_raw_tex_delimiter_count": count_raw_tex_delimiters(converted),
            "converted_display_math_block_count": count_display_math_blocks(converted),
            "converted_empty_mathml_stub_count": count_empty_mathml_stubs(converted),
            "converted_absolute_equation_image_url_count": count_absolute_equation_image_urls(converted),
            "converted_equation_images_missing_alt_count": count_equation_images_missing_alt(converted),
            "converted_equation_images_missing_source_count": count_equation_images_missing_source(converted),
            "original_math_modes": list(original_modes),
            "converted_math_modes": list(converted_modes),
            "review_flags": [],
        }

        review_flags: list[str] = []
        if row["converted_raw_tex_delimiter_count"] > 0:
            review_flags.append("raw_tex_delimiters")
        if row["converted_equation_images_missing_alt_count"] > 0:
            review_flags.append("equation_images_missing_alt")
        if row["converted_equation_images_missing_source_count"] > 0:
            review_flags.append("equation_images_missing_source")
        if row["converted_absolute_equation_image_url_count"] > 0:
            review_flags.append("absolute_equation_image_urls")
        if row["converted_empty_mathml_stub_count"] > 0:
            review_flags.append("empty_mathml_stubs")
        if len(converted_modes) > 1:
            review_flags.append("mixed_math_modes")
        row["review_flags"] = review_flags
        rows.append(row)

    summary = {
        "files_scanned": len(rows),
        "files_with_math": sum(
            1
            for row in rows
            if row["converted_mathml_count"]
            or row["converted_equation_image_count"]
            or row["converted_raw_tex_delimiter_count"]
        ),
        "files_with_math_review_flags": sum(1 for row in rows if row["review_flags"]),
        "files_with_mixed_math_modes": sum(1 for row in rows if "mixed_math_modes" in row["review_flags"]),
        "total_original_mathml": sum(row["original_mathml_count"] for row in rows),
        "total_converted_mathml": sum(row["converted_mathml_count"] for row in rows),
        "total_converted_wiris_annotations": sum(row["converted_wiris_annotation_count"] for row in rows),
        "total_converted_equation_images": sum(row["converted_equation_image_count"] for row in rows),
        "total_converted_raw_tex_delimiters": sum(row["converted_raw_tex_delimiter_count"] for row in rows),
        "total_converted_display_math_blocks": sum(row["converted_display_math_block_count"] for row in rows),
        "total_converted_empty_mathml_stubs": sum(row["converted_empty_mathml_stub_count"] for row in rows),
        "total_absolute_equation_image_urls": sum(row["converted_absolute_equation_image_url_count"] for row in rows),
        "total_equation_images_missing_alt": sum(row["converted_equation_images_missing_alt_count"] for row in rows),
        "total_equation_images_missing_source": sum(
            row["converted_equation_images_missing_source_count"] for row in rows
        ),
    }

    return {
        "inputs": {
            "original_zip": str(original_zip),
            "converted_zip": str(converted_zip),
        },
        "summary": summary,
        "files": rows,
    }


def _default_output_json(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -4]
    return converted_zip.with_name(f"{stem}.math-audit.json")


def _default_output_markdown(output_json: Path) -> Path:
    return output_json.with_suffix(".md")


def _write_markdown(report: dict, output_markdown: Path) -> None:
    summary = report.get("summary", {})
    flagged_files = [row for row in report.get("files", []) if row.get("review_flags")]
    lines = [
        "# Math Audit",
        "",
        "## Summary",
        "",
        f"- Files scanned: {summary.get('files_scanned', 0)}",
        f"- Files with math: {summary.get('files_with_math', 0)}",
        f"- Files with math review flags: {summary.get('files_with_math_review_flags', 0)}",
        f"- Files with mixed math modes: {summary.get('files_with_mixed_math_modes', 0)}",
        f"- MathML expressions in original: {summary.get('total_original_mathml', 0)}",
        f"- MathML expressions in converted: {summary.get('total_converted_mathml', 0)}",
        f"- WIRIS annotations in converted: {summary.get('total_converted_wiris_annotations', 0)}",
        f"- Canvas equation images in converted: {summary.get('total_converted_equation_images', 0)}",
        f"- Raw TeX delimiters in converted: {summary.get('total_converted_raw_tex_delimiters', 0)}",
        f"- Display math blocks in converted: {summary.get('total_converted_display_math_blocks', 0)}",
        f"- Empty MathML stubs in converted: {summary.get('total_converted_empty_mathml_stubs', 0)}",
        f"- Absolute equation-image URLs: {summary.get('total_absolute_equation_image_urls', 0)}",
        f"- Equation images missing alt: {summary.get('total_equation_images_missing_alt', 0)}",
        f"- Equation images missing source metadata: {summary.get('total_equation_images_missing_source', 0)}",
        "",
        "## Files With Math Review Flags",
        "",
    ]
    if not flagged_files:
        lines.append("- None")
    else:
        for row in flagged_files:
            lines.append(f"- `{row.get('path', '')}`: {', '.join(row.get('review_flags', []))}")
    lines.append("")
    output_markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lms-math-audit",
        description="Audit original and converted course HTML for math-equation handling and review flags.",
    )
    parser.add_argument("--original-zip", type=Path, required=True, help="Path to original D2L export zip")
    parser.add_argument("--converted-zip", type=Path, required=True, help="Path to converted canvas-ready zip")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path")
    parser.add_argument("--output-markdown", type=Path, default=None, help="Optional output Markdown path")
    args = parser.parse_args()

    if not args.original_zip.exists():
        parser.error(f"Original zip does not exist: {args.original_zip}")
    if not args.converted_zip.exists():
        parser.error(f"Converted zip does not exist: {args.converted_zip}")

    output_json = args.output_json or _default_output_json(args.converted_zip)
    output_markdown = args.output_markdown or _default_output_markdown(output_json)

    report = build_math_audit(original_zip=args.original_zip, converted_zip=args.converted_zip)
    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, output_markdown)

    print(f"Math audit JSON: {output_json}")
    print(f"Math audit Markdown: {output_markdown}")


if __name__ == "__main__":
    main()

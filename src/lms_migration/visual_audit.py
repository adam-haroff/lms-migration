from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from zipfile import ZipFile


_HTML_EXTENSIONS = {".html", ".htm"}


def _load_html_files(zip_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            suffix = Path(name).suffix.lower()
            if suffix not in _HTML_EXTENSIONS:
                continue
            files[name] = zf.read(name).decode("utf-8", errors="ignore")
    return files


def _first_block_text(content: str) -> str:
    block_pattern = re.compile(
        r"<(?P<tag>h[1-6]|p)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in block_pattern.finditer(content):
        text = re.sub(r"<[^>]+>", " ", match.group("body"))
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if text.lower() == "printer-friendly version":
            continue
        return text
    return ""


def _title_text(content: str) -> str:
    match = re.search(r"<title\b[^>]*>(?P<title>.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return ""
    title = re.sub(r"<[^>]+>", " ", match.group("title"))
    title = html.unescape(title)
    return re.sub(r"\s+", " ", title).strip()


def _normalized(value: str) -> str:
    lowered = value.lower().strip()
    lowered = lowered.replace("&", "and")
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def build_visual_audit(*, original_zip: Path, converted_zip: Path) -> dict:
    original_html = _load_html_files(original_zip)
    converted_html = _load_html_files(converted_zip)

    all_paths = sorted(set(original_html) | set(converted_html))
    rows: list[dict] = []

    for path in all_paths:
        original = original_html.get(path, "")
        converted = converted_html.get(path, "")

        title = _title_text(converted)
        first_block = _first_block_text(converted)
        duplicate_title_block = bool(title and first_block and _normalized(title) == _normalized(first_block))

        accordion_cards_original = len(
            re.findall(
                r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bcard\b[^"\']*["\'][^>]*>',
                original,
                flags=re.IGNORECASE,
            )
        )
        details_converted = len(
            re.findall(
                r"<details\b[^>]*class\s*=\s*['\"][^'\"]*\bmigration-accordion\b[^'\"]*['\"][^>]*>",
                converted,
                flags=re.IGNORECASE,
            )
        )
        shared_template_refs = converted.lower().count("/shared/brightspace_html_template/")
        title_tags_converted = len(re.findall(r"<title\b", converted, flags=re.IGNORECASE))
        hr_tags = re.findall(r"<hr\b[^>]*>", converted, flags=re.IGNORECASE)
        hr_nonstandard = sum(1 for tag in hr_tags if "height: 2px" not in tag.lower())
        icon_tags = re.findall(
            r'<img\b[^>]*src\s*=\s*["\'][^"\']*templateassets/[^"\']+["\'][^>]*>',
            converted,
            flags=re.IGNORECASE,
        )
        icon_missing_size = 0
        for tag in icon_tags:
            lowered = tag.lower()
            if any(name in lowered for name in ("banner-", "footer.png", "course-card.png")):
                continue
            if "width: 24px" in lowered or "width: 45px" in lowered or "width: 72px" in lowered:
                continue
            icon_missing_size += 1
        original_mathml = len(re.findall(r"<math\b", original, flags=re.IGNORECASE))
        converted_mathml = len(re.findall(r"<math\b", converted, flags=re.IGNORECASE))
        converted_wiris_annotations = len(
            re.findall(
                r"<annotation\b[^>]*\bencoding\s*=\s*([\"'])wiris\1",
                converted,
                flags=re.IGNORECASE,
            )
        )

        rows.append(
            {
                "path": path,
                "original_exists": bool(original),
                "converted_exists": bool(converted),
                "duplicate_title_first_block": duplicate_title_block,
                "original_accordion_cards": accordion_cards_original,
                "converted_details_blocks": details_converted,
                "converted_shared_template_refs": shared_template_refs,
                "converted_title_tags": title_tags_converted,
                "converted_hr_nonstandard": hr_nonstandard,
                "converted_template_icons_missing_size_style": icon_missing_size,
                "original_mathml_count": original_mathml,
                "converted_mathml_count": converted_mathml,
                "converted_wiris_annotation_count": converted_wiris_annotations,
            }
        )

    summary = {
        "files_scanned": len(rows),
        "files_with_duplicate_title_first_block": sum(1 for row in rows if row["duplicate_title_first_block"]),
        "files_with_remaining_shared_template_refs": sum(
            1 for row in rows if row["converted_shared_template_refs"] > 0
        ),
        "files_with_remaining_title_tags": sum(1 for row in rows if row["converted_title_tags"] > 0),
        "files_with_nonstandard_hr": sum(1 for row in rows if row["converted_hr_nonstandard"] > 0),
        "files_with_icon_size_anomalies": sum(
            1 for row in rows if row["converted_template_icons_missing_size_style"] > 0
        ),
        "total_original_accordion_cards": sum(row["original_accordion_cards"] for row in rows),
        "total_converted_details_blocks": sum(row["converted_details_blocks"] for row in rows),
        "total_original_mathml": sum(row["original_mathml_count"] for row in rows),
        "total_converted_mathml": sum(row["converted_mathml_count"] for row in rows),
        "total_converted_wiris_annotations": sum(row["converted_wiris_annotation_count"] for row in rows),
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
    return converted_zip.with_name(f"{stem}.visual-audit.json")


def _default_output_markdown(output_json: Path) -> Path:
    return output_json.with_suffix(".md")


def _write_markdown(report: dict, output_markdown: Path) -> None:
    summary = report.get("summary", {})
    lines = [
        "# Visual Audit",
        "",
        "## Summary",
        "",
        f"- Files scanned: {summary.get('files_scanned', 0)}",
        f"- Files with duplicate title/first-block: {summary.get('files_with_duplicate_title_first_block', 0)}",
        f"- Files with shared template refs remaining: {summary.get('files_with_remaining_shared_template_refs', 0)}",
        f"- Files with <title> tags remaining: {summary.get('files_with_remaining_title_tags', 0)}",
        f"- Files with nonstandard horizontal dividers: {summary.get('files_with_nonstandard_hr', 0)}",
        f"- Files with icon sizing anomalies: {summary.get('files_with_icon_size_anomalies', 0)}",
        f"- Accordion cards in original: {summary.get('total_original_accordion_cards', 0)}",
        f"- Details blocks in converted: {summary.get('total_converted_details_blocks', 0)}",
        f"- MathML expressions in original: {summary.get('total_original_mathml', 0)}",
        f"- MathML expressions in converted: {summary.get('total_converted_mathml', 0)}",
        f"- WIRIS annotation nodes remaining in converted: {summary.get('total_converted_wiris_annotations', 0)}",
        "",
    ]
    output_markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lms-visual-audit",
        description="Compare original D2L HTML and converted Canvas-ready HTML for visual-structure deltas.",
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

    report = build_visual_audit(
        original_zip=args.original_zip,
        converted_zip=args.converted_zip,
    )
    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, output_markdown)

    print(f"Visual audit JSON: {output_json}")
    print(f"Visual audit Markdown: {output_markdown}")


if __name__ == "__main__":
    main()

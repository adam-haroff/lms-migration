from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile


_SHARED_TEMPLATE_RE = re.compile(r"^/?shared/brightspace_html_template/", flags=re.IGNORECASE)
_ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
}


def _extract_refs(html_text: str) -> list[str]:
    return [match.strip() for match in re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html_text, flags=re.IGNORECASE)]


def _template_basename(url: str) -> str:
    parsed = urlparse(url.strip())
    path_text = (parsed.path or "").strip().replace("\\", "/")
    if not path_text:
        return ""
    return Path(path_text).name.strip().lower()


def _scan_brightspace_template_refs(examples_dir: Path) -> tuple[Counter[str], dict[str, Counter[str]]]:
    overall = Counter()
    by_course: dict[str, Counter[str]] = defaultdict(Counter)

    for course_dir in sorted(path for path in examples_dir.iterdir() if path.is_dir()):
        if course_dir.name.lower() == "template":
            continue
        package_path = next((path for path in sorted(course_dir.glob("*.zip"))), None)
        if package_path is None:
            continue

        with ZipFile(package_path, "r") as zf:
            html_paths = [name for name in zf.namelist() if name.lower().endswith((".html", ".htm"))]
            for html_path in html_paths:
                text = zf.read(html_path).decode("utf-8", errors="ignore")
                for ref in _extract_refs(text):
                    if not _SHARED_TEMPLATE_RE.match(ref):
                        continue
                    basename = _template_basename(ref)
                    if not basename:
                        continue
                    overall[basename] += 1
                    by_course[course_dir.name][basename] += 1

    return overall, dict(by_course)


def _analyze_template_package(template_package: Path) -> dict:
    with ZipFile(template_package, "r") as zf:
        all_files = [name for name in zf.namelist() if not name.endswith("/")]

    assets = [name for name in all_files if Path(name).suffix.lower() in _ASSET_SUFFIXES]
    wiki_pages = [name for name in all_files if name.lower().startswith("wiki_content/") and name.lower().endswith(".html")]
    icon_assets = [name for name in assets if "/icons/" in name.lower()]
    banner_assets = [name for name in assets if "/banners/" in name.lower()]
    button_assets = [name for name in assets if "/buttons/" in name.lower()]

    by_basename: dict[str, list[str]] = defaultdict(list)
    for path in assets:
        by_basename[Path(path).name.strip().lower()].append(path)

    return {
        "template_package": str(template_package),
        "counts": {
            "all_files": len(all_files),
            "assets": len(assets),
            "wiki_pages": len(wiki_pages),
            "icons": len(icon_assets),
            "banners": len(banner_assets),
            "buttons": len(button_assets),
        },
        "asset_paths": assets,
        "wiki_pages": wiki_pages,
        "assets_by_basename": dict(by_basename),
    }


def analyze_template_compatibility(
    *,
    template_package: Path,
    examples_dir: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    template = _analyze_template_package(template_package)
    brightspace_refs, refs_by_course = _scan_brightspace_template_refs(examples_dir)
    assets_by_basename = template["assets_by_basename"]

    compatibility_rows = []
    matched_hits = 0
    for basename, occurrences in brightspace_refs.most_common():
        matches = assets_by_basename.get(basename, [])
        if matches:
            matched_hits += occurrences
        compatibility_rows.append(
            {
                "brightspace_basename": basename,
                "occurrences": occurrences,
                "available_in_template": bool(matches),
                "template_asset_paths": matches,
            }
        )

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "template_package": str(template_package),
            "examples_dir": str(examples_dir),
        },
        "template_summary": template["counts"],
        "brightspace_ref_summary": {
            "total_occurrences": sum(brightspace_refs.values()),
            "unique_basenames": len(brightspace_refs),
            "matched_occurrences": matched_hits,
            "unmatched_occurrences": sum(brightspace_refs.values()) - matched_hits,
            "matched_unique_basenames": sum(1 for row in compatibility_rows if row["available_in_template"]),
            "unmatched_unique_basenames": sum(1 for row in compatibility_rows if not row["available_in_template"]),
        },
        "compatibility_rows": compatibility_rows,
        "by_course_top_template_refs": {
            course_code: dict(counter.most_common(20))
            for course_code, counter in sorted(refs_by_course.items())
        },
        "template_wiki_pages": template["wiki_pages"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "template-compatibility-analysis.json"
    md_path = output_dir / "template-compatibility-analysis.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    summary = report["brightspace_ref_summary"]
    lines = [
        "# Template Compatibility Analysis",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Inputs",
        "",
        f"- Template package: `{template_package}`",
        f"- Examples dir: `{examples_dir}`",
        "",
        "## Template Summary",
        "",
        f"- Files: {template['counts']['all_files']}",
        f"- Assets: {template['counts']['assets']}",
        f"- Wiki pages: {template['counts']['wiki_pages']}",
        f"- Icon assets: {template['counts']['icons']}",
        f"- Banner assets: {template['counts']['banners']}",
        f"- Button assets: {template['counts']['buttons']}",
        "",
        "## Brightspace Reference Compatibility",
        "",
        f"- Total Brightspace template reference hits in examples: {summary['total_occurrences']}",
        f"- Unique Brightspace basenames: {summary['unique_basenames']}",
        f"- Matched by basename in template package: {summary['matched_occurrences']} hits across {summary['matched_unique_basenames']} names",
        f"- Unmatched by basename: {summary['unmatched_occurrences']} hits across {summary['unmatched_unique_basenames']} names",
        "",
        "## Top Unmatched Brightspace Basenames",
        "",
    ]

    unmatched = [row for row in compatibility_rows if not row["available_in_template"]][:20]
    if unmatched:
        for row in unmatched:
            lines.append(f"- {row['brightspace_basename']} ({row['occurrences']})")
    else:
        lines.append("- None")

    lines.extend(["", "## Top Matched Brightspace Basenames", ""])
    matched = [row for row in compatibility_rows if row["available_in_template"]][:20]
    if matched:
        for row in matched:
            lines.append(f"- {row['brightspace_basename']} ({row['occurrences']})")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Practical Next Step",
            "",
            "- Import this template package into the Canvas destination course (or Blueprint) before the migrated package, then run post-import auto-relink.",
            "- For unmatched Brightspace basenames, create explicit alias mapping rules in the app (do not guess replacements).",
            "",
        ]
    )

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-analyze-template-package",
        description="Analyze Canvas template IMSCC and compare against Brightspace-template refs in D2L examples.",
    )
    parser.add_argument(
        "--template-package",
        type=Path,
        required=True,
        help="Path to Canvas template IMSCC export.",
    )
    parser.add_argument(
        "--examples-dir",
        type=Path,
        default=Path("resources/examples"),
        help="Directory containing D2L example course zips.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/examples"),
        help="Directory for report outputs.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.template_package.exists():
        parser.error(f"Template package does not exist: {args.template_package}")
    if not args.examples_dir.exists():
        parser.error(f"Examples directory does not exist: {args.examples_dir}")

    json_path, md_path = analyze_template_compatibility(
        template_package=args.template_package,
        examples_dir=args.examples_dir,
        output_dir=args.output_dir,
    )
    print(f"Template compatibility JSON: {json_path}")
    print(f"Template compatibility Markdown: {md_path}")


if __name__ == "__main__":
    main()

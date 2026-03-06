from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .canvas_api import (
    fetch_course,
    fetch_course_announcements,
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_files,
    fetch_course_modules,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
)


def _default_output_json(*, output_dir: Path, course_id: str) -> Path:
    return output_dir / f"canvas-course-{course_id}.snapshot.json"


def _default_output_md(*, output_dir: Path, course_id: str) -> Path:
    return output_dir / f"canvas-course-{course_id}.snapshot.md"


def snapshot_canvas_course(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    output_markdown_path: Path | None = None,
) -> tuple[Path, Path]:
    normalized_base = normalize_base_url(base_url)

    course = fetch_course(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    files = fetch_course_files(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    modules = fetch_course_modules(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    assignments = fetch_course_assignments(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    discussions = fetch_course_discussion_topics(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    announcements = fetch_course_announcements(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    pages_summary = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    page_records = []
    for page in pages_summary:
        if not isinstance(page, dict):
            continue
        page_url = str(page.get("url", "")).strip()
        if not page_url:
            continue
        full_page = fetch_course_page(
            base_url=normalized_base,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        page_records.append(full_page)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": normalized_base,
        "course_id": str(course_id),
        "course": course,
        "counts": {
            "pages": len(page_records),
            "modules": len(modules),
            "files": len(files),
            "assignments": len(assignments),
            "discussions": len(discussions),
            "announcements": len(announcements),
        },
        "pages": page_records,
        "modules": modules,
        "files": files,
        "assignments": assignments,
        "discussions": discussions,
        "announcements": announcements,
    }

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = output_markdown_path or output_json_path.with_suffix(".md")
    lines = [
        "# Canvas Course Snapshot",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Course",
        "",
        f"- Base URL: `{normalized_base}`",
        f"- Course ID: `{course_id}`",
        f"- Course Name: `{course.get('name', '')}`",
        f"- Course Code: `{course.get('course_code', '')}`",
        "",
        "## Counts",
        "",
        f"- Pages: {report['counts']['pages']}",
        f"- Modules: {report['counts']['modules']}",
        f"- Files: {report['counts']['files']}",
        f"- Assignments: {report['counts']['assignments']}",
        f"- Discussions: {report['counts']['discussions']}",
        f"- Announcements: {report['counts']['announcements']}",
        "",
        "## Outputs",
        "",
        f"- Snapshot JSON: `{output_json_path}`",
        f"- Snapshot Markdown: `{md_path}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return output_json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-snapshot",
        description="Capture a local JSON snapshot of a Canvas course via API.",
    )
    parser.add_argument("--base-url", required=True, type=str, help="Canvas base URL")
    parser.add_argument("--course-id", required=True, type=str, help="Canvas course ID")
    parser.add_argument("--token", required=True, type=str, help="Canvas API token")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory to write snapshot artifacts",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional explicit JSON output path",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional explicit Markdown output path",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_json = args.output_json or _default_output_json(
        output_dir=args.output_dir,
        course_id=str(args.course_id),
    )
    output_md = args.output_markdown or _default_output_md(
        output_dir=args.output_dir,
        course_id=str(args.course_id),
    )

    json_path, md_path = snapshot_canvas_course(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        output_json_path=output_json,
        output_markdown_path=output_md,
    )
    print(f"Canvas snapshot JSON: {json_path}")
    print(f"Canvas snapshot Markdown: {md_path}")


if __name__ == "__main__":
    main()

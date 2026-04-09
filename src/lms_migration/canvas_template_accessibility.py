from __future__ import annotations

import argparse
import json
from pathlib import Path

from .canvas_api import (
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
    update_course_assignment_description,
    update_course_page_body,
    update_discussion_topic_message,
)
from .html_tools import AppliedChange, apply_accessibility_markup_fixes


def _serialize_changes(changes: list[AppliedChange]) -> list[dict[str, object]]:
    return [
        {
            "category": change.category,
            "description": change.description,
            "count": change.count,
        }
        for change in changes
    ]


def auto_fix_template_accessibility(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    normalized_base = normalize_base_url(base_url)

    page_summaries = fetch_course_pages(
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

    page_updates: list[dict[str, object]] = []
    assignment_updates: list[dict[str, object]] = []
    discussion_updates: list[dict[str, object]] = []
    pages_updated = 0
    assignments_updated = 0
    discussions_updated = 0

    for page_summary in page_summaries:
        if not isinstance(page_summary, dict):
            continue
        page_url = str(page_summary.get("url", "")).strip()
        if not page_url:
            continue
        page = fetch_course_page(
            base_url=normalized_base,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        body = str(page.get("body") or "").strip()
        if not body:
            continue

        fixed_body, changes = apply_accessibility_markup_fixes(
            body, repair_heading_jumps=True
        )
        changed = fixed_body != body
        if changed and not dry_run:
            update_course_page_body(
                base_url=normalized_base,
                course_id=course_id,
                page_url=page_url,
                body_html=fixed_body,
                token=token,
            )
        if changed:
            pages_updated += 1
        page_updates.append(
            {
                "page_url": page_url,
                "title": str(page.get("title", "")).strip(),
                "changed": changed,
                "changes": _serialize_changes(changes),
            }
        )

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        assignment_id = assignment.get("id")
        if not assignment_id:
            continue
        body = str(assignment.get("description") or "").strip()
        if not body:
            continue

        fixed_body, changes = apply_accessibility_markup_fixes(
            body, repair_heading_jumps=True
        )
        changed = fixed_body != body
        if changed and not dry_run:
            update_course_assignment_description(
                base_url=normalized_base,
                course_id=course_id,
                assignment_id=assignment_id,
                description_html=fixed_body,
                token=token,
            )
        if changed:
            assignments_updated += 1
        assignment_updates.append(
            {
                "assignment_id": str(assignment_id),
                "title": str(assignment.get("name", "")).strip(),
                "changed": changed,
                "changes": _serialize_changes(changes),
            }
        )

    for discussion in discussions:
        if not isinstance(discussion, dict):
            continue
        topic_id = discussion.get("id")
        if not topic_id:
            continue
        body = str(discussion.get("message") or "").strip()
        if not body:
            continue

        fixed_body, changes = apply_accessibility_markup_fixes(
            body, repair_heading_jumps=True
        )
        changed = fixed_body != body
        if changed and not dry_run:
            update_discussion_topic_message(
                base_url=normalized_base,
                course_id=course_id,
                topic_id=topic_id,
                message_html=fixed_body,
                token=token,
            )
        if changed:
            discussions_updated += 1
        discussion_updates.append(
            {
                "topic_id": str(topic_id),
                "title": str(discussion.get("title", "")).strip(),
                "changed": changed,
                "changes": _serialize_changes(changes),
            }
        )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "course_id": str(course_id),
        "dry_run": dry_run,
        "summary": {
            "pages_updated": pages_updated,
            "assignments_updated": assignments_updated,
            "discussions_updated": discussions_updated,
            "total_updates": pages_updated + assignments_updated + discussions_updated,
        },
        "pages": page_updates,
        "assignments": assignment_updates,
        "discussions": discussion_updates,
    }
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply safe accessibility markup fixes to Canvas pages, assignments, and discussions."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", required=True, dest="output_json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    auto_fix_template_accessibility(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        output_json_path=Path(args.output_json),
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

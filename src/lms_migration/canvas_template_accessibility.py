from __future__ import annotations

import argparse
import json
import re
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
from .html_tools import (
    AppliedChange,
    _extract_inline_style_map,
    _is_blackish_color,
    _merge_inline_style,
    _normalize_descendant_white_text_styles,
    apply_accessibility_markup_fixes,
)


_HEADING_BLOCK_RE = re.compile(
    r"(?P<open><h(?P<level>[1-6])(?P<attrs>\b[^>]*)>)(?P<body>.*?)(?P<close></h(?P=level)>)",
    flags=re.IGNORECASE | re.DOTALL,
)


def _normalize_title_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _demote_body_h1s_to_h2(content: str) -> tuple[str, int]:
    updates = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal updates
        level = int(match.group("level"))
        if level != 1:
            return match.group(0)
        updates += 1
        return f"<h2{match.group('attrs')}>{match.group('body')}</h2>"

    updated = _HEADING_BLOCK_RE.sub(replace, content)
    return updated, updates


def _normalize_gray_heading_blocks(content: str) -> tuple[str, int]:
    updates = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal updates
        open_tag = match.group("open")
        body = match.group("body")
        style_map = _extract_inline_style_map(open_tag)
        background_value = (
            style_map.get("background-color")
            or style_map.get("background")
            or ""
        ).replace(" ", "").lower()
        if "#cacaca" not in background_value and "rgb(202,202,202)" not in background_value:
            return match.group(0)

        updated_open, open_changed = _merge_inline_style(
            open_tag,
            {
                "color": "#000000",
                "background": "#CACACA",
                "padding-left": "5px",
                "text-align": "left",
            },
        )
        rebuilt_body, nested_changes = _normalize_descendant_white_text_styles(body)
        if not open_changed and nested_changes == 0:
            return match.group(0)
        updates += 1
        return f"{updated_open}{rebuilt_body}{match.group('close')}"

    updated = _HEADING_BLOCK_RE.sub(replace, content)
    return updated, updates


def apply_template_page_accessibility_presets(
    *,
    title: str,
    body_html: str,
) -> tuple[str, list[AppliedChange]]:
    normalized_title = _normalize_title_key(title)
    updated = body_html
    applied: list[AppliedChange] = []

    if normalized_title == "template: image customizations":
        updated, gray_heading_updates = _normalize_gray_heading_blocks(updated)
        if gray_heading_updates:
            applied.append(
                AppliedChange(
                    category="accessibility",
                    description="Normalized gray template section headings to use accessible black-on-gray contrast",
                    count=gray_heading_updates,
                )
            )

        updated, demoted_h1_count = _demote_body_h1s_to_h2(updated)
        if demoted_h1_count:
            applied.append(
                AppliedChange(
                    category="accessibility",
                    description="Demoted body H1 headings to H2 on template reference pages",
                    count=demoted_h1_count,
                )
            )

    return updated, applied


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
        fixed_body, preset_changes = apply_template_page_accessibility_presets(
            title=str(page.get("title", "")).strip(),
            body_html=fixed_body,
        )
        changes.extend(preset_changes)
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

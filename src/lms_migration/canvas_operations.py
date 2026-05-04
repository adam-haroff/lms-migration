from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .canvas_api import (
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_page,
    fetch_course_pages,
    update_course_assignment,
    update_course_page,
    update_discussion_topic,
)

_VALID_MATCH_MODES = {"contains", "exact", "regex"}

_SUBMISSION_PRESETS: dict[str, list[str] | None] = {
    "keep-current": None,
    "file-upload-only": ["online_upload"],
    "text-entry-only": ["online_text_entry"],
    "external-tool": ["external_tool"],
    "no-submission": ["none"],
    "on-paper": ["on_paper"],
}


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_report(
    *,
    report: dict[str, Any],
    output_json_path: Path,
    output_markdown_path: Path,
    markdown_text: str,
) -> dict[str, Any]:
    _ensure_parent(output_json_path)
    _ensure_parent(output_markdown_path)
    output_json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    output_markdown_path.write_text(markdown_text.rstrip() + "\n", encoding="utf-8")
    return report


def _title_matches(
    title: str,
    pattern: str,
    *,
    match_mode: str,
    case_sensitive: bool,
) -> bool:
    normalized_pattern = pattern.strip()
    if not normalized_pattern:
        raise ValueError("Title pattern is required.")
    if match_mode not in _VALID_MATCH_MODES:
        raise ValueError(f"Unsupported match mode: {match_mode}")

    if match_mode == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.search(normalized_pattern, title, flags=flags) is not None

    left = title if case_sensitive else title.lower()
    right = normalized_pattern if case_sensitive else normalized_pattern.lower()
    if match_mode == "exact":
        return left == right
    return right in left


def _replace_text(
    body_html: str,
    *,
    find_text: str,
    replace_text: str,
    regex: bool,
    case_sensitive: bool,
) -> tuple[str, int]:
    if not find_text:
        raise ValueError("Find text is required.")
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.subn(find_text, replace_text, body_html, flags=flags)
    pattern = re.escape(find_text)
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.subn(pattern, replace_text, body_html, flags=flags)


def _normalize_submission_types(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_points_possible(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _resolve_submission_preset(preset: str) -> list[str] | None:
    normalized = preset.strip()
    if normalized not in _SUBMISSION_PRESETS:
        raise ValueError(f"Unsupported submission preset: {preset}")
    return _SUBMISSION_PRESETS[normalized]


def _markdown_for_page_replace(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Canvas Operations Report",
        "",
        "## Page Text Replace",
        "",
        f"- Dry run: `{summary.get('dry_run', True)}`",
        f"- Pages scanned: `{summary.get('pages_scanned', 0)}`",
        f"- Pages matched: `{summary.get('pages_matched', 0)}`",
        f"- Pages with replacements: `{summary.get('pages_with_replacements', 0)}`",
        f"- Pages updated: `{summary.get('pages_updated', 0)}`",
        f"- Total replacements: `{summary.get('total_replacements', 0)}`",
        "",
        "## Matches",
        "",
    ]
    matches = report.get("matches", [])
    if not matches:
        lines.append("- No matching pages needed changes.")
        return "\n".join(lines)
    for item in matches:
        lines.append(
            f"- `{item.get('title', '')}`"
            f" (`{item.get('page_url', '')}`)"
            f" replacements=`{item.get('replacement_count', 0)}`"
            f" updated=`{item.get('updated', False)}`"
        )
    return "\n".join(lines)


def _markdown_for_assignment_settings(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Canvas Operations Report",
        "",
        "## Assignment Settings Update",
        "",
        f"- Dry run: `{summary.get('dry_run', True)}`",
        f"- Assignments scanned: `{summary.get('assignments_scanned', 0)}`",
        f"- Assignments matched: `{summary.get('assignments_matched', 0)}`",
        f"- Assignments needing changes: `{summary.get('assignments_needing_changes', 0)}`",
        f"- Assignments updated: `{summary.get('assignments_updated', 0)}`",
        "",
        "## Matches",
        "",
    ]
    matches = report.get("matches", [])
    if not matches:
        lines.append("- No matching assignments needed changes.")
        return "\n".join(lines)
    for item in matches:
        change_bits = item.get("changes", {})
        lines.append(
            f"- `{item.get('title', '')}`"
            f" points=`{change_bits.get('points_possible', 'unchanged')}`"
            f" submission_types=`{change_bits.get('submission_types', 'unchanged')}`"
            f" updated=`{item.get('updated', False)}`"
        )
    return "\n".join(lines)


def _markdown_for_publish_state(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Canvas Operations Report",
        "",
        "## Publish State Update",
        "",
        f"- Dry run: `{summary.get('dry_run', True)}`",
        f"- Items scanned: `{summary.get('items_scanned', 0)}`",
        f"- Items matched: `{summary.get('items_matched', 0)}`",
        f"- Items needing changes: `{summary.get('items_needing_changes', 0)}`",
        f"- Items updated: `{summary.get('items_updated', 0)}`",
        "",
        "## Matches",
        "",
    ]
    matches = report.get("matches", [])
    if not matches:
        lines.append("- No matching items needed publish-state changes.")
        return "\n".join(lines)
    for item in matches:
        lines.append(
            f"- `{item.get('kind', '')}` `{item.get('title', '')}`"
            f" before=`{item.get('published_before', False)}`"
            f" target=`{item.get('published_target', False)}`"
            f" updated=`{item.get('updated', False)}`"
        )
    return "\n".join(lines)


def bulk_replace_page_text(
    *,
    base_url: str,
    course_id: str,
    token: str,
    title_pattern: str,
    match_mode: str,
    case_sensitive: bool,
    find_text: str,
    replace_text: str,
    regex: bool,
    dry_run: bool,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    pages = fetch_course_pages(base_url=base_url, course_id=course_id, token=token)
    matches: list[dict[str, Any]] = []
    pages_matched = 0
    total_replacements = 0
    pages_updated = 0

    for page in pages:
        title = str(page.get("title") or "").strip()
        page_url = str(page.get("url") or "").strip()
        if not title or not page_url:
            continue
        if not _title_matches(
            title, title_pattern, match_mode=match_mode, case_sensitive=case_sensitive
        ):
            continue
        pages_matched += 1
        page_payload = fetch_course_page(
            base_url=base_url, course_id=course_id, page_url=page_url, token=token
        )
        body_html = str(page_payload.get("body") or "")
        updated_body, replacement_count = _replace_text(
            body_html,
            find_text=find_text,
            replace_text=replace_text,
            regex=regex,
            case_sensitive=case_sensitive,
        )
        if replacement_count <= 0:
            continue
        total_replacements += replacement_count
        updated = False
        if not dry_run:
            update_course_page(
                base_url=base_url,
                course_id=course_id,
                page_url=page_url,
                token=token,
                body_html=updated_body,
            )
            updated = True
            pages_updated += 1
        matches.append(
            {
                "title": title,
                "page_url": page_url,
                "replacement_count": replacement_count,
                "updated": updated,
            }
        )

    report = {
        "operation": "page_text_replace",
        "parameters": {
            "title_pattern": title_pattern,
            "match_mode": match_mode,
            "case_sensitive": case_sensitive,
            "find_text": find_text,
            "replace_text": replace_text,
            "regex": regex,
            "dry_run": dry_run,
        },
        "summary": {
            "dry_run": dry_run,
            "pages_scanned": len(pages),
            "pages_matched": pages_matched,
            "pages_with_replacements": len(matches),
            "pages_updated": pages_updated,
            "total_replacements": total_replacements,
        },
        "matches": matches,
    }
    return _write_report(
        report=report,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        markdown_text=_markdown_for_page_replace(report),
    )


def bulk_update_assignment_settings(
    *,
    base_url: str,
    course_id: str,
    token: str,
    title_pattern: str,
    match_mode: str,
    case_sensitive: bool,
    points_possible: float | None,
    submission_preset: str,
    dry_run: bool,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    assignments = fetch_course_assignments(
        base_url=base_url, course_id=course_id, token=token
    )
    target_submission_types = _resolve_submission_preset(submission_preset)
    matches: list[dict[str, Any]] = []
    assignments_matched = 0
    assignments_updated = 0

    for assignment in assignments:
        title = str(assignment.get("name") or "").strip()
        assignment_id = assignment.get("id")
        if not title or assignment_id in (None, ""):
            continue
        if not _title_matches(
            title, title_pattern, match_mode=match_mode, case_sensitive=case_sensitive
        ):
            continue
        assignments_matched += 1
        current_points = assignment.get("points_possible")
        current_submission_types = _normalize_submission_types(
            assignment.get("submission_types")
        )
        changes: dict[str, Any] = {}
        if points_possible is not None and current_points != points_possible:
            changes["points_possible"] = {
                "before": current_points,
                "after": points_possible,
            }
        if (
            target_submission_types is not None
            and current_submission_types != target_submission_types
        ):
            changes["submission_types"] = {
                "before": current_submission_types,
                "after": target_submission_types,
            }
        if not changes:
            continue
        updated = False
        if not dry_run:
            update_course_assignment(
                base_url=base_url,
                course_id=course_id,
                assignment_id=assignment_id,
                token=token,
                points_possible=points_possible,
                submission_types=target_submission_types,
            )
            updated = True
            assignments_updated += 1
        matches.append(
            {
                "title": title,
                "assignment_id": assignment_id,
                "changes": changes,
                "updated": updated,
            }
        )

    report = {
        "operation": "assignment_settings_update",
        "parameters": {
            "title_pattern": title_pattern,
            "match_mode": match_mode,
            "case_sensitive": case_sensitive,
            "points_possible": points_possible,
            "submission_preset": submission_preset,
            "dry_run": dry_run,
        },
        "summary": {
            "dry_run": dry_run,
            "assignments_scanned": len(assignments),
            "assignments_matched": assignments_matched,
            "assignments_needing_changes": len(matches),
            "assignments_updated": assignments_updated,
        },
        "matches": matches,
    }
    return _write_report(
        report=report,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        markdown_text=_markdown_for_assignment_settings(report),
    )


def bulk_set_publish_state(
    *,
    base_url: str,
    course_id: str,
    token: str,
    title_pattern: str,
    match_mode: str,
    case_sensitive: bool,
    include_pages: bool,
    include_assignments: bool,
    include_discussions: bool,
    publish: bool,
    dry_run: bool,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    items_scanned = 0
    items_matched = 0
    items_updated = 0

    if include_pages:
        pages = fetch_course_pages(base_url=base_url, course_id=course_id, token=token)
        items_scanned += len(pages)
        for page in pages:
            title = str(page.get("title") or "").strip()
            page_url = str(page.get("url") or "").strip()
            if not title or not page_url:
                continue
            if not _title_matches(
                title,
                title_pattern,
                match_mode=match_mode,
                case_sensitive=case_sensitive,
            ):
                continue
            items_matched += 1
            published_before = bool(page.get("published", False))
            if published_before == publish:
                continue
            updated = False
            if not dry_run:
                update_course_page(
                    base_url=base_url,
                    course_id=course_id,
                    page_url=page_url,
                    token=token,
                    published=publish,
                )
                updated = True
                items_updated += 1
            matches.append(
                {
                    "kind": "page",
                    "title": title,
                    "identifier": page_url,
                    "published_before": published_before,
                    "published_target": publish,
                    "updated": updated,
                }
            )

    if include_assignments:
        assignments = fetch_course_assignments(
            base_url=base_url, course_id=course_id, token=token
        )
        items_scanned += len(assignments)
        for assignment in assignments:
            title = str(assignment.get("name") or "").strip()
            assignment_id = assignment.get("id")
            if not title or assignment_id in (None, ""):
                continue
            if not _title_matches(
                title,
                title_pattern,
                match_mode=match_mode,
                case_sensitive=case_sensitive,
            ):
                continue
            items_matched += 1
            published_before = bool(assignment.get("published", False))
            if published_before == publish:
                continue
            updated = False
            if not dry_run:
                update_course_assignment(
                    base_url=base_url,
                    course_id=course_id,
                    assignment_id=assignment_id,
                    token=token,
                    published=publish,
                )
                updated = True
                items_updated += 1
            matches.append(
                {
                    "kind": "assignment",
                    "title": title,
                    "identifier": assignment_id,
                    "published_before": published_before,
                    "published_target": publish,
                    "updated": updated,
                }
            )

    if include_discussions:
        discussions = fetch_course_discussion_topics(
            base_url=base_url, course_id=course_id, token=token
        )
        items_scanned += len(discussions)
        for discussion in discussions:
            title = str(discussion.get("title") or "").strip()
            discussion_id = discussion.get("id")
            if not title or discussion_id in (None, ""):
                continue
            if not _title_matches(
                title,
                title_pattern,
                match_mode=match_mode,
                case_sensitive=case_sensitive,
            ):
                continue
            items_matched += 1
            published_before = bool(discussion.get("published", False))
            if published_before == publish:
                continue
            updated = False
            if not dry_run:
                update_discussion_topic(
                    base_url=base_url,
                    course_id=course_id,
                    topic_id=discussion_id,
                    token=token,
                    published=publish,
                )
                updated = True
                items_updated += 1
            matches.append(
                {
                    "kind": "discussion",
                    "title": title,
                    "identifier": discussion_id,
                    "published_before": published_before,
                    "published_target": publish,
                    "updated": updated,
                }
            )

    report = {
        "operation": "publish_state_update",
        "parameters": {
            "title_pattern": title_pattern,
            "match_mode": match_mode,
            "case_sensitive": case_sensitive,
            "include_pages": include_pages,
            "include_assignments": include_assignments,
            "include_discussions": include_discussions,
            "publish": publish,
            "dry_run": dry_run,
        },
        "summary": {
            "dry_run": dry_run,
            "items_scanned": items_scanned,
            "items_matched": items_matched,
            "items_needing_changes": len(matches),
            "items_updated": items_updated,
        },
        "matches": matches,
    }
    return _write_report(
        report=report,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        markdown_text=_markdown_for_publish_state(report),
    )


__all__ = [
    "bulk_replace_page_text",
    "bulk_set_publish_state",
    "bulk_update_assignment_settings",
    "_parse_points_possible",
    "_resolve_submission_preset",
]

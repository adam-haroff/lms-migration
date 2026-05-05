from __future__ import annotations

import json
import re
import csv
from pathlib import Path
from typing import Any

from .canvas_api import (
    create_course_module,
    create_course_module_item,
    create_or_update_course_page,
    fetch_course_assignment,
    fetch_course_assignments,
    fetch_course_discussion_topic,
    fetch_course_discussion_topics,
    fetch_course_modules,
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

_SCAFFOLD_PAGE_KINDS = {"plain", "intro_checklist"}


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


def _slug_from_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value}")


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text)


def _split_checklist_items(value: str | None) -> list[str]:
    if value is None:
        return []
    parts = [item.strip() for item in str(value).split("||")]
    return [item for item in parts if item]


def _build_scaffold_page_html(
    *,
    page_kind: str,
    introduction_html: str,
    checklist_items: list[str],
    page_body_html: str,
) -> str:
    normalized_kind = page_kind.strip().lower() or "plain"
    if normalized_kind not in _SCAFFOLD_PAGE_KINDS:
        raise ValueError(f"Unsupported page kind: {page_kind}")
    if page_body_html.strip():
        return page_body_html.strip()
    if normalized_kind == "plain":
        return introduction_html.strip() or "<p>Content coming soon.</p>"
    intro_block = introduction_html.strip() or (
        "<p>Review the materials for this module and complete the checklist below.</p>"
    )
    checklist_markup = "".join(f"<li>{item}</li>" for item in checklist_items)
    if not checklist_markup:
        checklist_markup = "<li>Review the module materials.</li>"
    return (
        f"{intro_block}\n"
        "<h2>Module Checklist</h2>\n"
        f"<ol>\n{checklist_markup}\n</ol>"
    )


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


def _markdown_for_module_scaffold(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Canvas Operations Report",
        "",
        "## Module Scaffold From CSV",
        "",
        f"- Dry run: `{summary.get('dry_run', True)}`",
        f"- Rows processed: `{summary.get('rows_processed', 0)}`",
        f"- Modules created: `{summary.get('modules_created', 0)}`",
        f"- Modules reused: `{summary.get('modules_reused', 0)}`",
        f"- Pages created or updated: `{summary.get('pages_written', 0)}`",
        f"- Module items created: `{summary.get('module_items_created', 0)}`",
        "",
        "## Rows",
        "",
    ]
    rows = report.get("rows", [])
    if not rows:
        lines.append("- No scaffold rows were processed.")
        return "\n".join(lines)
    for item in rows:
        lines.append(
            f"- row `{item.get('row_number', 0)}`"
            f" module=`{item.get('module_name', '')}`"
            f" module_created=`{item.get('module_created', False)}`"
            f" page_title=`{item.get('page_title', '')}`"
            f" page_written=`{item.get('page_written', False)}`"
            f" module_item_created=`{item.get('module_item_created', False)}`"
        )
    return "\n".join(lines)


def _markdown_for_description_replace(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Canvas Operations Report",
        "",
        "## Description Text Replace",
        "",
        f"- Dry run: `{summary.get('dry_run', True)}`",
        f"- Assignments scanned: `{summary.get('assignments_scanned', 0)}`",
        f"- Discussions scanned: `{summary.get('discussions_scanned', 0)}`",
        f"- Items matched: `{summary.get('items_matched', 0)}`",
        f"- Items with replacements: `{summary.get('items_with_replacements', 0)}`",
        f"- Items updated: `{summary.get('items_updated', 0)}`",
        f"- Total replacements: `{summary.get('total_replacements', 0)}`",
        "",
        "## Matches",
        "",
    ]
    matches = report.get("matches", [])
    if not matches:
        lines.append("- No matching descriptions needed changes.")
        return "\n".join(lines)
    for item in matches:
        lines.append(
            f"- `{item.get('kind', '')}` `{item.get('title', '')}`"
            f" replacements=`{item.get('replacement_count', 0)}`"
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


def bulk_replace_description_text(
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
    include_assignments: bool,
    include_discussions: bool,
    dry_run: bool,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    assignments_scanned = 0
    discussions_scanned = 0
    items_matched = 0
    items_updated = 0
    total_replacements = 0

    if include_assignments:
        assignments = fetch_course_assignments(
            base_url=base_url, course_id=course_id, token=token
        )
        assignments_scanned = len(assignments)
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
            assignment_payload = fetch_course_assignment(
                base_url=base_url,
                course_id=course_id,
                assignment_id=assignment_id,
                token=token,
            )
            description_html = str(assignment_payload.get("description") or "")
            updated_body, replacement_count = _replace_text(
                description_html,
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
                update_course_assignment(
                    base_url=base_url,
                    course_id=course_id,
                    assignment_id=assignment_id,
                    token=token,
                    description_html=updated_body,
                )
                updated = True
                items_updated += 1
            matches.append(
                {
                    "kind": "assignment",
                    "title": title,
                    "identifier": assignment_id,
                    "replacement_count": replacement_count,
                    "updated": updated,
                }
            )

    if include_discussions:
        discussions = fetch_course_discussion_topics(
            base_url=base_url, course_id=course_id, token=token
        )
        discussions_scanned = len(discussions)
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
            discussion_payload = fetch_course_discussion_topic(
                base_url=base_url,
                course_id=course_id,
                topic_id=discussion_id,
                token=token,
            )
            message_html = str(discussion_payload.get("message") or "")
            updated_body, replacement_count = _replace_text(
                message_html,
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
                update_discussion_topic(
                    base_url=base_url,
                    course_id=course_id,
                    topic_id=discussion_id,
                    token=token,
                    message_html=updated_body,
                )
                updated = True
                items_updated += 1
            matches.append(
                {
                    "kind": "discussion",
                    "title": title,
                    "identifier": discussion_id,
                    "replacement_count": replacement_count,
                    "updated": updated,
                }
            )

    report = {
        "operation": "description_text_replace",
        "parameters": {
            "title_pattern": title_pattern,
            "match_mode": match_mode,
            "case_sensitive": case_sensitive,
            "find_text": find_text,
            "replace_text": replace_text,
            "regex": regex,
            "include_assignments": include_assignments,
            "include_discussions": include_discussions,
            "dry_run": dry_run,
        },
        "summary": {
            "dry_run": dry_run,
            "assignments_scanned": assignments_scanned,
            "discussions_scanned": discussions_scanned,
            "items_matched": items_matched,
            "items_with_replacements": len(matches),
            "items_updated": items_updated,
            "total_replacements": total_replacements,
        },
        "matches": matches,
    }
    return _write_report(
        report=report,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        markdown_text=_markdown_for_description_replace(report),
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


def scaffold_modules_from_csv(
    *,
    base_url: str,
    course_id: str,
    token: str,
    csv_path: Path,
    dry_run: bool,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    modules = fetch_course_modules(base_url=base_url, course_id=course_id, token=token)
    existing_modules_by_name: dict[str, dict[str, Any]] = {}
    existing_module_page_keys: set[tuple[int, str]] = set()
    for module in modules:
        module_name = str(module.get("name") or "").strip()
        module_id = module.get("id")
        if module_name and module_name not in existing_modules_by_name:
            existing_modules_by_name[module_name] = module
        if module_id in (None, ""):
            continue
        module_id_int = int(module_id)
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip() != "Page":
                continue
            title = str(item.get("title") or "").strip()
            page_url = str(item.get("page_url") or "").strip()
            if title:
                existing_module_page_keys.add((module_id_int, title.lower()))
            if page_url:
                existing_module_page_keys.add((module_id_int, page_url.lower()))

    rows_report: list[dict[str, Any]] = []
    modules_created = 0
    modules_reused = 0
    pages_written = 0
    module_items_created = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Scaffold CSV must include a header row.")
        for row_number, raw_row in enumerate(reader, start=2):
            module_name = str(raw_row.get("module_name") or "").strip()
            if not module_name:
                continue
            module_position = _parse_optional_int(raw_row.get("module_position"))
            module_published = _parse_bool(raw_row.get("module_published"), False)
            page_title = str(raw_row.get("page_title") or "").strip()
            page_kind = str(raw_row.get("page_kind") or "plain").strip() or "plain"
            page_published = _parse_bool(raw_row.get("page_published"), False)
            page_body_html = str(raw_row.get("page_body_html") or "")
            introduction_html = str(raw_row.get("introduction_html") or "")
            checklist_items = _split_checklist_items(raw_row.get("checklist_items"))
            item_indent = _parse_optional_int(raw_row.get("item_indent"))
            page_url = _slug_from_title(page_title) if page_title else ""

            module = existing_modules_by_name.get(module_name)
            module_created = False
            if module is None:
                if dry_run:
                    module = {
                        "id": -1000 - row_number,
                        "name": module_name,
                        "published": module_published,
                    }
                else:
                    module = create_course_module(
                        base_url=base_url,
                        course_id=course_id,
                        token=token,
                        name=module_name,
                        position=module_position,
                        published=module_published,
                    )
                existing_modules_by_name[module_name] = module
                module_created = True
                modules_created += 1
            else:
                modules_reused += 1

            page_written = False
            module_item_created = False
            module_id_int = int(module.get("id"))
            if page_title:
                page_html = _build_scaffold_page_html(
                    page_kind=page_kind,
                    introduction_html=introduction_html,
                    checklist_items=checklist_items,
                    page_body_html=page_body_html,
                )
                if not dry_run:
                    create_or_update_course_page(
                        base_url=base_url,
                        course_id=course_id,
                        title=page_title,
                        body_html=page_html,
                        token=token,
                        published=page_published,
                    )
                page_written = True
                pages_written += 1
                page_keys = {(module_id_int, page_title.lower()), (module_id_int, page_url.lower())}
                already_in_module = any(key in existing_module_page_keys for key in page_keys)
                if not already_in_module:
                    if not dry_run:
                        create_course_module_item(
                            base_url=base_url,
                            course_id=course_id,
                            module_id=module_id_int,
                            token=token,
                            item_type="Page",
                            title=page_title,
                            page_url=page_url,
                            indent=item_indent,
                        )
                    module_item_created = True
                    module_items_created += 1
                    existing_module_page_keys.update(page_keys)

            rows_report.append(
                {
                    "row_number": row_number,
                    "module_name": module_name,
                    "module_created": module_created,
                    "page_title": page_title,
                    "page_kind": page_kind,
                    "page_written": page_written,
                    "module_item_created": module_item_created,
                }
            )

    report = {
        "operation": "module_scaffold_from_csv",
        "parameters": {
            "csv_path": str(csv_path),
            "dry_run": dry_run,
        },
        "summary": {
            "dry_run": dry_run,
            "rows_processed": len(rows_report),
            "modules_created": modules_created,
            "modules_reused": modules_reused,
            "pages_written": pages_written,
            "module_items_created": module_items_created,
        },
        "rows": rows_report,
    }
    return _write_report(
        report=report,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        markdown_text=_markdown_for_module_scaffold(report),
    )


__all__ = [
    "bulk_replace_description_text",
    "bulk_replace_page_text",
    "bulk_set_publish_state",
    "bulk_update_assignment_settings",
    "_parse_points_possible",
    "_resolve_submission_preset",
    "scaffold_modules_from_csv",
]

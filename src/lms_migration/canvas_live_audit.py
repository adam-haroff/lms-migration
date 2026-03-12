from __future__ import annotations

import argparse
import csv
import json
import posixpath
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from .canvas_api import (
    fetch_course_announcements,
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_files,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
    update_course_page_body,
)
from .canvas_post_import import _build_file_index, _load_alias_map, _rewrite_page_body


_LEGACY_D2L_RE = re.compile(r"^/?(?:d2l/|content/enforced/)", flags=re.IGNORECASE)
_SHARED_TEMPLATE_RE = re.compile(r"^/?shared/brightspace_html_template/", flags=re.IGNORECASE)


def _extract_attr_refs(html_text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?:^|\s)(?P<attr>href|src)\s*=\s*(?P<quote>[\"'])(?P<url>[^\"']+)(?P=quote)",
        flags=re.IGNORECASE,
    )
    refs: list[tuple[str, str]] = []
    for match in pattern.finditer(html_text):
        refs.append((str(match.group("attr")).lower(), str(match.group("url")).strip()))
    return refs


def _is_local_candidate(url: str) -> bool:
    value = url.strip()
    if not value:
        return False
    if value.startswith(("#", "/", "mailto:", "tel:", "javascript:", "data:")):
        return False
    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//"):
        return False
    return True


def _local_ref_suggestion(
    *,
    attr: str,
    ref_url: str,
    file_index: dict[str, list],
    alias_map: dict[str, tuple[str, ...]],
    course_id: str,
) -> tuple[str, str, bool]:
    parsed = urlparse(ref_url)
    path_text = unquote(parsed.path).strip().replace("\\", "/")
    basename = posixpath.basename(path_text).strip().lower()
    if not basename:
        return "", "no_basename", False

    direct = file_index.get(basename, [])
    if len(direct) == 1:
        file_ref = direct[0]
        if attr == "src":
            return f"/courses/{course_id}/files/{file_ref.file_id}/preview", "direct_match", False
        return f"/courses/{course_id}/files/{file_ref.file_id}/download?wrap=1", "direct_match", False
    if len(direct) > 1:
        return "", "collision", False

    for alias_name in alias_map.get(basename, ()):
        alias_matches = file_index.get(alias_name, [])
        if len(alias_matches) != 1:
            continue
        file_ref = alias_matches[0]
        if attr == "src":
            return f"/courses/{course_id}/files/{file_ref.file_id}/preview", f"alias_match:{alias_name}", True
        return f"/courses/{course_id}/files/{file_ref.file_id}/download?wrap=1", f"alias_match:{alias_name}", True

    return "", "no_match", False


def _audit_html(
    *,
    html_text: str,
    content_type: str,
    content_id: str,
    content_label: str,
    content_url: str,
    file_index: dict[str, list],
    alias_map: dict[str, tuple[str, ...]],
    course_id: str,
) -> list[dict]:
    findings: list[dict] = []

    for match in re.finditer(
        r"<a\b[^>]*data-migration-link-status\s*=\s*([\"'])needs-review\1[^>]*>",
        html_text,
        flags=re.IGNORECASE,
    ):
        tag = match.group(0)
        original_href_match = re.search(
            r'data-migration-original-href\s*=\s*([\"\'])(?P<value>[^\"\']+)\1',
            tag,
            flags=re.IGNORECASE,
        )
        findings.append(
            {
                "content_type": content_type,
                "content_id": content_id,
                "content_label": content_label,
                "content_url": content_url,
                "field": "html",
                "issue_type": "neutralized_migration_link",
                "severity": "warning",
                "ref": original_href_match.group("value").strip() if original_href_match else "",
                "suggested_target": "",
                "note": "Link is marked needs-review and should be relinked in Canvas.",
            }
        )

    for attr, ref_url in _extract_attr_refs(html_text):
        ref_value = ref_url.strip()
        if not ref_value:
            continue

        if _LEGACY_D2L_RE.match(ref_value):
            findings.append(
                {
                    "content_type": content_type,
                    "content_id": content_id,
                    "content_label": content_label,
                    "content_url": content_url,
                    "field": attr,
                    "issue_type": "legacy_d2l_link",
                    "severity": "warning",
                    "ref": ref_value,
                    "suggested_target": "",
                    "note": "Legacy D2L link should be replaced with Canvas destination.",
                }
            )
            continue

        if _SHARED_TEMPLATE_RE.match(ref_value):
            findings.append(
                {
                    "content_type": content_type,
                    "content_id": content_id,
                    "content_label": content_label,
                    "content_url": content_url,
                    "field": attr,
                    "issue_type": "brightspace_template_link",
                    "severity": "warning",
                    "ref": ref_value,
                    "suggested_target": "",
                    "note": "Brightspace shared-template link usually breaks in Canvas.",
                }
            )
            continue

        if not _is_local_candidate(ref_value):
            continue

        suggestion, reason, via_alias = _local_ref_suggestion(
            attr=attr,
            ref_url=ref_value,
            file_index=file_index,
            alias_map=alias_map,
            course_id=course_id,
        )
        issue_type = "relative_local_reference"
        severity = "info"
        note = "Relative local reference should be converted to Canvas file link for reliability."
        if reason == "collision":
            issue_type = "relative_local_reference_collision"
            severity = "warning"
            note = "Multiple course files share this basename; cannot auto-select a unique file."
        elif reason == "no_match":
            issue_type = "relative_local_reference_unresolved"
            severity = "warning"
            note = "No matching Canvas file basename found."
        elif via_alias:
            issue_type = "relative_local_reference_alias_match"
            note = "Resolved via alias map; safe-fix can convert this link."

        findings.append(
            {
                "content_type": content_type,
                "content_id": content_id,
                "content_label": content_label,
                "content_url": content_url,
                "field": attr,
                "issue_type": issue_type,
                "severity": severity,
                "ref": ref_value,
                "suggested_target": suggestion,
                "note": note,
            }
        )

    return findings


def run_live_link_audit(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    output_markdown_path: Path | None = None,
    output_csv_path: Path | None = None,
    apply_safe_fixes: bool = False,
    alias_map_json_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    normalized_base = normalize_base_url(base_url)
    files = fetch_course_files(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    file_index, collisions = _build_file_index(files)
    alias_map, alias_source = _load_alias_map(alias_map_json_path)

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
    announcements = fetch_course_announcements(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    findings: list[dict] = []
    pages_updated = 0
    total_rewrites = 0
    total_alias_rewrites = 0
    total_unresolved = 0
    alias_keys_used: set[str] = set()

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
        page_body = page.get("body", "")
        if not isinstance(page_body, str):
            page_body = str(page_body or "")

        final_body = page_body
        if apply_safe_fixes:
            updated_body, rewrites, unresolved, alias_rewrites, page_alias_keys = _rewrite_page_body(
                body_html=page_body,
                file_index=file_index,
                course_id=course_id,
                alias_map=alias_map,
            )
            if rewrites and updated_body != page_body:
                update_course_page_body(
                    base_url=normalized_base,
                    course_id=course_id,
                    page_url=page_url,
                    body_html=updated_body,
                    token=token,
                )
                pages_updated += 1
                final_body = updated_body
            total_rewrites += rewrites
            total_unresolved += unresolved
            total_alias_rewrites += alias_rewrites
            alias_keys_used.update(page_alias_keys)

        page_id = str(page.get("page_id") or page_summary.get("page_id") or "")
        title = str(page.get("title") or page_summary.get("title") or page_url)
        findings.extend(
            _audit_html(
                html_text=final_body,
                content_type="page",
                content_id=page_id,
                content_label=title,
                content_url=f"/courses/{course_id}/pages/{page_url}",
                file_index=file_index,
                alias_map=alias_map,
                course_id=course_id,
            )
        )

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        html_text = assignment.get("description", "")
        if not isinstance(html_text, str) or not html_text.strip():
            continue
        findings.extend(
            _audit_html(
                html_text=html_text,
                content_type="assignment",
                content_id=str(assignment.get("id", "")),
                content_label=str(assignment.get("name", "")),
                content_url=f"/courses/{course_id}/assignments/{assignment.get('id', '')}",
                file_index=file_index,
                alias_map=alias_map,
                course_id=course_id,
            )
        )

    for discussion in discussions:
        if not isinstance(discussion, dict):
            continue
        html_text = discussion.get("message", "")
        if not isinstance(html_text, str) or not html_text.strip():
            continue
        findings.extend(
            _audit_html(
                html_text=html_text,
                content_type="discussion",
                content_id=str(discussion.get("id", "")),
                content_label=str(discussion.get("title", "")),
                content_url=f"/courses/{course_id}/discussion_topics/{discussion.get('id', '')}",
                file_index=file_index,
                alias_map=alias_map,
                course_id=course_id,
            )
        )

    for announcement in announcements:
        if not isinstance(announcement, dict):
            continue
        html_text = announcement.get("message", "")
        if not isinstance(html_text, str) or not html_text.strip():
            continue
        findings.extend(
            _audit_html(
                html_text=html_text,
                content_type="announcement",
                content_id=str(announcement.get("id", "")),
                content_label=str(announcement.get("title", "")),
                content_url=f"/courses/{course_id}/announcements/{announcement.get('id', '')}",
                file_index=file_index,
                alias_map=alias_map,
                course_id=course_id,
            )
        )

    issue_counter = Counter(item.get("issue_type", "") for item in findings)
    content_counter = Counter(item.get("content_type", "") for item in findings)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": normalized_base,
        "course_id": str(course_id),
        "apply_safe_fixes": bool(apply_safe_fixes),
        "alias_map_json": alias_source,
        "alias_map_rules_loaded": len(alias_map),
        "file_name_collisions": collisions,
        "counts": {
            "files": len(files),
            "pages": len(page_summaries),
            "assignments": len(assignments),
            "discussions": len(discussions),
            "announcements": len(announcements),
            "findings_total": len(findings),
        },
        "safe_fix_summary": {
            "pages_updated": pages_updated,
            "total_rewrites": total_rewrites,
            "total_alias_rewrites": total_alias_rewrites,
            "total_unresolved_local_refs": total_unresolved,
            "alias_keys_used": sorted(alias_keys_used),
        },
        "finding_counts_by_issue_type": dict(issue_counter),
        "finding_counts_by_content_type": dict(content_counter),
        "findings": findings,
    }

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = output_markdown_path or output_json_path.with_suffix(".md")
    lines = [
        "# Canvas Live Link Audit",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Inputs",
        "",
        f"- Base URL: `{normalized_base}`",
        f"- Course ID: `{course_id}`",
        f"- Apply safe fixes: `{apply_safe_fixes}`",
        f"- Alias map: `{alias_source or 'none'}`",
        "",
        "## Counts",
        "",
        f"- Findings total: {report['counts']['findings_total']}",
        f"- Pages: {report['counts']['pages']}",
        f"- Assignments: {report['counts']['assignments']}",
        f"- Discussions: {report['counts']['discussions']}",
        f"- Announcements: {report['counts']['announcements']}",
        "",
        "## Safe Fix Summary",
        "",
        f"- Pages updated: {report['safe_fix_summary']['pages_updated']}",
        f"- Total rewrites: {report['safe_fix_summary']['total_rewrites']}",
        f"- Alias rewrites: {report['safe_fix_summary']['total_alias_rewrites']}",
        f"- Unresolved local refs: {report['safe_fix_summary']['total_unresolved_local_refs']}",
        "",
        "## Findings By Issue Type",
        "",
    ]
    for issue_type, count in issue_counter.most_common():
        lines.append(f"- {issue_type}: {count}")
    if not issue_counter:
        lines.append("- none")
    lines.append("")

    lines.extend(
        [
            "## Findings By Content Type",
            "",
        ]
    )
    for content_type, count in content_counter.most_common():
        lines.append(f"- {content_type}: {count}")
    if not content_counter:
        lines.append("- none")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    csv_path = output_csv_path or output_json_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "content_type",
                "content_id",
                "content_label",
                "content_url",
                "field",
                "issue_type",
                "severity",
                "ref",
                "suggested_target",
                "note",
            ],
        )
        writer.writeheader()
        for row in findings:
            writer.writerow(row)

    return output_json_path, md_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-live-audit",
        description="Audit live Canvas course content for link/template migration issues.",
    )
    parser.add_argument("--base-url", required=True, type=str, help="Canvas base URL")
    parser.add_argument("--course-id", required=True, type=str, help="Canvas course ID")
    parser.add_argument("--token", required=True, type=str, help="Canvas API token")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("output/canvas-live-link-audit.json"),
        help="Path to write audit JSON report.",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional explicit markdown output path.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Optional explicit CSV output path.",
    )
    parser.add_argument(
        "--apply-safe-fixes",
        action="store_true",
        help="Apply deterministic page-body link rewrites before generating findings.",
    )
    parser.add_argument(
        "--alias-map-json",
        type=Path,
        default=None,
        help="Optional alias map JSON for legacy template asset names.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_json, output_md, output_csv = run_live_link_audit(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        output_csv_path=args.output_csv,
        apply_safe_fixes=bool(args.apply_safe_fixes),
        alias_map_json_path=args.alias_map_json,
    )
    print(f"Live audit JSON: {output_json}")
    print(f"Live audit Markdown: {output_md}")
    print(f"Live audit CSV: {output_csv}")


if __name__ == "__main__":
    main()

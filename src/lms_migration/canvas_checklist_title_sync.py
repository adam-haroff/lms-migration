from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .canvas_api import (
    fetch_course_modules,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
    update_course_page_body,
)
from .canvas_content_reference_sync import (
    _build_module_contexts,
    _rewrite_anchor_labels,
    _rewrite_textual_references,
)


def _normalize_title(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _is_intro_checklist_title(title: str) -> bool:
    normalized = _normalize_title(title)
    return "introduction and checklist" in normalized


def sync_intro_checklist_titles(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_base = normalize_base_url(base_url)
    modules = fetch_course_modules(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    page_summaries = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    page_to_module, course_candidates = _build_module_contexts(modules)
    discussion_title_by_id: dict[str, str] = {}
    assignment_title_by_id: dict[str, str] = {}
    page_title_by_slug: dict[str, str] = {}

    for module in modules:
        if not isinstance(module, dict):
            continue
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            title = str(item.get("title") or "").strip()
            content_id = str(item.get("content_id") or "").strip()
            if item_type == "Discussion" and content_id and title:
                discussion_title_by_id[content_id] = title
            if item_type == "Assignment" and content_id and title:
                assignment_title_by_id[content_id] = title
            if item_type == "Page":
                page_url = str(item.get("page_url") or "").strip()
                if page_url and title:
                    page_title_by_slug[page_url] = title

    pages_scanned = 0
    pages_updated = 0
    total_anchor_rewrites = 0
    total_text_rewrites = 0
    page_results: list[dict[str, Any]] = []

    for summary in page_summaries:
        if not isinstance(summary, dict):
            continue
        page_url = str(summary.get("url") or "").strip()
        title = str(summary.get("title") or "").strip()
        if not page_url or not _is_intro_checklist_title(title):
            continue
        module_context = page_to_module.get(page_url)
        if module_context is None:
            continue

        pages_scanned += 1
        page = fetch_course_page(
            base_url=normalized_base,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        body_html = str(page.get("body") or "")
        updated = body_html

        updated, anchor_rewrites = _rewrite_anchor_labels(
            updated,
            discussion_title_by_id=discussion_title_by_id,
            assignment_title_by_id=assignment_title_by_id,
            page_title_by_slug=page_title_by_slug,
        )
        updated, text_rewrites = _rewrite_textual_references(
            updated,
            module_context=module_context,
            course_candidates=course_candidates,
        )

        changed = updated != body_html
        if changed and not dry_run:
            update_course_page_body(
                base_url=normalized_base,
                course_id=course_id,
                page_url=page_url,
                body_html=updated,
                token=token,
            )
        if changed:
            pages_updated += 1

        total_anchor_rewrites += anchor_rewrites
        total_text_rewrites += text_rewrites

        if changed or anchor_rewrites or text_rewrites:
            page_results.append(
                {
                    "page_id": str(page.get("page_id") or summary.get("page_id") or ""),
                    "page_url": page_url,
                    "title": title,
                    "module": module_context.name,
                    "anchor_label_rewrites": anchor_rewrites,
                    "text_reference_rewrites": text_rewrites,
                    "updated": changed,
                }
            )

    report = {
        "base_url": normalized_base,
        "course_id": course_id,
        "dry_run": dry_run,
        "summary": {
            "intro_checklist_pages_scanned": pages_scanned,
            "pages_updated": pages_updated,
            "anchor_label_rewrites": total_anchor_rewrites,
            "text_reference_rewrites": total_text_rewrites,
        },
        "page_results": page_results,
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite Introduction and Checklist page references so checklist items "
            "match the final live Canvas module item names."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = sync_intro_checklist_titles(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json_path=args.output_json,
        dry_run=args.dry_run,
    )
    print(json.dumps(report["summary"], indent=2))
    print(args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

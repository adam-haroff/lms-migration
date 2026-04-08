from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .canvas_api import (
    fetch_course,
    fetch_course_announcements,
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_files,
    fetch_course_folders,
    fetch_course_modules,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
)
from .canvas_post_import import _build_file_index, _build_folder_path_index


_PAGE_LINK_PATTERN = re.compile(r"/pages/(?P<slug>[^\"'#?/\s>]+)", flags=re.IGNORECASE)
_FILE_LINK_PATTERN = re.compile(r"/files/(?P<file_id>\d+)(?:[/?\"'#]|$)", flags=re.IGNORECASE)


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _extract_page_links(html_text: str) -> set[str]:
    return {match.group("slug").strip() for match in _PAGE_LINK_PATTERN.finditer(html_text or "")}


def _extract_file_links(html_text: str) -> set[str]:
    return {match.group("file_id").strip() for match in _FILE_LINK_PATTERN.finditer(html_text or "")}


def _module_item_page_url(item: dict[str, Any]) -> str:
    page_url = str(item.get("page_url") or "").strip()
    if page_url:
        return page_url
    html_url = str(item.get("html_url") or "").strip()
    match = re.search(r"/pages/([^/?#]+)", html_url)
    return match.group(1).strip() if match else ""


def _module_item_file_id(item: dict[str, Any]) -> str:
    content_id = str(item.get("content_id") or "").strip()
    if content_id:
        return content_id
    html_url = str(item.get("html_url") or "").strip()
    match = re.search(r"/files/(\d+)", html_url)
    return match.group(1).strip() if match else ""


def audit_course_cleanup_data(
    *,
    course: dict[str, Any] | None = None,
    pages: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    discussions: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    files: list[dict[str, Any]],
    folders: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_course = course if isinstance(course, dict) else {}
    folder_paths = _build_folder_path_index(folders)
    file_index, _collisions = _build_file_index(files, folder_paths=folder_paths)

    module_name_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    module_page_urls: set[str] = set()
    module_file_ids: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name") or "").strip()
        if name:
            module_name_groups[_normalize_title(name)].append(
                {
                    "id": module.get("id"),
                    "name": name,
                    "published": bool(module.get("published", False)),
                }
            )
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() == "page":
                page_url = _module_item_page_url(item)
                if page_url:
                    module_page_urls.add(page_url)
            if str(item.get("type") or "").strip().lower() == "file":
                file_id = _module_item_file_id(item)
                if file_id:
                    module_file_ids.add(file_id)

    duplicate_modules = [
        {"name": group[0]["name"], "count": len(group), "modules": group}
        for group in module_name_groups.values()
        if len(group) > 1
    ]

    linked_page_urls: set[str] = set()
    linked_file_ids: set[str] = set(module_file_ids)
    for page in pages:
        linked_page_urls.update(_extract_page_links(str(page.get("body") or "")))
        linked_file_ids.update(_extract_file_links(str(page.get("body") or "")))
    for assignment in assignments:
        linked_page_urls.update(
            _extract_page_links(str(assignment.get("description") or ""))
        )
        linked_file_ids.update(
            _extract_file_links(str(assignment.get("description") or ""))
        )
    for discussion in discussions:
        linked_page_urls.update(_extract_page_links(str(discussion.get("message") or "")))
        linked_file_ids.update(_extract_file_links(str(discussion.get("message") or "")))
    for announcement in announcements:
        body = str(announcement.get("message") or announcement.get("message_html") or "")
        linked_page_urls.update(_extract_page_links(body))
        linked_file_ids.update(_extract_file_links(body))
    linked_page_urls.update(_extract_page_links(str(normalized_course.get("syllabus_body") or "")))
    linked_file_ids.update(_extract_file_links(str(normalized_course.get("syllabus_body") or "")))

    page_title_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    duplicate_page_urls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    published_unlinked_pages: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "").strip()
        page_url = str(page.get("url") or page.get("page_url") or "").strip()
        page_record = {
            "page_id": page.get("page_id") or page.get("id"),
            "title": title,
            "url": page_url,
            "published": bool(page.get("published", False)),
            "front_page": bool(page.get("front_page", False)),
        }
        if title:
            page_title_groups[_normalize_title(title)].append(page_record)
        if page_url:
            duplicate_page_urls[page_url].append(page_record)
        if (
            page_record["published"]
            and not page_record["front_page"]
            and page_url
            and page_url not in module_page_urls
            and page_url not in linked_page_urls
        ):
            published_unlinked_pages.append(page_record)

    duplicate_page_titles = [
        {"title": group[0]["title"], "count": len(group), "pages": group}
        for group in page_title_groups.values()
        if len(group) > 1
    ]
    duplicate_page_slugs = [
        {"url": url, "count": len(group), "pages": group}
        for url, group in duplicate_page_urls.items()
        if len(group) > 1
    ]

    file_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files_by_folder: dict[str, int] = defaultdict(int)
    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        name = str(file_obj.get("display_name") or file_obj.get("filename") or "").strip()
        if not name:
            continue
        basename_key = name.lower()
        folder_id = str(file_obj.get("folder_id") or "").strip()
        files_by_folder[folder_id] += 1
        file_groups[basename_key].append(
            {
                "id": str(file_obj.get("id") or "").strip(),
                "name": name,
                "folder_id": folder_id,
                "folder_path": folder_paths.get(folder_id, ""),
                "size": file_obj.get("size"),
            }
        )

    duplicate_file_basenames: list[dict[str, Any]] = []
    for basename_key, group in sorted(file_groups.items()):
        distinct_folders = {entry["folder_path"] for entry in group}
        if len(group) <= 1 or len(distinct_folders) <= 1:
            continue
        preferred_matches = file_index.get(basename_key, [])
        preferred_id = preferred_matches[0].file_id if len(preferred_matches) == 1 else ""
        duplicate_file_basenames.append(
            {
                "basename": group[0]["name"],
                "count": len(group),
                "preferred_file_id": preferred_id,
                "files": group,
            }
        )

    child_folder_counts: dict[str, int] = defaultdict(int)
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        parent_id = str(folder.get("parent_folder_id") or "").strip()
        if parent_id:
            child_folder_counts[parent_id] += 1

    empty_folders: list[dict[str, Any]] = []
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("id") or "").strip()
        full_name = str(folder.get("full_name") or folder.get("name") or "").strip()
        if not folder_id or not full_name:
            continue
        if full_name.strip().lower() == "course files":
            continue
        if files_by_folder.get(folder_id, 0) == 0 and child_folder_counts.get(folder_id, 0) == 0:
            empty_folders.append(
                {
                    "id": folder_id,
                    "name": str(folder.get("name") or "").strip(),
                    "full_name": full_name,
                }
            )

    unused_files: list[dict[str, Any]] = []
    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        file_id = str(file_obj.get("id") or "").strip()
        if not file_id or file_id in linked_file_ids:
            continue
        unused_files.append(
            {
                "id": file_id,
                "name": str(file_obj.get("display_name") or file_obj.get("filename") or "").strip(),
                "folder_path": folder_paths.get(str(file_obj.get("folder_id") or "").strip(), ""),
            }
        )

    return {
        "summary": {
            "duplicate_modules": len(duplicate_modules),
            "duplicate_page_titles": len(duplicate_page_titles),
            "duplicate_page_slugs": len(duplicate_page_slugs),
            "published_unlinked_pages": len(published_unlinked_pages),
            "duplicate_file_basenames": len(duplicate_file_basenames),
            "empty_folders": len(empty_folders),
            "unused_files": len(unused_files),
        },
        "duplicate_modules": duplicate_modules,
        "duplicate_page_titles": duplicate_page_titles,
        "duplicate_page_slugs": duplicate_page_slugs,
        "published_unlinked_pages": sorted(
            published_unlinked_pages,
            key=lambda row: (row.get("title", "").lower(), row.get("url", "").lower()),
        ),
        "duplicate_file_basenames": duplicate_file_basenames,
        "empty_folders": sorted(
            empty_folders, key=lambda row: row.get("full_name", "").lower()
        ),
        "unused_files": sorted(
            unused_files,
            key=lambda row: (row.get("folder_path", "").lower(), row.get("name", "").lower()),
        ),
    }


def run_canvas_cleanup_audit(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
) -> Path:
    normalized_base = normalize_base_url(base_url)
    course = fetch_course(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    pages = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    enriched_pages: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_url = str(page.get("url") or page.get("page_url") or "").strip()
        if not page_url:
            enriched_pages.append(page)
            continue
        if page.get("body") is None:
            full_page = fetch_course_page(
                base_url=normalized_base,
                course_id=course_id,
                page_url=page_url,
                token=token,
            )
            merged = dict(page)
            if isinstance(full_page, dict):
                merged.update(full_page)
            enriched_pages.append(merged)
        else:
            enriched_pages.append(page)

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
    files = fetch_course_files(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    folders = fetch_course_folders(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    report = {
        "base_url": normalized_base,
        "course_id": str(course_id),
        **audit_course_cleanup_data(
            course=course,
            pages=enriched_pages,
            modules=modules,
            assignments=assignments,
            discussions=discussions,
            announcements=announcements,
            files=files,
            folders=folders,
        ),
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-cleanup-audit",
        description="Audit a live Canvas course for duplicate/orphan cleanup issues.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("output/canvas-cleanup-audit.json"),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    report_path = run_canvas_cleanup_audit(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json_path=args.output_json,
    )
    print(f"Canvas cleanup audit JSON: {report_path}")


if __name__ == "__main__":
    main()

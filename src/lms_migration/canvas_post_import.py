from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .canvas_api import (
    fetch_course_files,
    fetch_course_page,
    normalize_base_url,
    update_course_page_body,
)


_LINK_ATTR_PATTERN = re.compile(
    r"(?P<prefix>\b(?P<attr>href|src)\s*=\s*)(?P<quote>[\"'])(?P<url>[^\"']+)(?P=quote)",
    flags=re.IGNORECASE,
)
_ANCHOR_TAG_PATTERN = re.compile(r"<a\b[^>]*>", flags=re.IGNORECASE)


@dataclass(frozen=True)
class _FileRef:
    file_id: str
    name: str


def _normalize_basename(value: str) -> str:
    return posixpath.basename(value.strip().replace("\\", "/")).strip().lower()


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


def _build_file_index(files: list[dict]) -> tuple[dict[str, list[_FileRef]], dict[str, int]]:
    by_basename: dict[str, list[_FileRef]] = defaultdict(list)
    for item in files:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id", "")).strip()
        if not file_id:
            continue
        name = str(item.get("display_name") or item.get("filename") or "").strip()
        if not name:
            continue
        basename = _normalize_basename(name)
        if not basename:
            continue
        by_basename[basename].append(_FileRef(file_id=file_id, name=name))

    collisions = {name: len(matches) for name, matches in by_basename.items() if len(matches) > 1}
    return dict(by_basename), collisions


def _extract_issue_pages(issues: list[dict]) -> list[str]:
    page_urls: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        description = str(issue.get("description", "")).strip().lower()
        if "missing links found in imported content - wiki page body" not in description:
            continue
        fix_url = str(issue.get("fix_issue_html_url", "")).strip()
        if not fix_url or "/pages/" not in fix_url:
            continue
        page_url = fix_url.split("/pages/", 1)[1].strip()
        if page_url:
            page_urls.add(page_url)
    return sorted(page_urls)


def _load_alias_map(alias_map_json_path: Path | None) -> tuple[dict[str, tuple[str, ...]], str]:
    if alias_map_json_path is None:
        return {}, ""
    if not alias_map_json_path.exists():
        raise ValueError(f"Alias map JSON does not exist: {alias_map_json_path}")

    payload = json.loads(alias_map_json_path.read_text(encoding="utf-8"))
    raw_mapping = payload.get("aliases") if isinstance(payload, dict) and isinstance(payload.get("aliases"), dict) else payload
    if not isinstance(raw_mapping, dict):
        raise ValueError("Alias map JSON must be an object or include an object at key 'aliases'.")

    normalized: dict[str, tuple[str, ...]] = {}
    for source_name, target_names in raw_mapping.items():
        source_basename = _normalize_basename(str(source_name))
        if not source_basename:
            continue
        if isinstance(target_names, str):
            candidates = [_normalize_basename(target_names)]
        elif isinstance(target_names, list):
            candidates = [_normalize_basename(str(item)) for item in target_names]
        else:
            continue
        cleaned = tuple(candidate for candidate in candidates if candidate)
        if cleaned:
            normalized[source_basename] = cleaned

    return normalized, str(alias_map_json_path)


def _rewrite_page_body(
    *,
    body_html: str,
    file_index: dict[str, list[_FileRef]],
    course_id: str,
    alias_map: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, int, int, int, set[str]]:
    rewrites = 0
    unresolved = 0
    alias_rewrites = 0
    alias_keys_used: set[str] = set()
    resolved_alias_map = alias_map or {}

    def resolve_basename(basename: str) -> tuple[_FileRef | None, bool]:
        direct = file_index.get(basename, [])
        if len(direct) == 1:
            return direct[0], False

        for alias_basename in resolved_alias_map.get(basename, ()):
            alias_matches = file_index.get(alias_basename, [])
            if len(alias_matches) == 1:
                alias_keys_used.add(f"{basename}->{alias_basename}")
                return alias_matches[0], True

        return None, False

    def replace_attr(match: re.Match[str]) -> str:
        nonlocal rewrites
        nonlocal unresolved
        nonlocal alias_rewrites

        attr = str(match.group("attr")).lower()
        original_url = str(match.group("url")).strip()
        if not _is_local_candidate(original_url):
            return match.group(0)

        parsed = urlparse(original_url)
        path_text = unquote(parsed.path).strip().replace("\\", "/")
        basename = _normalize_basename(path_text)
        if not basename:
            return match.group(0)

        file_ref, used_alias = resolve_basename(basename)
        if file_ref is None:
            unresolved += 1
            return match.group(0)

        if used_alias:
            alias_rewrites += 1

        file_id = file_ref.file_id
        if attr == "src":
            target_url = f"/courses/{course_id}/files/{file_id}/preview"
        else:
            target_url = f"/courses/{course_id}/files/{file_id}/download?wrap=1"

        rewrites += 1
        return f'{match.group("prefix")}"{target_url}"'

    updated = _LINK_ATTR_PATTERN.sub(replace_attr, body_html)

    # Second pass: relink previously-neutralized template links where we retained
    # data-migration-original-href.
    def replace_anchor_tag(match: re.Match[str]) -> str:
        nonlocal rewrites
        nonlocal unresolved
        nonlocal alias_rewrites

        tag = match.group(0)
        original_href_match = re.search(
            r'\bdata-migration-original-href\s*=\s*([\"\'])(?P<value>[^\"\']+)\1',
            tag,
            flags=re.IGNORECASE,
        )
        if original_href_match is None:
            return tag

        original_href = html.unescape(original_href_match.group("value").strip())
        parsed = urlparse(original_href)
        path_text = unquote(parsed.path).strip().replace("\\", "/")
        basename = _normalize_basename(path_text)
        if not basename:
            unresolved += 1
            return tag

        file_ref, used_alias = resolve_basename(basename)
        if file_ref is None:
            unresolved += 1
            return tag

        if used_alias:
            alias_rewrites += 1

        file_id = file_ref.file_id
        target_url = f"/courses/{course_id}/files/{file_id}/download?wrap=1"

        if re.search(r'\bhref\s*=\s*[\"\'][^\"\']*[\"\']', tag, flags=re.IGNORECASE):
            tag = re.sub(
                r'(\bhref\s*=\s*)([\"\'])([^\"\']*)(\2)',
                lambda m: f'{m.group(1)}"{target_url}"',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            tag = tag[:-1] + f' href="{target_url}">'

        tag = re.sub(
            r'\sdata-migration-link-status\s*=\s*([\"\'])(?:.*?)\1',
            "",
            tag,
            flags=re.IGNORECASE,
        )
        tag = re.sub(
            r'\sdata-migration-link-reason\s*=\s*([\"\'])(?:.*?)\1',
            "",
            tag,
            flags=re.IGNORECASE,
        )
        tag = re.sub(
            r'\sdata-migration-original-href\s*=\s*([\"\'])(?:.*?)\1',
            "",
            tag,
            flags=re.IGNORECASE,
        )

        rewrites += 1
        return tag

    updated = _ANCHOR_TAG_PATTERN.sub(replace_anchor_tag, updated)
    return updated, rewrites, unresolved, alias_rewrites, alias_keys_used


def auto_relink_missing_links(
    *,
    base_url: str,
    course_id: str,
    token: str,
    issues_json_path: Path,
    output_json_path: Path,
    alias_map_json_path: Path | None = None,
    dry_run: bool = False,
) -> Path:
    if not issues_json_path.exists():
        raise ValueError(f"Issues JSON does not exist: {issues_json_path}")

    payload = json.loads(issues_json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Issues JSON must be a list.")
    issues = [item for item in payload if isinstance(item, dict)]
    page_urls = _extract_issue_pages(issues)

    files = fetch_course_files(
        base_url=base_url,
        course_id=course_id,
        token=token,
    )
    file_index, collisions = _build_file_index(files)
    alias_map, alias_map_source = _load_alias_map(alias_map_json_path)

    page_results: list[dict] = []
    pages_updated = 0
    total_rewrites = 0
    total_unresolved = 0
    total_alias_rewrites = 0
    alias_keys_used: set[str] = set()

    for page_url in page_urls:
        page = fetch_course_page(
            base_url=base_url,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        body = page.get("body", "")
        if not isinstance(body, str):
            body = str(body or "")

        updated_body, rewrites, unresolved, alias_rewrites, page_alias_keys_used = _rewrite_page_body(
            body_html=body,
            file_index=file_index,
            course_id=course_id,
            alias_map=alias_map,
        )
        changed = bool(rewrites and updated_body != body)
        if changed and not dry_run:
            update_course_page_body(
                base_url=base_url,
                course_id=course_id,
                page_url=page_url,
                body_html=updated_body,
                token=token,
            )
            pages_updated += 1
        elif changed and dry_run:
            pages_updated += 1

        total_rewrites += rewrites
        total_unresolved += unresolved
        total_alias_rewrites += alias_rewrites
        alias_keys_used.update(page_alias_keys_used)
        page_results.append(
            {
                "page_url": page_url,
                "rewrites": rewrites,
                "alias_rewrites": alias_rewrites,
                "unresolved_local_refs": unresolved,
                "changed": changed,
            }
        )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "base_url": normalize_base_url(base_url),
        "course_id": str(course_id),
        "issues_json": str(issues_json_path),
        "alias_map_json": alias_map_source,
        "dry_run": bool(dry_run),
        "issue_counts": {
            "total_issues": len(issues),
            "missing_page_link_issues": len(page_urls),
        },
        "summary": {
            "pages_scanned": len(page_urls),
            "pages_updated": pages_updated,
            "total_rewrites": total_rewrites,
            "total_alias_rewrites": total_alias_rewrites,
            "total_unresolved_local_refs": total_unresolved,
            "file_name_collisions": len(collisions),
        },
        "alias_map_rules_loaded": len(alias_map),
        "alias_keys_used": sorted(alias_keys_used),
        "file_name_collisions": collisions,
        "page_results": page_results,
    }
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-auto-relink",
        description="Auto-relink Canvas page local file/image references after import issues export.",
    )
    parser.add_argument("--base-url", required=True, type=str, help="Canvas base URL")
    parser.add_argument("--course-id", required=True, type=str, help="Canvas course ID")
    parser.add_argument("--token", required=True, type=str, help="Canvas API token")
    parser.add_argument(
        "--issues-json",
        required=True,
        type=Path,
        help="Path to canvas-migration-issues.json",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("output/canvas-auto-relink-report.json"),
        help="Path to write auto-relink report JSON",
    )
    parser.add_argument(
        "--alias-map-json",
        type=Path,
        help="Optional JSON map for basename aliases (for example old template names -> new template asset names).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and report rewrites without updating Canvas pages.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.issues_json.exists():
        parser.error(f"Issues JSON does not exist: {args.issues_json}")

    report_path = auto_relink_missing_links(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        issues_json_path=args.issues_json,
        output_json_path=args.output_json,
        alias_map_json_path=args.alias_map_json,
        dry_run=args.dry_run,
    )
    print(f"Auto-relink report JSON: {report_path}")


if __name__ == "__main__":
    main()

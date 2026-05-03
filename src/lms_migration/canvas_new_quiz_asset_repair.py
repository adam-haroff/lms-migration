from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from zipfile import ZipFile

from .canvas_api import (
    fetch_course_files,
    fetch_course_folders,
    fetch_new_quiz_items,
    fetch_new_quizzes,
    normalize_base_url,
    update_new_quiz_item_body,
    upload_course_file,
)
from .canvas_file_organizer import _ensure_folder_path
from .canvas_post_import import _FileRef, _build_file_index, _build_folder_path_index


_CANONICAL_QUIZ_IMAGE_FOLDER = "course files/course-content/course-images"
_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?P<attr>href|src)\s*=\s*)(?P<quote>[\"'])(?P<url>[^\"']+)(?P=quote)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class QuizAssetRef:
    attr: str
    url: str
    basename: str


def _normalize_basename(value: str) -> str:
    return posixpath.basename(value.strip().replace("\\", "/")).strip().lower()


def _is_repair_candidate_url(url: str) -> bool:
    value = html.unescape(url).strip()
    if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return False
    parsed = urlparse(value)
    path = parsed.path.lower()
    if "/files/" in path and (path.endswith("/preview") or "/download" in path):
        return False
    if "/file_contents/" in path:
        return True
    if parsed.scheme or value.startswith("//"):
        return False
    return True


def extract_quiz_asset_refs(item_body_html: str) -> list[QuizAssetRef]:
    refs: list[QuizAssetRef] = []
    for match in _ATTR_RE.finditer(item_body_html):
        original_url = str(match.group("url")).strip()
        if not _is_repair_candidate_url(original_url):
            continue
        parsed = urlparse(html.unescape(original_url))
        basename = _normalize_basename(unquote(parsed.path))
        if not basename:
            continue
        refs.append(
            QuizAssetRef(
                attr=str(match.group("attr")).lower(),
                url=original_url,
                basename=basename,
            )
        )
    return refs


def _build_source_zip_index(source_zip_path: Path) -> dict[str, list[str]]:
    by_basename: dict[str, list[str]] = {}
    with ZipFile(source_zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            basename = _normalize_basename(info.filename)
            if not basename:
                continue
            by_basename.setdefault(basename, []).append(info.filename)
    return by_basename


def _resolve_source_member(
    source_zip_index: dict[str, list[str]],
    basename: str,
) -> tuple[str | None, str]:
    matches = source_zip_index.get(basename, [])
    if not matches:
        return None, "missing"
    if len(matches) > 1:
        return None, "ambiguous"
    return matches[0], "ok"


def _rewrite_quiz_item_body(
    *,
    item_body_html: str,
    file_index: dict[str, list[_FileRef]],
    course_id: str,
) -> tuple[str, int, int, list[str]]:
    rewrites = 0
    unresolved = 0
    unresolved_basenames: list[str] = []

    def replace(match: re.Match[str]) -> str:
        nonlocal rewrites
        nonlocal unresolved
        original_url = str(match.group("url")).strip()
        if not _is_repair_candidate_url(original_url):
            return match.group(0)
        parsed = urlparse(html.unescape(original_url))
        basename = _normalize_basename(unquote(parsed.path))
        if not basename:
            return match.group(0)
        matches = file_index.get(basename, [])
        if len(matches) != 1:
            unresolved += 1
            unresolved_basenames.append(basename)
            return match.group(0)

        file_id = matches[0].file_id
        attr = str(match.group("attr")).lower()
        target_url = (
            f"/courses/{course_id}/files/{file_id}/preview"
            if attr == "src"
            else f"/courses/{course_id}/files/{file_id}/download?wrap=1"
        )
        if target_url == original_url:
            return match.group(0)
        rewrites += 1
        return f'{match.group("prefix")}"{target_url}"'

    updated = _ATTR_RE.sub(replace, item_body_html)
    return updated, rewrites, unresolved, sorted(set(unresolved_basenames))


def reconcile_new_quiz_assets(
    *,
    base_url: str,
    course_id: str,
    token: str,
    source_zip_path: Path,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    if not source_zip_path.exists():
        raise ValueError(f"Source zip does not exist: {source_zip_path}")

    normalized_base = normalize_base_url(base_url)
    source_zip_index = _build_source_zip_index(source_zip_path)

    files = fetch_course_files(base_url=normalized_base, course_id=course_id, token=token)
    folders = fetch_course_folders(
        base_url=normalized_base, course_id=course_id, token=token
    )
    folder_paths = _build_folder_path_index(folders)
    file_index, collisions = _build_file_index(files, folder_paths=folder_paths)

    ensured_folder_id = ""
    created_folders: list[dict[str, object]] = []
    folders_mut = list(folders)
    if not dry_run:
        ensured_folder_id, folders_mut, just_created = _ensure_folder_path(
            base_url=normalized_base,
            course_id=course_id,
            token=token,
            target_folder=_CANONICAL_QUIZ_IMAGE_FOLDER,
            folders=folders_mut,
        )
        created_folders = [
            {
                "id": str(folder.get("id") or "").strip(),
                "full_name": str(folder.get("full_name") or folder.get("name") or "").strip(),
            }
            for folder in just_created
        ]
    else:
        folder_paths_mut = _build_folder_path_index(folders_mut)
        for folder in folders_mut:
            folder_id = str(folder.get("id") or "").strip()
            if folder_id and folder_paths_mut.get(folder_id) == _CANONICAL_QUIZ_IMAGE_FOLDER:
                ensured_folder_id = folder_id
                break

    uploaded_assets: list[dict[str, object]] = []
    upload_failures: list[dict[str, object]] = []
    source_lookup_issues: list[dict[str, object]] = []
    item_results: list[dict[str, object]] = []

    quizzes = fetch_new_quizzes(base_url=normalized_base, course_id=course_id, token=token)

    with tempfile.TemporaryDirectory(prefix="canvas-new-quiz-assets-") as tmp_dir:
        temp_root = Path(tmp_dir)
        for quiz in quizzes:
            if not isinstance(quiz, dict):
                continue
            assignment_id = str(quiz.get("id") or "").strip()
            if not assignment_id:
                continue
            quiz_title = str(quiz.get("title") or "").strip()
            items = fetch_new_quiz_items(
                base_url=normalized_base,
                course_id=course_id,
                assignment_id=assignment_id,
                token=token,
            )
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                entry = item.get("entry") or {}
                if not item_id or not isinstance(entry, dict):
                    continue
                item_body = str(entry.get("item_body") or "")
                if not item_body:
                    continue
                refs = extract_quiz_asset_refs(item_body)
                if not refs:
                    continue

                unique_basenames = sorted({ref.basename for ref in refs})
                uploaded_for_item: list[str] = []
                missing_for_item: list[str] = []
                ambiguous_for_item: list[str] = []

                for basename in unique_basenames:
                    if len(file_index.get(basename, [])) == 1:
                        continue
                    member_name, source_status = _resolve_source_member(
                        source_zip_index, basename
                    )
                    if source_status == "missing":
                        missing_for_item.append(basename)
                        source_lookup_issues.append(
                            {
                                "quiz_title": quiz_title,
                                "assignment_id": assignment_id,
                                "item_id": item_id,
                                "basename": basename,
                                "reason": "missing-in-source-zip",
                            }
                        )
                        continue
                    if source_status == "ambiguous" or member_name is None:
                        ambiguous_for_item.append(basename)
                        source_lookup_issues.append(
                            {
                                "quiz_title": quiz_title,
                                "assignment_id": assignment_id,
                                "item_id": item_id,
                                "basename": basename,
                                "reason": "ambiguous-in-source-zip",
                                "matches": source_zip_index.get(basename, []),
                            }
                        )
                        continue
                    if dry_run:
                        continue
                    try:
                        with ZipFile(source_zip_path, "r") as zf:
                            temp_path = temp_root / Path(member_name).name
                            temp_path.write_bytes(zf.read(member_name))
                        uploaded = upload_course_file(
                            base_url=normalized_base,
                            course_id=course_id,
                            folder_id=ensured_folder_id,
                            file_path=temp_path,
                            token=token,
                            on_duplicate="rename",
                        )
                        file_id = str(uploaded.get("id") or "").strip()
                        if not file_id:
                            raise RuntimeError(
                                f"Upload succeeded without a file id for {basename}"
                            )
                        file_index[basename] = [
                            _FileRef(
                                file_id=file_id,
                                name=str(uploaded.get("display_name") or uploaded.get("filename") or temp_path.name).strip(),
                                folder_path=_CANONICAL_QUIZ_IMAGE_FOLDER,
                            )
                        ]
                        uploaded_for_item.append(basename)
                        uploaded_assets.append(
                            {
                                "basename": basename,
                                "source_member": member_name,
                                "file_id": file_id,
                                "quiz_title": quiz_title,
                                "assignment_id": assignment_id,
                                "item_id": item_id,
                            }
                        )
                    except Exception as exc:  # pragma: no cover - network/runtime dependent
                        upload_failures.append(
                            {
                                "basename": basename,
                                "source_member": member_name,
                                "quiz_title": quiz_title,
                                "assignment_id": assignment_id,
                                "item_id": item_id,
                                "error": str(exc),
                            }
                        )

                updated_body, rewrites, unresolved, unresolved_basenames = _rewrite_quiz_item_body(
                    item_body_html=item_body,
                    file_index=file_index,
                    course_id=course_id,
                )
                changed = rewrites > 0 and updated_body != item_body
                if changed and not dry_run:
                    update_new_quiz_item_body(
                        base_url=normalized_base,
                        course_id=course_id,
                        assignment_id=assignment_id,
                        item_id=item_id,
                        item_body_html=updated_body,
                        token=token,
                        entry_type=str(item.get("entry_type") or "").strip() or None,
                    )

                item_results.append(
                    {
                        "quiz_title": quiz_title,
                        "assignment_id": assignment_id,
                        "item_id": item_id,
                        "refs_found": len(refs),
                        "unique_basenames": unique_basenames,
                        "rewrites": rewrites,
                        "unresolved_local_refs": unresolved,
                        "unresolved_basenames": unresolved_basenames,
                        "uploaded_basenames": uploaded_for_item,
                        "missing_source_basenames": missing_for_item,
                        "ambiguous_source_basenames": ambiguous_for_item,
                        "changed": changed,
                    }
                )

    payload = {
        "base_url": normalized_base,
        "course_id": str(course_id),
        "source_zip": str(source_zip_path),
        "dry_run": bool(dry_run),
        "summary": {
            "quizzes_scanned": len([quiz for quiz in quizzes if isinstance(quiz, dict)]),
            "items_with_candidate_refs": len(item_results),
            "items_updated": sum(1 for row in item_results if row.get("changed")),
            "refs_rewritten": sum(int(row.get("rewrites", 0) or 0) for row in item_results),
            "assets_uploaded": len(uploaded_assets),
            "source_lookup_issues": len(source_lookup_issues),
            "upload_failures": len(upload_failures),
            "file_name_collisions": len(collisions),
            "folders_created": len(created_folders),
        },
        "canonical_folder": _CANONICAL_QUIZ_IMAGE_FOLDER,
        "created_folders": created_folders,
        "file_name_collisions": collisions,
        "uploaded_assets": uploaded_assets,
        "source_lookup_issues": source_lookup_issues,
        "upload_failures": upload_failures,
        "item_results": item_results,
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore missing or non-canonical New Quiz image/file assets from the original D2L source zip."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report_path = reconcile_new_quiz_assets(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        source_zip_path=args.source_zip,
        output_json_path=args.output_json,
        dry_run=bool(args.dry_run),
    )
    print(report_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

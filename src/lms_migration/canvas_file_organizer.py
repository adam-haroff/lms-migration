from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .canvas_api import (
    create_course_folder,
    delete_canvas_folder,
    fetch_course_files,
    fetch_course_folders,
    move_course_file,
    normalize_base_url,
)
from .canvas_post_import import _build_folder_path_index


_CANONICAL_COURSE_CONTENT_ROOT = "course files/course-content"
_CANONICAL_COURSE_IMAGES_ROOT = f"{_CANONICAL_COURSE_CONTENT_ROOT}/course-images"
_TEMPLATE_PREFIX = "course files/template-images"
_UPLOADED_MEDIA_PREFIX = "course files/uploaded media"
_PROTECTED_EMPTY_PREFIXES = (
    _CANONICAL_COURSE_CONTENT_ROOT,
    _TEMPLATE_PREFIX,
    _UPLOADED_MEDIA_PREFIX,
)


def _normalize_folder_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/").lower()


def _normalize_basename(value: str) -> str:
    return Path(value.strip()).name.lower()


def _target_folder_for_file(name: str) -> str | None:
    basename = _normalize_basename(name)
    stem = Path(basename).stem
    suffix = Path(basename).suffix
    if stem == "course-card" and suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return _CANONICAL_COURSE_IMAGES_ROOT
    if basename == "netiquette.pdf":
        return _CANONICAL_COURSE_CONTENT_ROOT
    return None


def _folder_depth(full_name: str) -> int:
    normalized = _normalize_folder_path(full_name)
    if not normalized:
        return 0
    return len([part for part in normalized.split("/") if part])


def _is_protected_empty_folder(full_name: str) -> bool:
    normalized = _normalize_folder_path(full_name)
    if normalized == "course files":
        return True
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in _PROTECTED_EMPTY_PREFIXES
    )


def _build_file_name_index(files: list[dict[str, Any]], folder_paths: dict[str, str]) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        file_id = str(file_obj.get("id") or "").strip()
        if not file_id:
            continue
        name = str(file_obj.get("display_name") or file_obj.get("filename") or "").strip()
        if not name:
            continue
        folder_id = str(file_obj.get("folder_id") or "").strip()
        folder_path = folder_paths.get(folder_id, "")
        index[(_normalize_basename(name), folder_path)].append(file_id)
    return index


def plan_canvas_file_organization(
    *,
    files: list[dict[str, Any]],
    folders: list[dict[str, Any]],
) -> dict[str, Any]:
    folder_paths = _build_folder_path_index(folders)
    file_name_index = _build_file_name_index(files, folder_paths)
    moves: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    target_folders_needed: set[str] = set()

    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        file_id = str(file_obj.get("id") or "").strip()
        if not file_id:
            continue
        name = str(file_obj.get("display_name") or file_obj.get("filename") or "").strip()
        if not name:
            continue
        basename = _normalize_basename(name)
        folder_id = str(file_obj.get("folder_id") or "").strip()
        current_folder = folder_paths.get(folder_id, "")
        target_folder = _target_folder_for_file(name)
        if not target_folder or current_folder == target_folder:
            continue
        conflicting_ids = [
            other_id
            for other_id in file_name_index.get((basename, target_folder), [])
            if other_id != file_id
        ]
        if conflicting_ids:
            skipped.append(
                {
                    "file_id": file_id,
                    "name": name,
                    "current_folder": current_folder,
                    "target_folder": target_folder,
                    "reason": "target-already-contains-same-name",
                    "conflicting_file_ids": conflicting_ids,
                }
            )
            continue
        moves.append(
            {
                "file_id": file_id,
                "name": name,
                "current_folder": current_folder,
                "target_folder": target_folder,
            }
        )
        target_folders_needed.add(target_folder)

    return {
        "moves": moves,
        "skipped": skipped,
        "target_folders_needed": sorted(target_folders_needed),
    }


def plan_empty_folder_deletions(
    *,
    files: list[dict[str, Any]],
    folders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    files_by_folder: dict[str, int] = defaultdict(int)
    child_counts: dict[str, int] = defaultdict(int)
    folder_by_id: dict[str, dict[str, Any]] = {}

    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        folder_id = str(file_obj.get("folder_id") or "").strip()
        if folder_id:
            files_by_folder[folder_id] += 1

    for folder in folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("id") or "").strip()
        if not folder_id:
            continue
        folder_by_id[folder_id] = folder
        parent_id = str(folder.get("parent_folder_id") or "").strip()
        if parent_id:
            child_counts[parent_id] += 1

    candidates = sorted(
        (
            folder
            for folder in folder_by_id.values()
            if not _is_protected_empty_folder(str(folder.get("full_name") or folder.get("name") or ""))
        ),
        key=lambda folder: _folder_depth(str(folder.get("full_name") or folder.get("name") or "")),
        reverse=True,
    )

    deletions: list[dict[str, Any]] = []
    for folder in candidates:
        folder_id = str(folder.get("id") or "").strip()
        if not folder_id:
            continue
        if files_by_folder.get(folder_id, 0) != 0:
            continue
        if child_counts.get(folder_id, 0) != 0:
            continue
        deletions.append(
            {
                "id": folder_id,
                "name": str(folder.get("name") or "").strip(),
                "full_name": str(folder.get("full_name") or folder.get("name") or "").strip(),
            }
        )
        parent_id = str(folder.get("parent_folder_id") or "").strip()
        if parent_id:
            child_counts[parent_id] = max(0, child_counts.get(parent_id, 0) - 1)

    return deletions


def _ensure_folder_path(
    *,
    base_url: str,
    course_id: str,
    token: str,
    target_folder: str,
    folders: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_target = _normalize_folder_path(target_folder)
    folder_paths = _build_folder_path_index(folders)
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("id") or "").strip()
        if folder_id and folder_paths.get(folder_id) == normalized_target:
            return folder_id, folders, []

    created: list[dict[str, Any]] = []
    folders_mut = list(folders)
    path_by_id = _build_folder_path_index(folders_mut)
    id_by_path = {path: folder_id for folder_id, path in path_by_id.items()}
    root_id = id_by_path.get("course files")
    if not root_id:
        raise RuntimeError("Could not resolve Canvas course files root folder.")

    segments = [segment for segment in normalized_target.split("/") if segment]
    if not segments or segments[0] != "course files":
        raise RuntimeError(f"Unexpected folder target outside course files root: {target_folder}")

    current_id = root_id
    current_path = "course files"
    for segment in segments[1:]:
        next_path = f"{current_path}/{segment}"
        existing_id = id_by_path.get(next_path)
        if existing_id:
            current_id = existing_id
            current_path = next_path
            continue
        created_folder = create_course_folder(
            base_url=base_url,
            course_id=course_id,
            token=token,
            name=segment,
            parent_folder_id=current_id,
        )
        folders_mut.append(created_folder)
        current_id = str(created_folder.get("id") or "").strip()
        current_path = _normalize_folder_path(
            str(created_folder.get("full_name") or created_folder.get("name") or next_path)
        )
        id_by_path[current_path] = current_id
        created.append(created_folder)

    return current_id, folders_mut, created


def run_canvas_file_organizer(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    normalized_base = normalize_base_url(base_url)
    files = fetch_course_files(base_url=normalized_base, course_id=course_id, token=token)
    folders = fetch_course_folders(base_url=normalized_base, course_id=course_id, token=token)

    plan = plan_canvas_file_organization(files=files, folders=folders)
    moves = list(plan.get("moves", []))
    skipped = list(plan.get("skipped", []))
    target_folders_needed = list(plan.get("target_folders_needed", []))

    created_folders: list[dict[str, Any]] = []
    move_results: list[dict[str, Any]] = []
    move_errors: list[dict[str, Any]] = []
    folders_mut = list(folders)
    ensured_folder_ids: dict[str, str] = {}

    if not dry_run:
        for target_folder in target_folders_needed:
            folder_id, folders_mut, just_created = _ensure_folder_path(
                base_url=normalized_base,
                course_id=course_id,
                token=token,
                target_folder=target_folder,
                folders=folders_mut,
            )
            ensured_folder_ids[target_folder] = folder_id
            created_folders.extend(just_created)

        for move in moves:
            try:
                destination_folder_id = ensured_folder_ids.get(move["target_folder"])
                if not destination_folder_id:
                    raise RuntimeError(
                        f"Missing destination folder id for {move['target_folder']}"
                    )
                updated_file = move_course_file(
                    base_url=normalized_base,
                    file_id=move["file_id"],
                    token=token,
                    parent_folder_id=destination_folder_id,
                )
                move_results.append(
                    {
                        **move,
                        "status": "moved",
                        "updated_folder_id": str(updated_file.get("folder_id") or "").strip(),
                    }
                )
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                move_errors.append({**move, "error": str(exc)})

    files_after = fetch_course_files(base_url=normalized_base, course_id=course_id, token=token)
    folders_after = fetch_course_folders(base_url=normalized_base, course_id=course_id, token=token)

    deleted_folders: list[dict[str, Any]] = []
    folder_delete_errors: list[dict[str, Any]] = []
    if not dry_run:
        while True:
            deletion_plan = plan_empty_folder_deletions(files=files_after, folders=folders_after)
            if not deletion_plan:
                break
            progress = False
            for folder in deletion_plan:
                try:
                    delete_canvas_folder(
                        base_url=normalized_base,
                        folder_id=folder["id"],
                        token=token,
                    )
                    deleted_folders.append({**folder, "status": "deleted"})
                    progress = True
                except Exception as exc:  # pragma: no cover - network/runtime dependent
                    folder_delete_errors.append({**folder, "error": str(exc)})
            if not progress:
                break
            files_after = fetch_course_files(
                base_url=normalized_base,
                course_id=course_id,
                token=token,
            )
            folders_after = fetch_course_folders(
                base_url=normalized_base,
                course_id=course_id,
                token=token,
            )

    final_empty_folders = plan_empty_folder_deletions(files=files_after, folders=folders_after)

    payload = {
        "base_url": normalized_base,
        "course_id": str(course_id),
        "dry_run": bool(dry_run),
        "summary": {
            "files_scanned": len(files),
            "folders_scanned": len(folders),
            "moves_planned": len(moves),
            "moves_completed": len(move_results),
            "moves_skipped": len(skipped),
            "move_errors": len(move_errors),
            "folders_created": len(created_folders),
            "empty_folders_deleted": len(deleted_folders),
            "folder_delete_errors": len(folder_delete_errors),
            "empty_folders_remaining": len(final_empty_folders),
        },
        "planned_moves": moves,
        "skipped_moves": skipped,
        "move_results": move_results,
        "move_errors": move_errors,
        "created_folders": [
            {
                "id": str(folder.get("id") or "").strip(),
                "full_name": str(folder.get("full_name") or folder.get("name") or "").strip(),
            }
            for folder in created_folders
        ],
        "deleted_folders": deleted_folders,
        "folder_delete_errors": folder_delete_errors,
        "empty_folders_remaining": final_empty_folders,
    }
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Organize common seeded-template course files and prune empty folders."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    report_path = run_canvas_file_organizer(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        output_json_path=Path(args.output_json),
        dry_run=bool(args.dry_run),
    )
    print(report_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

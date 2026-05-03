from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .canvas_api import (
    delete_canvas_file,
    delete_canvas_folder,
    fetch_course,
    fetch_course_announcements,
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_files,
    fetch_course_folders,
    fetch_course_modules,
    fetch_course_pages,
    normalize_base_url,
)
from .canvas_cleanup_audit import audit_course_cleanup_data


_ARTIFACT_EXTENSIONS = {".html", ".xml", ".qti", ".xsd"}
_ARTIFACT_BASENAMES = {
    "imsmanifest.xml",
    "course_settings.xml",
    "assignment_groups.xml",
    "context.xml",
    "module_meta.xml",
}
_PROTECTED_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}


def _normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/").lower()


def _classify_unused_file(file_obj: dict[str, Any]) -> dict[str, Any] | None:
    name = str(file_obj.get("name") or "").strip()
    folder_path = _normalize_path(str(file_obj.get("folder_path") or ""))
    if not name:
        return None

    suffix = Path(name).suffix.lower()
    basename = name.lower()

    if suffix in _PROTECTED_EXTENSIONS:
        return None

    if folder_path.startswith("course files/template-images"):
        return None
    if folder_path.startswith("course files/course-content"):
        # Keep actual course resources conservative unless they are explicit package artifacts.
        if basename not in _ARTIFACT_BASENAMES and suffix != ".xml":
            return None

    if basename in _ARTIFACT_BASENAMES:
        reason = "known_canvas_import_artifact"
    elif basename.endswith("_d2l.xml"):
        reason = "d2l_metadata_xml"
    elif suffix == ".xml":
        reason = "unused_xml_import_artifact"
    elif suffix in {".qti", ".xsd"}:
        reason = "quiz_import_artifact"
    elif suffix == ".html":
        reason = "unused_source_html_import_artifact"
    else:
        return None

    return {
        "id": str(file_obj.get("id") or "").strip(),
        "name": name,
        "folder_path": str(file_obj.get("folder_path") or "").strip(),
        "reason": reason,
    }


def cleanup_import_artifacts(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    apply_deletes: bool = False,
) -> dict[str, Any]:
    normalized_base = normalize_base_url(base_url)
    course = fetch_course(base_url=normalized_base, course_id=course_id, token=token)
    pages = fetch_course_pages(base_url=normalized_base, course_id=course_id, token=token)
    modules = fetch_course_modules(base_url=normalized_base, course_id=course_id, token=token)
    assignments = fetch_course_assignments(
        base_url=normalized_base, course_id=course_id, token=token
    )
    discussions = fetch_course_discussion_topics(
        base_url=normalized_base, course_id=course_id, token=token
    )
    announcements = fetch_course_announcements(
        base_url=normalized_base, course_id=course_id, token=token
    )
    files = fetch_course_files(base_url=normalized_base, course_id=course_id, token=token)
    folders = fetch_course_folders(
        base_url=normalized_base, course_id=course_id, token=token
    )

    audit = audit_course_cleanup_data(
        course=course,
        pages=pages,
        modules=modules,
        assignments=assignments,
        discussions=discussions,
        announcements=announcements,
        files=files,
        folders=folders,
    )

    candidates: list[dict[str, Any]] = []
    for file_obj in audit.get("unused_files", []):
        if not isinstance(file_obj, dict):
            continue
        classified = _classify_unused_file(file_obj)
        if classified is not None:
            candidates.append(classified)

    deleted_files: list[dict[str, Any]] = []
    delete_errors: list[dict[str, Any]] = []
    if apply_deletes:
        for candidate in candidates:
            file_id = str(candidate.get("id") or "").strip()
            if not file_id:
                continue
            try:
                delete_canvas_file(
                    base_url=normalized_base,
                    file_id=file_id,
                    token=token,
                )
                deleted_files.append(candidate)
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                delete_errors.append(
                    {
                        "type": "file",
                        "id": file_id,
                        "name": candidate.get("name", ""),
                        "error": str(exc),
                    }
                )

    empty_folder_candidates = [
        folder
        for folder in audit.get("empty_folders", [])
        if isinstance(folder, dict)
        and not _normalize_path(str(folder.get("full_name") or "")).startswith(
            "course files/template-images"
        )
    ]

    deleted_folders: list[dict[str, Any]] = []
    if apply_deletes:
        for folder in empty_folder_candidates:
            folder_id = str(folder.get("id") or "").strip()
            if not folder_id:
                continue
            try:
                delete_canvas_folder(
                    base_url=normalized_base,
                    folder_id=folder_id,
                    token=token,
                    force=True,
                )
                deleted_folders.append(folder)
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                delete_errors.append(
                    {
                        "type": "folder",
                        "id": folder_id,
                        "name": folder.get("full_name") or folder.get("name") or "",
                        "error": str(exc),
                    }
                )

    report = {
        "base_url": normalized_base,
        "course_id": course_id,
        "apply_deletes": apply_deletes,
        "summary": {
            "artifact_file_candidates": len(candidates),
            "empty_folder_candidates": len(empty_folder_candidates),
            "deleted_files": len(deleted_files),
            "deleted_folders": len(deleted_folders),
            "delete_errors": len(delete_errors),
        },
        "artifact_file_candidates": candidates,
        "empty_folder_candidates": empty_folder_candidates,
        "deleted_files": deleted_files,
        "deleted_folders": deleted_folders,
        "delete_errors": delete_errors,
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Identify or delete likely Canvas import artifact files and empty folders "
            "after a migration."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--apply-deletes", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = cleanup_import_artifacts(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json_path=args.output_json,
        apply_deletes=args.apply_deletes,
    )
    print(json.dumps(report["summary"], indent=2))
    print(args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

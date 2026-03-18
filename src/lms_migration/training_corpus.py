from __future__ import annotations

import json
from pathlib import Path


def _first_existing(base_dir: Path, relative_paths: tuple[str, ...]) -> Path | None:
    for relative_path in relative_paths:
        candidate = base_dir / relative_path
        if candidate.is_file():
            return candidate
    return None


def _latest_match(directory: Path, patterns: tuple[str, ...]) -> Path | None:
    if not directory.exists():
        return None
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in directory.glob(pattern) if path.is_file())
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _load_json(path: Path | None) -> dict | list | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_snapshot_identity(snapshot_path: Path | None) -> dict:
    payload = _load_json(snapshot_path)
    if not isinstance(payload, dict):
        return {"course_id": None, "course_name": "", "course_code": ""}
    course = payload.get("course", {})
    if not isinstance(course, dict):
        course = {}
    return {
        "course_id": course.get("id"),
        "course_name": str(course.get("name", "") or "").strip(),
        "course_code": str(course.get("course_code", "") or "").strip(),
    }


def discover_course_artifacts(course_dir: Path) -> dict:
    before_zip = _first_existing(course_dir, ("before/d2l-export.zip",))
    if before_zip is None:
        before_zip = _latest_match(course_dir / "before", ("*.zip",))
    if before_zip is None:
        before_zip = _latest_match(course_dir, ("*.zip",))

    after_zip = _first_existing(course_dir, ("after/canvas-gold-export.imscc",))
    if after_zip is None:
        after_zip = _latest_match(course_dir / "after", ("*.imscc",))
    if after_zip is None:
        after_zip = _latest_match(course_dir, ("*.imscc",))

    snapshot_json = _first_existing(course_dir, ("after/canvas-snapshot.json",))
    if snapshot_json is None:
        snapshot_json = _latest_match(course_dir / "after", ("*.snapshot.json",))

    snapshot_markdown = _first_existing(course_dir, ("after/canvas-snapshot.md",))
    if snapshot_markdown is None:
        snapshot_markdown = _latest_match(course_dir / "after", ("*.snapshot.md",))

    issues_json = _first_existing(
        course_dir,
        ("baseline/canvas-migration-issues.json", "canvas-migration-issues.json"),
    )

    metadata_json = course_dir / "metadata.json"
    if not metadata_json.exists():
        metadata_json = None

    snapshot_identity = load_snapshot_identity(snapshot_json)
    completeness_score = sum(
        1
        for candidate in (before_zip, after_zip, snapshot_json, snapshot_markdown, issues_json, metadata_json)
        if candidate is not None
    )

    return {
        "course_code": course_dir.name,
        "course_dir": course_dir,
        "before_zip": before_zip,
        "after_zip": after_zip,
        "snapshot_json": snapshot_json,
        "snapshot_markdown": snapshot_markdown,
        "issues_json": issues_json,
        "metadata_json": metadata_json,
        "snapshot_identity": snapshot_identity,
        "completeness_score": completeness_score,
    }


def collect_course_artifacts(
    roots: list[Path] | tuple[Path, ...],
    *,
    skip_names: tuple[str, ...] = ("template",),
) -> list[dict]:
    selected: dict[str, dict] = {}
    normalized_skip = {name.strip().lower() for name in skip_names}

    for root in roots:
        if not root.exists():
            continue
        for course_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            if course_dir.name.startswith("_") or course_dir.name.lower() in normalized_skip:
                continue
            artifacts = discover_course_artifacts(course_dir)
            if (
                artifacts["before_zip"] is None
                and artifacts["after_zip"] is None
                and artifacts["snapshot_json"] is None
                and artifacts["issues_json"] is None
            ):
                continue
            artifacts["root"] = root
            existing = selected.get(course_dir.name)
            if existing is None or artifacts["completeness_score"] > existing.get("completeness_score", 0):
                selected[course_dir.name] = artifacts

    return [selected[key] for key in sorted(selected)]

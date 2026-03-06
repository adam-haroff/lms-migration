from __future__ import annotations

import argparse
import json
import posixpath
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from zipfile import ZipFile


_LEGACY_D2L_RE = re.compile(r"^/?(?:d2l/|content/enforced/)", flags=re.IGNORECASE)
_SHARED_TEMPLATE_RE = re.compile(r"^/?shared/brightspace_html_template/", flags=re.IGNORECASE)
_QUIZ_KEY_WARNING = "The importer couldn't determine the correct answers for this question."


def _normalize_course_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _extract_number_tokens(text: str) -> set[str]:
    return {match.group(0) for match in re.finditer(r"\d{4,5}", text)}


def _load_snapshot_index(snapshot_root: Path) -> tuple[list[dict], dict[Path, dict]]:
    entries: list[dict] = []
    payload_cache: dict[Path, dict] = {}

    for snapshot_path in sorted(snapshot_root.rglob("canvas-course-*.snapshot.json")):
        payload: dict = {}
        try:
            loaded = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}

        payload_cache[snapshot_path] = payload
        course = payload.get("course") if isinstance(payload.get("course"), dict) else {}
        number_tokens = set()
        number_tokens.update(_extract_number_tokens(snapshot_path.parent.name))
        number_tokens.update(_extract_number_tokens(str(course.get("course_code", ""))))
        number_tokens.update(_extract_number_tokens(str(course.get("name", ""))))
        number_tokens.update(_extract_number_tokens(str(payload.get("course_id", ""))))
        keys = {
            _normalize_course_token(snapshot_path.parent.name),
            _normalize_course_token(str(course.get("course_code", ""))),
            _normalize_course_token(str(course.get("name", ""))),
            _normalize_course_token(str(payload.get("course_id", ""))),
        }
        entries.append(
            {
                "path": snapshot_path,
                "keys": {item for item in keys if item},
                "numbers": number_tokens,
            }
        )

    return entries, payload_cache


def _find_snapshot_for_course(
    course_code: str,
    snapshot_entries: list[dict],
) -> Path | None:
    token = _normalize_course_token(course_code)
    if not token:
        return None

    # Exact folder/token match first.
    for row in snapshot_entries:
        keys = row.get("keys", set())
        if token in keys:
            return row["path"]

    # Fallback: token appears inside snapshot code/name tokens.
    for row in snapshot_entries:
        keys = row.get("keys", set())
        if any(token in item for item in keys):
            return row["path"]

    number_tokens = _extract_number_tokens(course_code)
    if number_tokens:
        number_candidates = [
            row
            for row in snapshot_entries
            if number_tokens.intersection(row.get("numbers", set()))
        ]
        if len(number_candidates) == 1:
            return number_candidates[0]["path"]

    return None


def _extract_refs(html_text: str) -> list[str]:
    return [match.strip() for match in re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html_text, flags=re.IGNORECASE)]


def _is_local_candidate(url: str) -> bool:
    value = url.strip()
    if not value:
        return False
    if value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return False
    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//"):
        return False
    return True


def _collect_zip_metrics(zip_path: Path) -> dict:
    with ZipFile(zip_path, "r") as zf:
        names = [item for item in zf.namelist() if not item.endswith("/")]
        name_set = set(names)
        html_files = [item for item in names if item.lower().endswith((".html", ".htm"))]
        manifest_hrefs: list[str] = []
        if "imsmanifest.xml" in name_set:
            manifest = zf.read("imsmanifest.xml").decode("utf-8", errors="ignore")
            manifest_hrefs = re.findall(r'href\s*=\s*"([^"]+)"', manifest, flags=re.IGNORECASE)

        refs: list[tuple[str, str]] = []
        for html_file in html_files:
            html_text = zf.read(html_file).decode("utf-8", errors="ignore")
            refs.extend((html_file, url) for url in _extract_refs(html_text))

    stats: Counter[str] = Counter()
    unresolved_samples: list[dict[str, str]] = []
    lower_map = {name.lower(): name for name in name_set}

    for src, raw_url in refs:
        url = raw_url.strip()
        if not _is_local_candidate(url):
            if url.lower().startswith(("http://", "https://")):
                stats["external_http_refs"] += 1
            continue

        stats["local_refs"] += 1
        if _LEGACY_D2L_RE.match(url):
            stats["legacy_d2l_refs"] += 1
            continue
        if _SHARED_TEMPLATE_RE.match(url):
            stats["shared_template_refs"] += 1
            continue

        parsed = urlparse(url)
        path_text = unquote(parsed.path).strip().replace("\\", "/")
        if not path_text:
            continue
        if path_text.startswith("/"):
            normalized = posixpath.normpath(path_text.lstrip("/"))
        else:
            normalized = posixpath.normpath(posixpath.join(posixpath.dirname(src), path_text))
        normalized = normalized.lstrip("./")

        if normalized in name_set:
            stats["local_refs_resolve_exact"] += 1
            continue
        if normalized.lower() in lower_map:
            stats["local_refs_resolve_casefold"] += 1
            continue

        basename = posixpath.basename(normalized).lower()
        if basename:
            basename_matches = [name for name in name_set if posixpath.basename(name).lower() == basename]
            if len(basename_matches) == 1:
                stats["local_refs_resolve_basename_unique"] += 1
                continue

        stats["local_refs_unresolved"] += 1
        if len(unresolved_samples) < 40:
            unresolved_samples.append(
                {
                    "source_html": src,
                    "ref": raw_url,
                    "normalized": normalized,
                }
            )

    manifest_set = set(manifest_hrefs)
    stats["zip_file_count"] = len(names)
    stats["html_file_count"] = len(html_files)
    stats["manifest_href_count"] = len(manifest_hrefs)
    stats["files_not_in_manifest_href"] = sum(
        1 for item in names if item != "imsmanifest.xml" and item not in manifest_set
    )

    return {
        "zip_file": str(zip_path),
        "stats": dict(stats),
        "unresolved_local_refs_sample": unresolved_samples,
    }


def analyze_example_corpus(
    *,
    examples_dir: Path,
    snapshot_root: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    courses: list[dict] = []
    skipped_courses: list[dict[str, str]] = []
    overall: Counter[str] = Counter()
    snapshot_entries, snapshot_payload_cache = _load_snapshot_index(snapshot_root)

    for course_dir in sorted([path for path in examples_dir.iterdir() if path.is_dir()]):
        package_path = next(
            (
                path
                for path in sorted(course_dir.iterdir())
                if path.is_file() and path.suffix.lower() in {".zip", ".imscc"}
            ),
            None,
        )
        issues_path = course_dir / "canvas-migration-issues.json"
        snapshot_path = _find_snapshot_for_course(course_dir.name, snapshot_entries)

        if package_path is None:
            skipped_courses.append({"course_code": course_dir.name, "reason": "missing_package_zip_or_imscc"})
            continue
        if not issues_path.exists():
            skipped_courses.append({"course_code": course_dir.name, "reason": "missing_canvas_migration_issues_json"})
            continue

        issues_payload = json.loads(issues_path.read_text(encoding="utf-8"))
        if not isinstance(issues_payload, list):
            skipped_courses.append({"course_code": course_dir.name, "reason": "invalid_issues_payload"})
            continue
        issue_counter: Counter[str] = Counter(
            str(item.get("description", "")).strip()
            for item in issues_payload
            if isinstance(item, dict)
        )
        package_metrics = _collect_zip_metrics(package_path)

        snapshot_payload = None
        if snapshot_path is not None:
            snapshot_payload = snapshot_payload_cache.get(snapshot_path)
            if not isinstance(snapshot_payload, dict):
                snapshot_payload = None

        courses.append(
            {
                "course_code": course_dir.name,
                "source_package_metrics": package_metrics,
                "issue_counts": dict(issue_counter),
                "snapshot": {
                    "available": bool(snapshot_payload),
                    "course_id": snapshot_payload.get("course_id") if isinstance(snapshot_payload, dict) else None,
                    "course_code": ((snapshot_payload.get("course") or {}).get("course_code") if isinstance(snapshot_payload, dict) else None),
                    "course_name": ((snapshot_payload.get("course") or {}).get("name") if isinstance(snapshot_payload, dict) else None),
                    "counts": snapshot_payload.get("counts", {}) if isinstance(snapshot_payload, dict) else {},
                    "snapshot_json": str(snapshot_path) if snapshot_path is not None else "",
                },
            }
        )

        overall["courses"] += 1
        if snapshot_payload is None:
            overall["courses_without_snapshot"] += 1
        else:
            overall["courses_with_snapshot"] += 1
        overall["issues_total"] += len(issues_payload)
        overall["issue_missing_page_link"] += issue_counter.get(
            "Missing links found in imported content - Wiki Page body",
            0,
        )
        overall["issue_quiz_answer_key"] += issue_counter.get(
            _QUIZ_KEY_WARNING,
            0,
        )
        for key, value in package_metrics["stats"].items():
            if isinstance(value, int):
                overall[f"d2l_{key}"] += value

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "examples_dir": str(examples_dir),
        "snapshot_root": str(snapshot_root),
        "overall": dict(overall),
        "skipped_courses": skipped_courses,
        "courses": courses,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "training-corpus-analysis.json"
    md_path = output_dir / "training-corpus-analysis.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Training Corpus Analysis",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        f"- Examples directory: `{examples_dir}`",
        f"- Snapshot root: `{snapshot_root}`",
        "",
        "## Overall",
        "",
    ]
    for key in sorted(report["overall"]):
        lines.append(f"- {key}: {report['overall'][key]}")

    lines.extend(["", "## Per Course", ""])
    for row in courses:
        course = row["course_code"]
        stats = row["source_package_metrics"]["stats"]
        issue_counts = row["issue_counts"]
        snapshot = row["snapshot"]
        lines.append(f"### {course}")
        lines.append(f"- Source package: `{row['source_package_metrics']['zip_file']}`")
        if snapshot.get("available"):
            lines.append(f"- Snapshot JSON: `{snapshot['snapshot_json']}`")
            lines.append(f"- Snapshot course: `{snapshot['course_id']}` | `{snapshot['course_code']}`")
        else:
            lines.append("- Snapshot JSON: `(not found)`")
        lines.append(f"- D2L html files: {stats.get('html_file_count', 0)}")
        lines.append(f"- D2L local refs: {stats.get('local_refs', 0)}")
        lines.append(f"- D2L legacy /d2l refs: {stats.get('legacy_d2l_refs', 0)}")
        lines.append(f"- D2L shared template refs: {stats.get('shared_template_refs', 0)}")
        lines.append(f"- D2L unresolved local refs (pre-fix): {stats.get('local_refs_unresolved', 0)}")
        lines.append(f"- Canvas issues total: {sum(issue_counts.values())}")
        lines.append(
            f"- Canvas missing page links: {issue_counts.get('Missing links found in imported content - Wiki Page body', 0)}"
        )
        lines.append(
            "- Canvas quiz-answer-key warnings: "
            f"{issue_counts.get(_QUIZ_KEY_WARNING, 0)}"
        )
        if snapshot.get("available"):
            lines.append(
                "- Gold snapshot pages/modules/files: "
                f"{snapshot['counts'].get('pages', 0)}/{snapshot['counts'].get('modules', 0)}/{snapshot['counts'].get('files', 0)}"
            )
        else:
            lines.append("- Gold snapshot pages/modules/files: n/a")
        lines.append("")

    if skipped_courses:
        lines.extend(["## Skipped Folders", ""])
        for row in skipped_courses:
            lines.append(f"- {row['course_code']}: {row['reason']}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-analyze-examples",
        description="Analyze D2L example corpus with Canvas issue JSON and manual-conversion snapshots.",
    )
    parser.add_argument(
        "--examples-dir",
        type=Path,
        default=Path("resources/examples"),
        help="Directory containing per-course example folders (zip + canvas-migration-issues.json).",
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=Path("output"),
        help="Root directory containing per-course canvas snapshot JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/examples"),
        help="Directory for analysis outputs.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.examples_dir.exists():
        parser.error(f"Examples directory does not exist: {args.examples_dir}")
    if not args.snapshot_root.exists():
        parser.error(f"Snapshot root does not exist: {args.snapshot_root}")

    json_path, md_path = analyze_example_corpus(
        examples_dir=args.examples_dir,
        snapshot_root=args.snapshot_root,
        output_dir=args.output_dir,
    )
    print(f"Training corpus analysis JSON: {json_path}")
    print(f"Training corpus analysis Markdown: {md_path}")


if __name__ == "__main__":
    main()

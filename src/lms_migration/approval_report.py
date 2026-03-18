from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from .example_corpus import _collect_zip_metrics
from .training_corpus import collect_course_artifacts
from .visual_audit import build_visual_audit


_HTML_EXTENSIONS = {".html", ".htm"}
_QUIZ_KEY_WARNING = "The importer couldn't determine the correct answers for this question."
_MISSING_PAGE_LINK_WARNING = "Missing links found in imported content - Wiki Page body"
_MODULE_RE = re.compile(r"^module\s+\d+", flags=re.IGNORECASE)
_TOPIC_RE = re.compile(r"^topic\s+\d+", flags=re.IGNORECASE)
_COURSE_FOLDER_RE = re.compile(r"[^a-z0-9]+")


def _normalize_course_token(value: str) -> str:
    return _COURSE_FOLDER_RE.sub("", value.lower())


def _load_json(path: Path | None) -> dict | list | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _extract_html_title(text: str, fallback_name: str) -> str:
    match = re.search(r"<title\b[^>]*>(?P<title>.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return fallback_name
    title = re.sub(r"<[^>]+>", " ", match.group("title"))
    title = html.unescape(title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or fallback_name


def _load_zip_titles(zip_path: Path) -> list[str]:
    titles: list[str] = []
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _HTML_EXTENSIONS:
                continue
            fallback = Path(name).stem.replace("_", " ").strip()
            text = zf.read(name).decode("utf-8", errors="ignore")
            titles.append(_extract_html_title(text, fallback))
    return titles


def _summarize_zip_structure(zip_path: Path) -> dict:
    try:
        zip_metrics = _collect_zip_metrics(zip_path)
    except Exception:
        zip_metrics = {"zip_file": str(zip_path), "stats": {}, "unresolved_local_refs_sample": []}

    titles = _load_zip_titles(zip_path)
    title_counter = Counter(title.lower().strip() for title in titles if title.strip())

    topic_html_files = 0
    module_html_files = 0
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _HTML_EXTENSIONS:
                continue
            parts = [part for part in Path(name).parts if part]
            if any(_TOPIC_RE.match(part) for part in parts):
                topic_html_files += 1
            if any(_MODULE_RE.match(part) for part in parts):
                module_html_files += 1

    return {
        "zip_path": str(zip_path),
        "stats": zip_metrics.get("stats", {}),
        "topic_html_files": topic_html_files,
        "module_html_files": module_html_files,
        "intro_objectives_pages": sum(
            count for title, count in title_counter.items() if "introduction and objectives" in title
        ),
        "intro_checklist_pages": sum(
            count for title, count in title_counter.items() if "introduction and checklist" in title
        ),
        "learning_activities_pages": sum(
            count for title, count in title_counter.items() if "learning activities" in title
        ),
        "html_titles": sorted(title_counter),
    }


def _extract_snapshot_counts(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    raw_counts = payload.get("counts", {})
    return raw_counts if isinstance(raw_counts, dict) else {}


def _snapshot_features(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}

    module_names = [
        str(item.get("name", "")).strip()
        for item in payload.get("modules", [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    page_titles = [
        str(item.get("title", "")).strip()
        for item in payload.get("pages", [])
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]
    counts = _extract_snapshot_counts(payload)

    module_style_count = sum(1 for name in module_names if _MODULE_RE.match(name))
    topic_style_count = sum(1 for name in module_names if _TOPIC_RE.match(name))
    start_here_modules = sum(1 for name in module_names if "start here" in name.lower())
    instructor_modules = sum(
        1
        for name in module_names
        if "instructor module" in name.lower()
        or "faculty use" in name.lower()
        or "do not publish" in name.lower()
        or "preparing your course" in name.lower()
    )
    help_discussion_modules = sum(1 for name in module_names if "help discussion" in name.lower())
    overview_modules = sum(1 for name in module_names if "course overview" in name.lower())
    intro_objectives_pages = sum(1 for title in page_titles if "introduction and objectives" in title.lower())
    intro_checklist_pages = sum(1 for title in page_titles if "introduction and checklist" in title.lower())
    learning_activities_pages = sum(1 for title in page_titles if "learning activities" in title.lower())

    module_count = len(module_names)
    page_count = len(page_titles)

    return {
        "module_count": module_count,
        "page_count": page_count,
        "assignment_count": int(counts.get("assignments", 0) or 0),
        "discussion_count": int(counts.get("discussions", 0) or 0),
        "announcement_count": int(counts.get("announcements", 0) or 0),
        "file_count": int(counts.get("files", 0) or 0),
        "module_style_count": module_style_count,
        "topic_style_count": topic_style_count,
        "module_style_ratio": round(module_style_count / max(module_count, 1), 3),
        "topic_style_ratio": round(topic_style_count / max(module_count, 1), 3),
        "start_here_modules": start_here_modules,
        "instructor_modules": instructor_modules,
        "help_discussion_modules": help_discussion_modules,
        "overview_modules": overview_modules,
        "intro_objectives_pages": intro_objectives_pages,
        "intro_checklist_pages": intro_checklist_pages,
        "learning_activities_pages": learning_activities_pages,
        "sample_modules": module_names[:10],
        "sample_pages": page_titles[:10],
    }


def _classify_structure_cohort(features: dict) -> str:
    if not features:
        return "unknown"
    if (
        features.get("module_style_count", 0) >= 4
        and features.get("intro_checklist_pages", 0) >= 2
        and features.get("learning_activities_pages", 0) >= 2
    ):
        return "template-module"
    if features.get("topic_style_count", 0) >= 4:
        return "topic-sequence"
    if (
        features.get("help_discussion_modules", 0) > 0
        or features.get("overview_modules", 0) > 0
        or features.get("intro_objectives_pages", 0) > features.get("intro_checklist_pages", 0)
    ):
        return "overview-objectives"
    return "hybrid"


def _cohort_label(cohort: str) -> str:
    labels = {
        "template-module": "Template-aligned module course",
        "topic-sequence": "Topic-sequence course",
        "overview-objectives": "Overview/objectives course",
        "hybrid": "Hybrid course",
        "unknown": "Unknown",
    }
    return labels.get(cohort, cohort)


def _load_issue_counter(path: Path | None) -> Counter[str]:
    payload = _load_json(path)
    if not isinstance(payload, list):
        return Counter()
    return Counter(
        str(item.get("description", "")).strip()
        for item in payload
        if isinstance(item, dict) and str(item.get("description", "")).strip()
    )


def _load_training_metadata(
    training_metadata_root: Path,
    course_code: str,
    metadata_path: Path | None = None,
) -> dict:
    if metadata_path is None:
        metadata_path = training_metadata_root / course_code / "metadata.json"
    payload = _load_json(metadata_path)
    if isinstance(payload, dict):
        return payload
    return {}


def _load_training_profile(
    *,
    artifacts: dict,
    training_metadata_root: Path,
    output_root: Path,
) -> dict | None:
    course_code = artifacts["course_code"]
    metadata = _load_training_metadata(
        training_metadata_root,
        course_code,
        artifacts.get("metadata_json"),
    )
    output_dir = output_root / course_code

    snapshot_path = artifacts.get("snapshot_json")
    if snapshot_path is None:
        snapshot_path = _latest_file(output_dir, "canvas-course-*.snapshot.json")
    snapshot_payload = _load_json(snapshot_path)
    snapshot_features = _snapshot_features(snapshot_payload if isinstance(snapshot_payload, dict) else None)
    if not snapshot_features:
        return None

    issues_path = artifacts.get("issues_json")
    if issues_path is None:
        output_issues = output_dir / "canvas-migration-issues.json"
        issues_path = output_issues if output_issues.exists() else None
    issue_counter = _load_issue_counter(issues_path)

    before_zip = artifacts.get("before_zip")
    after_zip = artifacts.get("after_zip")
    source_features = _summarize_zip_structure(before_zip) if before_zip is not None else {}
    gold_features = _summarize_zip_structure(after_zip) if after_zip is not None else {}
    snapshot_course = ((snapshot_payload or {}).get("course") or {}) if isinstance(snapshot_payload, dict) else {}

    return {
        "course_code": course_code,
        "course_name": str(snapshot_course.get("name", "")).strip(),
        "snapshot_course_id": snapshot_course.get("id"),
        "snapshot_course_code": str(snapshot_course.get("course_code", "")).strip(),
        "focus_tags": metadata.get("focus_tags", []) if isinstance(metadata.get("focus_tags", []), list) else [],
        "snapshot_path": str(snapshot_path) if snapshot_path is not None else "",
        "issues_path": str(issues_path) if isinstance(issues_path, Path) else "",
        "source_package": str(before_zip) if before_zip is not None else "",
        "gold_package": str(after_zip) if after_zip is not None else "",
        "training_source_root": str(artifacts.get("root", "")),
        "snapshot_features": snapshot_features,
        "source_features": source_features,
        "gold_features": gold_features,
        "cohort": _classify_structure_cohort(snapshot_features),
        "issue_total": sum(issue_counter.values()),
        "issue_missing_page_links": int(issue_counter.get(_MISSING_PAGE_LINK_WARNING, 0)),
        "issue_quiz_key_warnings": int(issue_counter.get(_QUIZ_KEY_WARNING, 0)),
        "top_issue_types": [
            {"description": description, "count": count}
            for description, count in issue_counter.most_common(5)
        ],
    }


def _collect_training_profiles(
    *,
    examples_dir: Path,
    training_metadata_root: Path,
    output_root: Path,
    current_course_code: str,
) -> list[dict]:
    current_token = _normalize_course_token(current_course_code)
    profiles: list[dict] = []
    for artifacts in collect_course_artifacts([training_metadata_root, examples_dir]):
        course_code = artifacts["course_code"]
        if _normalize_course_token(course_code) == current_token:
            continue
        profile = _load_training_profile(
            artifacts=artifacts,
            training_metadata_root=training_metadata_root,
            output_root=output_root,
        )
        if profile is not None:
            profiles.append(profile)
    return profiles


def _metric_distance(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0)


def _nested_metric(payload: dict, dotted_key: str) -> float:
    current: object = payload
    for key in dotted_key.split("."):
        if not isinstance(current, dict):
            return 0.0
        current = current.get(key, 0)
    try:
        return float(current or 0)
    except Exception:
        return 0.0


def _similarity_score(current: dict, reference: dict) -> float:
    current_features = current.get("snapshot_features", {})
    reference_features = reference.get("snapshot_features", {})
    score = 100.0

    if not current_features:
        current_source = current.get("source_features", {})
        reference_source = reference.get("source_features", {})
        weighted_source_metrics = (
            ("topic_html_files", 20.0),
            ("module_html_files", 10.0),
            ("intro_objectives_pages", 12.0),
            ("intro_checklist_pages", 12.0),
            ("learning_activities_pages", 12.0),
            ("stats.html_file_count", 14.0),
            ("stats.local_refs_unresolved", 8.0),
        )
        for key, weight in weighted_source_metrics:
            score -= weight * _metric_distance(
                _nested_metric(current_source, key),
                _nested_metric(reference_source, key),
            )
        return round(max(score, 0.0), 1)

    if current.get("cohort") != reference.get("cohort"):
        score -= 25.0

    weighted_metrics = (
        ("module_count", 14.0),
        ("page_count", 14.0),
        ("assignment_count", 8.0),
        ("discussion_count", 4.0),
        ("module_style_ratio", 8.0),
        ("topic_style_ratio", 8.0),
        ("intro_checklist_pages", 6.0),
        ("intro_objectives_pages", 6.0),
        ("learning_activities_pages", 6.0),
    )
    for key, weight in weighted_metrics:
        score -= weight * _metric_distance(
            float(current_features.get(key, 0) or 0),
            float(reference_features.get(key, 0) or 0),
        )

    boolean_metrics = (
        "start_here_modules",
        "instructor_modules",
        "help_discussion_modules",
        "overview_modules",
    )
    for key in boolean_metrics:
        current_has = int(current_features.get(key, 0) > 0)
        reference_has = int(reference_features.get(key, 0) > 0)
        if current_has != reference_has:
            score -= 3.0

    current_issues = float(current.get("post_import_issue_total", current.get("issue_total", 0)) or 0)
    reference_issues = float(reference.get("issue_total", 0) or 0)
    score -= 8.0 * _metric_distance(current_issues, reference_issues)

    return round(max(score, 0.0), 1)


def _top_reference_courses(current: dict, training_profiles: list[dict], *, limit: int = 3) -> list[dict]:
    ranked: list[dict] = []
    for profile in training_profiles:
        score = _similarity_score(current, profile)
        ranked.append(
            {
                "course_code": profile["course_code"],
                "course_name": profile.get("course_name", ""),
                "snapshot_course_id": profile.get("snapshot_course_id"),
                "snapshot_course_code": profile.get("snapshot_course_code", ""),
                "cohort": profile.get("cohort", "unknown"),
                "cohort_label": _cohort_label(profile.get("cohort", "unknown")),
                "focus_tags": profile.get("focus_tags", []),
                "similarity_score": score,
                "issue_total": profile.get("issue_total", 0),
                "snapshot_counts": {
                    key: profile.get("snapshot_features", {}).get(key, 0)
                    for key in ("page_count", "module_count", "assignment_count", "discussion_count")
                },
            }
        )
    return sorted(ranked, key=lambda row: row["similarity_score"], reverse=True)[:limit]


def _cohort_consensus(current: dict, training_profiles: list[dict]) -> dict:
    if not training_profiles:
        return {
            "cohort": current.get("cohort", "unknown"),
            "cohort_label": _cohort_label(current.get("cohort", "unknown")),
            "reference_courses": 0,
        }

    same_cohort = [profile for profile in training_profiles if profile.get("cohort") == current.get("cohort")]
    cohort_profiles = same_cohort or training_profiles

    def _median_for(key: str) -> float:
        values = [
            float(profile.get("snapshot_features", {}).get(key, 0) or 0)
            for profile in cohort_profiles
        ]
        return round(median(values), 1) if values else 0.0

    intro_style = "checklist"
    intro_objectives_total = sum(
        int(profile.get("snapshot_features", {}).get("intro_objectives_pages", 0) or 0)
        for profile in cohort_profiles
    )
    intro_checklist_total = sum(
        int(profile.get("snapshot_features", {}).get("intro_checklist_pages", 0) or 0)
        for profile in cohort_profiles
    )
    if intro_objectives_total > intro_checklist_total:
        intro_style = "objectives"

    module_style = "module"
    topic_total = sum(
        int(profile.get("snapshot_features", {}).get("topic_style_count", 0) or 0)
        for profile in cohort_profiles
    )
    module_total = sum(
        int(profile.get("snapshot_features", {}).get("module_style_count", 0) or 0)
        for profile in cohort_profiles
    )
    if topic_total > module_total:
        module_style = "topic"
    elif module_total == 0 and topic_total == 0:
        module_style = "neutral"

    return {
        "cohort": cohort_profiles[0].get("cohort", "unknown"),
        "cohort_label": _cohort_label(cohort_profiles[0].get("cohort", "unknown")),
        "reference_courses": len(cohort_profiles),
        "preferred_module_style": module_style,
        "preferred_intro_style": intro_style,
        "median_pages": _median_for("page_count"),
        "median_modules": _median_for("module_count"),
        "median_assignments": _median_for("assignment_count"),
        "median_post_import_issues": round(
            median(
                float(profile.get("issue_total", 0) or 0)
                for profile in cohort_profiles
            ),
            1,
        )
        if cohort_profiles
        else 0.0,
        "start_here_presence_rate": round(
            sum(1 for profile in cohort_profiles if profile.get("snapshot_features", {}).get("start_here_modules", 0) > 0)
            / max(len(cohort_profiles), 1),
            3,
        ),
        "instructor_module_presence_rate": round(
            sum(
                1
                for profile in cohort_profiles
                if profile.get("snapshot_features", {}).get("instructor_modules", 0) > 0
            )
            / max(len(cohort_profiles), 1),
            3,
        ),
        "learning_activities_presence_rate": round(
            sum(
                1
                for profile in cohort_profiles
                if profile.get("snapshot_features", {}).get("learning_activities_pages", 0) > 0
            )
            / max(len(cohort_profiles), 1),
            3,
        ),
    }


def _gate_status(*, approved: bool, review: bool = False) -> str:
    if approved:
        return "approved"
    if review:
        return "review"
    return "attention"


def _build_gate_summary(current: dict, consensus: dict) -> list[dict]:
    gates: list[dict] = []

    source_features = current.get("source_features", {})
    snapshot_features = current.get("snapshot_features", {})
    visual_summary = current.get("visual_audit", {}).get("summary", {})
    template_summary = current.get("template_overlay", {}).get("summary", {})
    issue_summary = current.get("migration_report", {}).get("issue_summary", {})
    live_audit = current.get("live_audit", {})
    live_counts = live_audit.get("counts", {}) if isinstance(live_audit, dict) else {}
    live_issue_counts = live_audit.get("finding_counts_by_issue_type", {}) if isinstance(live_audit, dict) else {}

    snapshot_available = bool(snapshot_features)
    preferred_module_style = consensus.get("preferred_module_style", "neutral")
    preferred_intro_style = consensus.get("preferred_intro_style", "checklist")
    structure_approved = False
    if snapshot_available:
        if preferred_module_style == "module":
            structure_approved = snapshot_features.get("module_style_count", 0) >= snapshot_features.get("topic_style_count", 0)
        elif preferred_module_style == "topic":
            structure_approved = snapshot_features.get("topic_style_count", 0) >= snapshot_features.get("module_style_count", 0)
        else:
            structure_approved = True

        if preferred_intro_style == "checklist":
            structure_approved = structure_approved and (
                snapshot_features.get("intro_checklist_pages", 0) >= snapshot_features.get("intro_objectives_pages", 0)
            )
        else:
            structure_approved = structure_approved and (
                snapshot_features.get("intro_objectives_pages", 0) >= snapshot_features.get("intro_checklist_pages", 0)
            )

    gates.append(
        {
            "id": "structure-conventions",
            "label": "Structure conventions",
            "status": _gate_status(
                approved=structure_approved,
                review=bool(snapshot_features or source_features),
            ),
            "summary": (
                "Current Canvas structure aligns with the closest training cohort."
                if structure_approved
                else (
                    "Canvas structure comparison is waiting for a post-import snapshot."
                    if not snapshot_available
                    else "Current Canvas structure diverges from the closest training cohort and needs review."
                )
            ),
            "evidence": [
                f"Cohort: {_cohort_label(current.get('cohort', 'unknown'))} ({consensus.get('reference_courses', 0)} reference course(s)).",
                f"Consensus module style: {preferred_module_style}; current snapshot module/topic counts: "
                f"{snapshot_features.get('module_style_count', 0)}/{snapshot_features.get('topic_style_count', 0)}.",
                f"Consensus intro style: {preferred_intro_style}; current intro checklist/objectives pages: "
                f"{snapshot_features.get('intro_checklist_pages', 0)}/{snapshot_features.get('intro_objectives_pages', 0)}.",
                f"Source Topic HTML files: {source_features.get('topic_html_files', 0)}; Canvas module-style modules: {snapshot_features.get('module_style_count', 0)}.",
                f"Source Introduction and Objectives pages: {source_features.get('intro_objectives_pages', 0)}; Canvas Introduction and Checklist pages: {snapshot_features.get('intro_checklist_pages', 0)}.",
            ],
            "recommended_actions": []
            if structure_approved
            else [
                (
                    "Import the package into Canvas and capture a course snapshot so structure can be compared against the training cohort."
                    if not snapshot_available
                    else "Expand structure-normalization rules for this cohort before the next migration run."
                ),
            ],
        }
    )

    visual_clean = all(
        int(visual_summary.get(key, 0) or 0) == 0
        for key in (
            "files_with_duplicate_title_first_block",
            "files_with_remaining_shared_template_refs",
            "files_with_remaining_title_tags",
            "files_with_nonstandard_hr",
            "files_with_icon_size_anomalies",
        )
    )
    template_clean = int(template_summary.get("unresolved_total", 0) or 0) == 0
    gates.append(
        {
            "id": "visual-template-fidelity",
            "label": "Visual and template fidelity",
            "status": _gate_status(
                approved=visual_clean and template_clean,
                review=bool(visual_summary or template_summary),
            ),
            "summary": (
                "Converted package cleared the main visual/template cleanup gates."
                if visual_clean and template_clean
                else "Converted package still has visual/template cleanup drift to review."
            ),
            "evidence": [
                f"Template overlay mapped_total/unresolved_total: {template_summary.get('mapped_total', 0)}/{template_summary.get('unresolved_total', 0)}.",
                f"Visual audit duplicate/shared/title/hr/icon anomalies: "
                f"{visual_summary.get('files_with_duplicate_title_first_block', 0)}/"
                f"{visual_summary.get('files_with_remaining_shared_template_refs', 0)}/"
                f"{visual_summary.get('files_with_remaining_title_tags', 0)}/"
                f"{visual_summary.get('files_with_nonstandard_hr', 0)}/"
                f"{visual_summary.get('files_with_icon_size_anomalies', 0)}.",
                f"Original accordion cards -> converted details blocks: "
                f"{visual_summary.get('total_original_accordion_cards', 0)} -> {visual_summary.get('total_converted_details_blocks', 0)}.",
            ],
            "recommended_actions": []
            if visual_clean and template_clean
            else [
                "Inspect the visual-audit detail rows and add a deterministic cleanup rule for the remaining pattern.",
            ],
        }
    )

    post_issue_total = int(current.get("post_import_issue_total", 0) or 0)
    missing_page_links = int(current.get("post_import_missing_page_links", 0) or 0)
    quiz_only_remaining = (
        post_issue_total > 0
        and post_issue_total == int(current.get("post_import_quiz_key_warnings", 0) or 0)
    )
    live_findings = int(live_counts.get("findings_total", 0) or 0)
    post_import_available = bool(
        current.get("post_import_issue_total", 0)
        or current.get("pre_import_issue_total", 0)
        or current.get("post_import_quiz_key_warnings", 0)
        or live_counts
        or current.get("live_audit", {})
    )
    post_import_approved = post_import_available and missing_page_links == 0 and (post_issue_total == 0 or quiz_only_remaining)
    gates.append(
        {
            "id": "post-import-quality",
            "label": "Post-import Canvas quality",
            "status": _gate_status(
                approved=post_import_approved and live_findings <= 4,
                review=bool(post_import_available),
            ),
            "summary": (
                "Post-import results are within an approval range for Canvas import quality."
                if post_import_approved and live_findings <= 4
                else (
                    "Post-import quality cannot be scored until the package is imported into Canvas and audited."
                    if not post_import_available
                    else "Post-import results still need review before treating the course as fully approved."
                )
            ),
            "evidence": [
                f"Pre/Post Canvas issue totals: {current.get('pre_import_issue_total', 0)} -> {post_issue_total}.",
                f"Post-import missing page-link issues: {missing_page_links}.",
                f"Post-import quiz-answer-key warnings: {current.get('post_import_quiz_key_warnings', 0)}.",
                f"Live audit findings total: {live_findings} ({', '.join(f'{k}={v}' for k, v in sorted(live_issue_counts.items())) or 'none'}).",
                f"Cohort median baseline import issues: {consensus.get('median_post_import_issues', 0)}.",
            ],
            "recommended_actions": []
            if post_import_approved and live_findings <= 4
            else [
                (
                    "Import the package into Canvas, then run Canvas cleanup, issues export, live audit, and a course snapshot."
                    if not post_import_available
                    else "Use the post-import fix checklist to close remaining import warnings before sign-off."
                ),
            ],
        }
    )

    manual_total = int(current.get("manual_review_issues", 0) or 0)
    accessibility_total = int(current.get("accessibility_issues", 0) or 0)
    manual_gate_approved = manual_total == 0 and accessibility_total == 0
    top_manual = issue_summary.get("top_manual_review_reasons", []) if isinstance(issue_summary, dict) else []
    top_a11y = issue_summary.get("top_accessibility_reasons", []) if isinstance(issue_summary, dict) else []
    gates.append(
        {
            "id": "manual-cleanup-load",
            "label": "Remaining manual cleanup",
            "status": _gate_status(
                approved=manual_gate_approved,
                review=not manual_gate_approved and (manual_total > 0 or accessibility_total > 0),
            ),
            "summary": (
                "No unresolved manual or accessibility cleanup remains."
                if manual_gate_approved
                else "Manual review and accessibility cleanup still remain after deterministic transforms."
            ),
            "evidence": [
                f"Migration report manual/accessibility totals: {manual_total}/{accessibility_total}.",
                "Top manual-review reasons: "
                + (
                    "; ".join(
                        f"{row.get('count', 0)} x {row.get('reason', '')}"
                        for row in top_manual[:4]
                        if isinstance(row, dict)
                    )
                    or "none"
                ),
                "Top accessibility reasons: "
                + (
                    "; ".join(
                        f"{row.get('count', 0)} x {row.get('reason', '')}"
                        for row in top_a11y[:4]
                        if isinstance(row, dict)
                    )
                    or "none"
                ),
            ],
            "recommended_actions": []
            if manual_gate_approved
            else [
                "Keep the deterministic cleanup passes enabled, then work the remaining manual-review checklist in priority order.",
            ],
        }
    )

    return gates


def _overall_status(gates: list[dict]) -> str:
    statuses = {str(gate.get("status", "")).strip().lower() for gate in gates}
    if "attention" in statuses:
        return "attention"
    if "review" in statuses:
        return "review"
    return "approved"


def _approval_score(gates: list[dict]) -> int:
    points = 0
    for gate in gates:
        status = str(gate.get("status", "")).strip().lower()
        if status == "approved":
            points += 2
        elif status == "review":
            points += 1
    max_points = max(len(gates) * 2, 1)
    return int(round((points / max_points) * 100))


def _summarize_current_course(
    *,
    course_code: str,
    current_snapshot: dict | None,
    current_source_zip: Path | None,
    current_migration_report: dict | None,
    current_visual_audit: dict | None,
    current_template_overlay: dict | None,
    pre_issue_counter: Counter[str],
    post_issue_counter: Counter[str],
    live_audit: dict | None,
) -> dict:
    snapshot_features = _snapshot_features(current_snapshot)
    source_features = _summarize_zip_structure(current_source_zip) if current_source_zip is not None else {}
    issue_summary = (
        current_migration_report.get("issue_summary", {})
        if isinstance(current_migration_report, dict)
        else {}
    )
    summary = (
        current_migration_report.get("summary", {})
        if isinstance(current_migration_report, dict)
        else {}
    )

    return {
        "course_code": course_code,
        "course_name": str(((current_snapshot or {}).get("course") or {}).get("name", "")).strip()
        if isinstance(current_snapshot, dict)
        else "",
        "snapshot_available": bool(current_snapshot),
        "snapshot_features": snapshot_features,
        "source_features": source_features,
        "cohort": _classify_structure_cohort(snapshot_features),
        "migration_report": current_migration_report if isinstance(current_migration_report, dict) else {},
        "visual_audit": current_visual_audit if isinstance(current_visual_audit, dict) else {},
        "template_overlay": current_template_overlay if isinstance(current_template_overlay, dict) else {},
        "live_audit": live_audit if isinstance(live_audit, dict) else {},
        "manual_review_issues": int(summary.get("manual_review_issues", 0) or 0),
        "accessibility_issues": int(summary.get("accessibility_issues", 0) or 0),
        "issue_summary": issue_summary if isinstance(issue_summary, dict) else {},
        "pre_import_issue_total": sum(pre_issue_counter.values()),
        "pre_import_missing_page_links": int(pre_issue_counter.get(_MISSING_PAGE_LINK_WARNING, 0)),
        "post_import_issue_total": sum(post_issue_counter.values()),
        "post_import_missing_page_links": int(post_issue_counter.get(_MISSING_PAGE_LINK_WARNING, 0)),
        "post_import_quiz_key_warnings": int(post_issue_counter.get(_QUIZ_KEY_WARNING, 0)),
    }


def _build_recommended_next_steps(current: dict, gates: list[dict]) -> list[str]:
    next_steps: list[str] = []
    post_import_available = bool(
        current.get("post_import_issue_total", 0)
        or current.get("pre_import_issue_total", 0)
        or current.get("post_import_quiz_key_warnings", 0)
        or current.get("live_audit", {})
    )

    if not post_import_available:
        next_steps.append("Import the canvas-ready package into a fresh Canvas course and run the Canvas cleanup workflow.")

    if int(current.get("post_import_quiz_key_warnings", 0) or 0) > 0:
        next_steps.append(
            f"Verify the {current['post_import_quiz_key_warnings']} remaining quiz-answer-key warning(s) in Canvas."
        )

    live_audit = current.get("live_audit", {})
    live_counts = live_audit.get("finding_counts_by_issue_type", {}) if isinstance(live_audit, dict) else {}
    neutralized = int(live_counts.get("neutralized_migration_link", 0) or 0)
    if neutralized > 0:
        next_steps.append(
            f"Resolve the {neutralized} neutralized migration link finding(s) reported by the live audit."
        )

    issue_summary = current.get("issue_summary", {})
    manual_reasons = issue_summary.get("top_manual_review_reasons", []) if isinstance(issue_summary, dict) else []
    for row in manual_reasons:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason", "")).strip()
        count = int(row.get("count", 0) or 0)
        if count <= 0 or not reason:
            continue
        next_steps.append(f"Reduce manual-review load for '{reason}' ({count} item(s)).")
        if len(next_steps) >= 3:
            break

    if len(next_steps) < 3:
        for gate in gates:
            if str(gate.get("status", "")).strip().lower() == "approved":
                continue
            for action in gate.get("recommended_actions", []):
                if action not in next_steps:
                    next_steps.append(action)
                if len(next_steps) >= 3:
                    break
            if len(next_steps) >= 3:
                break

    return next_steps[:3]


def build_approval_report(
    *,
    current_course_code: str,
    current_source_zip: Path | None,
    current_converted_zip: Path | None,
    current_migration_report_json: Path | None,
    current_visual_audit_json: Path | None,
    current_template_overlay_json: Path | None,
    current_snapshot_json: Path | None,
    pre_issues_json: Path | None,
    post_issues_json: Path | None,
    live_audit_json: Path | None,
    examples_dir: Path,
    training_metadata_root: Path,
    output_root: Path,
    output_json_path: Path,
    output_markdown_path: Path | None = None,
) -> tuple[Path, Path]:
    output_markdown = output_markdown_path or output_json_path.with_suffix(".md")

    current_migration_report = _load_json(current_migration_report_json)
    current_visual_audit = _load_json(current_visual_audit_json)
    if not isinstance(current_visual_audit, dict) and current_source_zip is not None and current_converted_zip is not None:
        current_visual_audit = build_visual_audit(
            original_zip=current_source_zip,
            converted_zip=current_converted_zip,
        )

    current_template_overlay = _load_json(current_template_overlay_json)
    current_snapshot = _load_json(current_snapshot_json)
    pre_issue_counter = _load_issue_counter(pre_issues_json)
    post_issue_counter = _load_issue_counter(post_issues_json)
    live_audit = _load_json(live_audit_json)

    current = _summarize_current_course(
        course_code=current_course_code,
        current_snapshot=current_snapshot if isinstance(current_snapshot, dict) else None,
        current_source_zip=current_source_zip,
        current_migration_report=current_migration_report if isinstance(current_migration_report, dict) else None,
        current_visual_audit=current_visual_audit if isinstance(current_visual_audit, dict) else None,
        current_template_overlay=current_template_overlay if isinstance(current_template_overlay, dict) else None,
        pre_issue_counter=pre_issue_counter,
        post_issue_counter=post_issue_counter,
        live_audit=live_audit if isinstance(live_audit, dict) else None,
    )

    training_profiles = _collect_training_profiles(
        examples_dir=examples_dir,
        training_metadata_root=training_metadata_root,
        output_root=output_root,
        current_course_code=current_course_code,
    )
    top_references = _top_reference_courses(current, training_profiles)
    consensus = _cohort_consensus(current, training_profiles)
    gates = _build_gate_summary(current, consensus)
    overall_status = _overall_status(gates)
    approval_score = _approval_score(gates)
    recommended_next_steps = _build_recommended_next_steps(current, gates)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "current_course_code": current_course_code,
            "current_source_zip": str(current_source_zip) if current_source_zip is not None else "",
            "current_converted_zip": str(current_converted_zip) if current_converted_zip is not None else "",
            "current_migration_report_json": str(current_migration_report_json) if current_migration_report_json is not None else "",
            "current_visual_audit_json": str(current_visual_audit_json) if current_visual_audit_json is not None else "",
            "current_template_overlay_json": str(current_template_overlay_json) if current_template_overlay_json is not None else "",
            "current_snapshot_json": str(current_snapshot_json) if current_snapshot_json is not None else "",
            "pre_issues_json": str(pre_issues_json) if pre_issues_json is not None else "",
            "post_issues_json": str(post_issues_json) if post_issues_json is not None else "",
            "live_audit_json": str(live_audit_json) if live_audit_json is not None else "",
            "examples_dir": str(examples_dir),
            "training_metadata_root": str(training_metadata_root),
            "output_root": str(output_root),
        },
        "summary": {
            "overall_status": overall_status,
            "approval_score": approval_score,
            "training_reference_courses": len(training_profiles),
            "reference_cohort": consensus.get("cohort", "unknown"),
            "reference_cohort_label": consensus.get("cohort_label", _cohort_label(consensus.get("cohort", "unknown"))),
        },
        "current_course": current,
        "cohort_consensus": consensus,
        "top_reference_courses": top_references,
        "approval_gates": gates,
        "recommended_next_steps": recommended_next_steps,
    }

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    current_snapshot_features = current.get("snapshot_features", {})
    source_features = current.get("source_features", {})
    lines = [
        "# Migration Approval Report",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Summary",
        "",
        f"- Overall status: {overall_status}",
        f"- Approval score: {approval_score}",
        f"- Current course: {current_course_code}",
        f"- Cohort: {_cohort_label(current.get('cohort', 'unknown'))}",
        f"- Training reference courses: {len(training_profiles)}",
        "",
        "## Current Course",
        "",
        f"- Snapshot pages/modules/assignments/discussions: "
        f"{current_snapshot_features.get('page_count', 0)}/"
        f"{current_snapshot_features.get('module_count', 0)}/"
        f"{current_snapshot_features.get('assignment_count', 0)}/"
        f"{current_snapshot_features.get('discussion_count', 0)}",
        f"- Source Topic HTML files -> Canvas Module-style modules: "
        f"{source_features.get('topic_html_files', 0)} -> {current_snapshot_features.get('module_style_count', 0)}",
        f"- Source Intro/Objectives pages -> Canvas Intro/Checklist pages: "
        f"{source_features.get('intro_objectives_pages', 0)} -> {current_snapshot_features.get('intro_checklist_pages', 0)}",
        f"- Pre/Post Canvas issue totals: {current.get('pre_import_issue_total', 0)} -> {current.get('post_import_issue_total', 0)}",
        f"- Migration report manual/accessibility totals: "
        f"{current.get('manual_review_issues', 0)}/{current.get('accessibility_issues', 0)}",
        "",
        "## Closest Reference Courses",
        "",
    ]

    for row in top_references:
        lines.append(
            f"- {row['course_code']} | score={row['similarity_score']} | "
            f"{row['cohort_label']} | canvas_course={row.get('snapshot_course_id', '')} | "
            f"pages/modules={row['snapshot_counts']['page_count']}/{row['snapshot_counts']['module_count']}"
        )
    if not top_references:
        lines.append("- No comparable training courses were available.")

    lines.extend(["", "## Approval Gates", ""])
    for gate in gates:
        lines.append(f"### {gate['label']} [{str(gate.get('status', '')).upper()}]")
        lines.append("")
        lines.append(gate.get("summary", ""))
        lines.append("")
        for evidence in gate.get("evidence", []):
            lines.append(f"- {evidence}")
        if gate.get("recommended_actions"):
            for action in gate.get("recommended_actions", []):
                lines.append(f"- Recommended action: {action}")
        lines.append("")

    lines.extend(["## Next Steps", ""])
    for item in recommended_next_steps:
        lines.append(f"- {item}")
    if not recommended_next_steps:
        lines.append("- No additional next steps were identified.")
    lines.append("")

    output_markdown.write_text("\n".join(lines), encoding="utf-8")
    return output_json_path, output_markdown


def _resolve_current_course_code(
    *,
    current_course_code: str | None,
    current_snapshot_json: Path | None,
    current_source_zip: Path | None,
    current_migration_report_json: Path | None,
) -> str:
    if current_course_code:
        return current_course_code.strip()

    for path in (current_snapshot_json, current_source_zip, current_migration_report_json):
        if path is None:
            continue
        parent_name = path.parent.name.strip()
        if parent_name and parent_name.lower() not in {"output", "resources"}:
            return parent_name

    if current_snapshot_json is not None:
        payload = _load_json(current_snapshot_json)
        if isinstance(payload, dict):
            course_code = str(((payload.get("course") or {}).get("course_code", ""))).strip()
            if course_code:
                return course_code
    return "current-course"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-approval-report",
        description="Build a deterministic approval report by comparing the current migration to the local training corpus.",
    )
    parser.add_argument("--current-course-code", type=str, default="", help="Optional current course code.")
    parser.add_argument("--current-source-zip", type=Path, default=None, help="Original D2L package zip.")
    parser.add_argument("--current-converted-zip", type=Path, default=None, help="Converted canvas-ready zip.")
    parser.add_argument("--current-migration-report-json", type=Path, default=None, help="Current migration report JSON.")
    parser.add_argument("--current-visual-audit-json", type=Path, default=None, help="Current visual audit JSON.")
    parser.add_argument("--current-template-overlay-json", type=Path, default=None, help="Current template overlay report JSON.")
    parser.add_argument("--current-snapshot-json", type=Path, default=None, help="Current Canvas snapshot JSON.")
    parser.add_argument("--pre-issues-json", type=Path, default=None, help="Optional pre post-import issues JSON.")
    parser.add_argument("--post-issues-json", type=Path, default=None, help="Optional post-import issues JSON.")
    parser.add_argument("--live-audit-json", type=Path, default=None, help="Optional Canvas live audit JSON.")
    parser.add_argument(
        "--examples-dir",
        type=Path,
        default=Path("resources/examples"),
        help="Directory containing training/example course folders.",
    )
    parser.add_argument(
        "--training-metadata-root",
        type=Path,
        default=Path("resources/training-corpus-v2/courses"),
        help="Directory containing training metadata folders.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="Root output directory containing training snapshots/issues.",
    )
    parser.add_argument("--output-json", type=Path, required=True, help="Approval report JSON output path.")
    parser.add_argument("--output-markdown", type=Path, default=None, help="Approval report Markdown output path.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    current_course_code = _resolve_current_course_code(
        current_course_code=args.current_course_code,
        current_snapshot_json=args.current_snapshot_json,
        current_source_zip=args.current_source_zip,
        current_migration_report_json=args.current_migration_report_json,
    )

    output_json, output_markdown = build_approval_report(
        current_course_code=current_course_code,
        current_source_zip=args.current_source_zip,
        current_converted_zip=args.current_converted_zip,
        current_migration_report_json=args.current_migration_report_json,
        current_visual_audit_json=args.current_visual_audit_json,
        current_template_overlay_json=args.current_template_overlay_json,
        current_snapshot_json=args.current_snapshot_json,
        pre_issues_json=args.pre_issues_json,
        post_issues_json=args.post_issues_json,
        live_audit_json=args.live_audit_json,
        examples_dir=args.examples_dir,
        training_metadata_root=args.training_metadata_root,
        output_root=args.output_root,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(f"Approval report JSON: {output_json}")
    print(f"Approval report Markdown: {output_markdown}")


if __name__ == "__main__":
    main()

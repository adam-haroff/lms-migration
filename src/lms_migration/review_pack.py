from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import posixpath
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote, urlparse
from zipfile import ZipFile

from .visual_audit import build_visual_audit


_HTML_EXTENSIONS = {".html", ".htm"}
_TITLE_RE = re.compile(
    r"<title\b[^>]*>(?P<body>.*?)</title>", flags=re.IGNORECASE | re.DOTALL
)
_BODY_RE = re.compile(
    r"<body\b[^>]*>(?P<body>.*?)</body>", flags=re.IGNORECASE | re.DOTALL
)
_HEADING_RE = re.compile(
    r"<(?P<tag>h[1-6])\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_BLOCK_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|li|td|th)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SRC_ATTR_RE = re.compile(
    r'(?P<prefix>\bsrc\s*=\s*)(?P<quote>["\'])(?P<src>[^"\']+)(?P=quote)',
    flags=re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _load_html_files(zip_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _HTML_EXTENSIONS:
                continue
            files[name] = zf.read(name).decode("utf-8", errors="ignore")
    return files


def _load_json(path: Path | None) -> dict | list | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _strip_html(value: str) -> str:
    cleaned = _SCRIPT_STYLE_RE.sub(" ", value)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    return _SPACE_RE.sub(" ", cleaned).strip()


def _normalize_text(value: str) -> str:
    lowered = _strip_html(value).lower().replace("&", "and")
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return _SPACE_RE.sub(" ", lowered).strip()


def _extract_title(value: str, fallback: str) -> str:
    match = _TITLE_RE.search(value)
    if match is None:
        return fallback
    title = _strip_html(match.group("body"))
    return title or fallback


def _extract_heading_outline(value: str, *, limit: int = 8) -> list[str]:
    headings: list[str] = []
    for match in _HEADING_RE.finditer(value):
        text = _strip_html(match.group("body"))
        if not text:
            continue
        if text.lower() == "printer-friendly version":
            continue
        headings.append(f"{match.group('tag').lower()}: {text}")
        if len(headings) >= limit:
            break
    return headings


def _extract_preview_blocks(value: str, *, limit: int = 6) -> list[str]:
    blocks: list[str] = []
    for match in _BLOCK_RE.finditer(value):
        text = _strip_html(match.group("body"))
        if not text:
            continue
        normalized = text.lower()
        if normalized in {"printer-friendly version", "printer friendly version"}:
            continue
        if len(text) > 220:
            text = text[:217].rstrip() + "..."
        blocks.append(text)
        if len(blocks) >= limit:
            break
    return blocks


def _content_metrics(value: str) -> dict[str, int]:
    plain = _strip_html(value)
    return {
        "heading_count": len(re.findall(r"<h[1-6]\b", value, flags=re.IGNORECASE)),
        "image_count": len(re.findall(r"<img\b", value, flags=re.IGNORECASE)),
        "iframe_count": len(re.findall(r"<iframe\b", value, flags=re.IGNORECASE)),
        "table_count": len(re.findall(r"<table\b", value, flags=re.IGNORECASE)),
        "list_count": len(re.findall(r"<(?:ul|ol)\b", value, flags=re.IGNORECASE)),
        "accordion_count": len(re.findall(r"<details\b", value, flags=re.IGNORECASE)),
        "divider_count": len(re.findall(r"<hr\b", value, flags=re.IGNORECASE)),
        "link_count": len(re.findall(r"<a\b", value, flags=re.IGNORECASE)),
        "template_icon_count": len(
            re.findall(r"<img\b[^>]*templateassets/[^>]*>", value, flags=re.IGNORECASE)
        ),
        "word_count": len(re.findall(r"\b\w+\b", plain)),
    }


def _extract_body_html(value: str) -> str:
    match = _BODY_RE.search(value)
    body = match.group("body") if match is not None else value
    body = _SCRIPT_STYLE_RE.sub("", body)
    body = _TITLE_RE.sub("", body)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return body.strip()


def _resolve_local_asset_name(
    *,
    page_path: str,
    raw_ref: str,
    name_set: set[str],
    lower_map: dict[str, str],
) -> str | None:
    value = raw_ref.strip()
    if not value or value.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
        return None
    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//"):
        return None

    normalized_ref = unquote((parsed.path or "").strip()).replace("\\", "/")
    if not normalized_ref:
        return None
    if normalized_ref.startswith("/"):
        normalized = posixpath.normpath(normalized_ref.lstrip("/"))
    else:
        normalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(page_path), normalized_ref)
        )
    normalized = normalized.lstrip("./")
    if normalized in name_set:
        return normalized
    return lower_map.get(normalized.lower())


# All known Canvas-template icon filenames with their human-readable labels.
# Ordered as they appear in the Sinclair e-Learn template guide.
_ICON_CATALOG: list[tuple[str, str]] = [
    ("star.png", "Introduction"),
    ("bullseye.png", "Module Objectives"),
    ("checkmark.png", "Module Checklist"),
    ("calendar.png", "Due Dates"),
    ("book.png", "Read"),
    ("headphones.png", "Listen"),
    ("video.png", "View"),
    ("bookmark.png", "View This"),
    ("folder.png", "Additional Resources"),
    ("circle-arrow.png", "Practice"),
    ("rocket.png", "Assessment"),
    ("pencil.png", "Instructions"),
    ("paper.png", "Do This"),
    ("exclamation.png", "Important"),
    ("info.png", "Information"),
    ("reminder.png", "Reminder"),
    ("flag.png", "Guidelines"),
    ("megaphone.png", "Announcement"),
    ("mail.png", "Communication"),
    ("question.png", "Help Links"),
    ("educator.png", "Instructor Information"),
    ("gear.png", "Technical Support"),
    ("download.png", "Download"),
    ("ai-brain.png", "AI Usage Allowed"),
]


def _build_icon_catalog(zip_path: Path) -> list[dict]:
    """Return label+data-URI for every icon that exists in *zip_path*.

    Each entry: ``{"basename": "book.png", "label": "Read", "data_uri": "data:..."}``
    Icons not present in the zip are still included but with ``data_uri: ""``.
    """
    catalog: list[dict] = []
    with ZipFile(zip_path, "r") as zf:
        name_set_lower = {n.lower() for n in zf.namelist()}
        for basename, label in _ICON_CATALOG:
            data_uri = ""
            candidate = f"templateassets/{basename.lower()}"
            # Find the actual cased path
            actual = next(
                (n for n in zf.namelist() if n.lower() == candidate),
                None,
            )
            if actual:
                mime_type, _ = mimetypes.guess_type(basename)
                if mime_type and mime_type.startswith("image/"):
                    try:
                        data = zf.read(actual)
                        encoded = base64.b64encode(data).decode("ascii")
                        data_uri = f"data:{mime_type};base64,{encoded}"
                    except KeyError:
                        pass
            catalog.append({"basename": basename, "label": label, "data_uri": data_uri})
    return catalog


def _build_preview_asset_map(
    *,
    zip_path: Path,
    page_path: str,
    body_html: str,
) -> dict[str, str]:
    refs = {
        match.group("src").strip()
        for match in _SRC_ATTR_RE.finditer(body_html)
        if match.group("src").strip()
    }
    if not refs:
        return {}

    asset_map: dict[str, str] = {}
    with ZipFile(zip_path, "r") as zf:
        name_set = {name for name in zf.namelist() if not name.endswith("/")}
        lower_map = {name.lower(): name for name in name_set}
        for raw_ref in sorted(refs):
            resolved = _resolve_local_asset_name(
                page_path=page_path,
                raw_ref=raw_ref,
                name_set=name_set,
                lower_map=lower_map,
            )
            if resolved is None:
                continue
            mime_type, _ = mimetypes.guess_type(resolved)
            if not mime_type or not mime_type.startswith("image/"):
                continue
            try:
                data = zf.read(resolved)
            except KeyError:
                continue
            encoded = base64.b64encode(data).decode("ascii")
            asset_map[raw_ref] = f"data:{mime_type};base64,{encoded}"
    return asset_map


def _banner_label(filename: str) -> str:
    """Turn a banner filename into a human-readable label."""
    name = filename.rsplit(".", 1)[0]  # strip extension
    name = name.replace("-", " ").replace("_", " ")
    # "banner 3" → "Banner 3", "banner building blue" → "Building Blue"
    parts = name.split()
    parts = [p for p in parts if p.lower() != "banner"]
    if not parts:
        return filename
    return " ".join(p.capitalize() for p in parts)


def _build_banner_catalog(zip_path: Path) -> dict[str, dict[str, str]]:
    """Load every banner image from the canvas zip as a base64 data URI.

    Returns a dict keyed by bare filename (e.g. ``"banner-3.png"``) with entries::

        {"raw_ref": "../TemplateAssets/banner-3.png",
         "data_uri": "data:image/png;base64,...",
         "label": "3"}
    """
    catalog: dict[str, dict[str, str]] = {}
    with ZipFile(zip_path, "r") as zf:
        for name in sorted(zf.namelist()):
            lower = name.lower()
            if "banner" not in lower:
                continue
            if not (
                lower.endswith(".png")
                or lower.endswith(".jpg")
                or lower.endswith(".jpeg")
            ):
                continue
            filename = name.split("/")[-1]
            raw_ref = f"../TemplateAssets/{filename}"
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type or not mime_type.startswith("image/"):
                continue
            try:
                data = zf.read(name)
            except KeyError:
                continue
            encoded = base64.b64encode(data).decode("ascii")
            catalog[filename] = {
                "raw_ref": raw_ref,
                "data_uri": f"data:{mime_type};base64,{encoded}",
                "label": _banner_label(filename),
            }
    return catalog


def _apply_preview_asset_map(body_html: str, asset_map: dict[str, str]) -> str:
    if not asset_map:
        return body_html

    def replace_src(match: re.Match[str]) -> str:
        raw_ref = match.group("src").strip()
        rewritten = asset_map.get(raw_ref)
        if not rewritten:
            return match.group(0)
        return f'{match.group("prefix")}{match.group("quote")}{rewritten}{match.group("quote")}'

    return _SRC_ATTR_RE.sub(replace_src, body_html)


def _metric_drift(original: dict[str, int], converted: dict[str, int]) -> list[str]:
    reasons: list[str] = []
    if original["image_count"] > 0 and converted["image_count"] == 0:
        reasons.append("Converted page removed all images from the original page.")
    elif abs(original["image_count"] - converted["image_count"]) >= 2:
        reasons.append(
            f"Image count changed {original['image_count']} -> {converted['image_count']}."
        )

    if abs(original["heading_count"] - converted["heading_count"]) >= 2:
        reasons.append(
            f"Heading count changed {original['heading_count']} -> {converted['heading_count']}."
        )

    if original["iframe_count"] != converted["iframe_count"]:
        reasons.append(
            f"Embedded iframe count changed {original['iframe_count']} -> {converted['iframe_count']}."
        )

    if original["table_count"] != converted["table_count"]:
        reasons.append(
            f"Table count changed {original['table_count']} -> {converted['table_count']}."
        )

    original_words = original["word_count"]
    converted_words = converted["word_count"]
    if original_words >= 60:
        delta_ratio = abs(converted_words - original_words) / max(original_words, 1)
        if delta_ratio >= 0.4:
            reasons.append(f"Word count changed {original_words} -> {converted_words}.")

    return reasons


def _visual_reasons(row: dict | None) -> list[str]:
    if not isinstance(row, dict):
        return []
    reasons: list[str] = []
    if row.get("duplicate_title_first_block"):
        reasons.append(
            "Converted page still repeats the title in the first content block."
        )
    if int(row.get("converted_shared_template_refs", 0) or 0) > 0:
        reasons.append(
            "Converted page still references shared Brightspace template assets."
        )
    if int(row.get("converted_title_tags", 0) or 0) > 0:
        reasons.append("Converted page still contains one or more <title> tags.")
    if int(row.get("converted_hr_nonstandard", 0) or 0) > 0:
        reasons.append("Converted page still contains nonstandard divider styling.")
    if int(row.get("converted_template_icons_missing_size_style", 0) or 0) > 0:
        reasons.append(
            "Converted page still contains template icons without standard sizing."
        )
    return reasons


def _migration_issue_index(payload: dict | None) -> dict[str, dict]:
    if not isinstance(payload, dict):
        return {}

    rows: dict[str, dict] = {}
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path or "::" in path:
            continue
        rows[path] = item
    return rows


def _visual_index(payload: dict | None) -> dict[str, dict]:
    if not isinstance(payload, dict):
        return {}
    rows: dict[str, dict] = {}
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if path:
            rows[path] = item
    return rows


def _priority(score: int, *, manual_count: int, accessibility_count: int) -> str:
    if manual_count >= 4 or accessibility_count >= 3 or score >= 10:
        return "high"
    if manual_count > 0 or accessibility_count > 0 or score >= 4:
        return "medium"
    return "low"


def _default_output_json(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.page-review.json")


def _default_output_markdown(output_json: Path) -> Path:
    return output_json.with_suffix(".md")


def _default_output_html(output_json: Path) -> Path:
    return output_json.with_suffix(".html")


def _default_draft_filename(converted_zip: Path) -> str:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return f"{stem}.review-draft.json"


def build_review_pack(
    *,
    original_zip: Path,
    converted_zip: Path,
    migration_report_json: Path | None = None,
    visual_audit_json: Path | None = None,
    output_json_path: Path | None = None,
    output_markdown_path: Path | None = None,
    output_html_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    original_html = _load_html_files(original_zip)
    converted_html = _load_html_files(converted_zip)
    editor_payloads: dict[str, dict[str, str]] = {}

    migration_report = _load_json(migration_report_json)
    visual_audit = _load_json(visual_audit_json)
    if not isinstance(visual_audit, dict):
        visual_audit = build_visual_audit(
            original_zip=original_zip, converted_zip=converted_zip
        )

    migration_index = _migration_issue_index(
        migration_report if isinstance(migration_report, dict) else None
    )
    visual_index = _visual_index(visual_audit)

    files: list[dict] = []
    for path in sorted(set(original_html) | set(converted_html)):
        original = original_html.get(path, "")
        converted = converted_html.get(path, "")
        fallback_title = Path(path).stem.replace("_", " ").strip()
        original_title = _extract_title(original, fallback_title)
        converted_title = _extract_title(converted, fallback_title)
        original_outline = _extract_heading_outline(original)
        converted_outline = _extract_heading_outline(converted)
        original_preview = _extract_preview_blocks(original)
        converted_preview = _extract_preview_blocks(converted)
        original_metrics = _content_metrics(original)
        converted_metrics = _content_metrics(converted)
        issue_row = migration_index.get(path, {})
        visual_row = visual_index.get(path, {})

        manual_issues = (
            issue_row.get("manual_review_issues", [])
            if isinstance(issue_row, dict)
            else []
        )
        if not isinstance(manual_issues, list):
            manual_issues = []
        accessibility_issues = (
            issue_row.get("accessibility_issues", [])
            if isinstance(issue_row, dict)
            else []
        )
        if not isinstance(accessibility_issues, list):
            accessibility_issues = []
        applied_changes = (
            issue_row.get("applied_changes", []) if isinstance(issue_row, dict) else []
        )
        if not isinstance(applied_changes, list):
            applied_changes = []

        structural_reasons = _metric_drift(original_metrics, converted_metrics)
        visual_reasons = _visual_reasons(
            visual_row if isinstance(visual_row, dict) else None
        )
        preview_similarity = round(
            SequenceMatcher(
                None,
                _normalize_text("\n".join(original_preview)),
                _normalize_text("\n".join(converted_preview)),
            ).ratio(),
            3,
        )

        score = (
            (len(manual_issues) * 4)
            + (len(accessibility_issues) * 3)
            + (len(visual_reasons) * 2)
            + len(structural_reasons)
        )
        if preview_similarity and preview_similarity < 0.55:
            score += 2
        elif preview_similarity and preview_similarity < 0.72:
            score += 1

        priority = _priority(
            score,
            manual_count=len(manual_issues),
            accessibility_count=len(accessibility_issues),
        )
        files.append(
            {
                "path": path,
                "priority": priority,
                "review_score": score,
                "titles": {
                    "original": original_title,
                    "converted": converted_title,
                },
                "preview_similarity": preview_similarity,
                "original_outline": original_outline,
                "converted_outline": converted_outline,
                "original_preview": original_preview,
                "converted_preview": converted_preview,
                "original_metrics": original_metrics,
                "converted_metrics": converted_metrics,
                "manual_review_issues": manual_issues,
                "accessibility_issues": accessibility_issues,
                "applied_changes": applied_changes,
                "structural_reasons": structural_reasons,
                "visual_reasons": visual_reasons,
            }
        )
        editor_payloads[path] = {
            "converted_body_html": _extract_body_html(converted),
        }

    files.sort(
        key=lambda row: (
            _PRIORITY_RANK.get(str(row.get("priority", "low")), 9),
            -int(row.get("review_score", 0) or 0),
            str(row.get("path", "")),
        )
    )

    summary = {
        "files_scanned": len(files),
        "files_with_high_priority_review": sum(
            1 for row in files if row.get("priority") == "high"
        ),
        "files_with_medium_priority_review": sum(
            1 for row in files if row.get("priority") == "medium"
        ),
        "files_with_manual_issues": sum(
            1 for row in files if row.get("manual_review_issues")
        ),
        "files_with_accessibility_issues": sum(
            1 for row in files if row.get("accessibility_issues")
        ),
        "files_with_visual_flags": sum(1 for row in files if row.get("visual_reasons")),
        "files_with_structural_drift": sum(
            1 for row in files if row.get("structural_reasons")
        ),
    }

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "original_zip": str(original_zip),
            "converted_zip": str(converted_zip),
            "migration_report_json": (
                str(migration_report_json) if migration_report_json is not None else ""
            ),
            "visual_audit_json": (
                str(visual_audit_json) if visual_audit_json is not None else ""
            ),
        },
        "summary": summary,
        "top_review_pages": files[:15],
        "files": files,
    }

    output_json = output_json_path or _default_output_json(converted_zip)
    output_markdown = output_markdown_path or _default_output_markdown(output_json)
    output_html = output_html_path or _default_output_html(output_json)

    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, output_markdown)
    _write_html(
        report,
        output_html,
        converted_zip=converted_zip,
        editor_payloads=editor_payloads,
    )
    return output_json, output_markdown, output_html


def _metric_cell(row: dict, key: str) -> str:
    return (
        str(((row.get("original_metrics") or {}).get(key, 0)))
        + " -> "
        + str(((row.get("converted_metrics") or {}).get(key, 0)))
    )


def _issue_reason_text(issue: dict) -> str:
    if not isinstance(issue, dict):
        return ""
    reason = str(issue.get("reason", "")).strip()
    evidence = str(issue.get("evidence", "")).strip()
    if reason and evidence:
        return f"{reason} [{evidence}]"
    return reason or evidence


def _write_markdown(report: dict, output_markdown: Path) -> None:
    summary = report.get("summary", {})
    lines = [
        "# Page Review Workbench",
        "",
        "## Summary",
        "",
        f"- Files scanned: {summary.get('files_scanned', 0)}",
        f"- High-priority review pages: {summary.get('files_with_high_priority_review', 0)}",
        f"- Medium-priority review pages: {summary.get('files_with_medium_priority_review', 0)}",
        f"- Pages with manual issues: {summary.get('files_with_manual_issues', 0)}",
        f"- Pages with accessibility issues: {summary.get('files_with_accessibility_issues', 0)}",
        f"- Pages with visual flags: {summary.get('files_with_visual_flags', 0)}",
        f"- Pages with structural drift: {summary.get('files_with_structural_drift', 0)}",
        "",
        "## Top Review Pages",
        "",
    ]
    for row in report.get("top_review_pages", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('path', '')}` | priority={row.get('priority', 'low')} | "
            f"score={row.get('review_score', 0)}"
        )
        manual_issues = row.get("manual_review_issues", [])
        accessibility_issues = row.get("accessibility_issues", [])
        visual_reasons = row.get("visual_reasons", [])
        structural_reasons = row.get("structural_reasons", [])
        if manual_issues:
            lines.append(
                "  - Manual: "
                + "; ".join(
                    filter(
                        None, (_issue_reason_text(item) for item in manual_issues[:3])
                    )
                )
            )
        if accessibility_issues:
            lines.append(
                "  - Accessibility: "
                + "; ".join(
                    filter(
                        None,
                        (_issue_reason_text(item) for item in accessibility_issues[:3]),
                    )
                )
            )
        if visual_reasons:
            lines.append(
                "  - Visual: " + "; ".join(str(item) for item in visual_reasons[:3])
            )
        if structural_reasons:
            lines.append(
                "  - Structure: "
                + "; ".join(str(item) for item in structural_reasons[:3])
            )
        converted_outline = row.get("converted_outline", [])
        if converted_outline:
            lines.append(
                "  - Converted outline: "
                + " | ".join(str(item) for item in converted_outline[:4])
            )
        lines.append("")
    output_markdown.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _badge(priority: str) -> str:
    colors = {"high": "#ac1a2f", "medium": "#d97706", "low": "#2563eb"}
    background = colors.get(priority, "#475569")
    return (
        f'<span class="badge" style="background:{background};">'
        f"{html.escape(priority.title())}</span>"
    )


def _render_issue_list(title: str, items: list[str]) -> str:
    if not items:
        return ""
    rendered = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f'<div class="issue-block"><h4>{html.escape(title)}</h4><ul>{rendered}</ul></div>'


def _editor_dom_id(path: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return normalized or "page-review-editor"


def _write_html(
    report: dict,
    output_html: Path,
    *,
    converted_zip: Path,
    editor_payloads: dict[str, dict[str, str]],
) -> None:
    summary = report.get("summary", {})
    cards = [
        ("Files scanned", summary.get("files_scanned", 0)),
        ("High priority", summary.get("files_with_high_priority_review", 0)),
        ("Medium priority", summary.get("files_with_medium_priority_review", 0)),
        ("Manual issue pages", summary.get("files_with_manual_issues", 0)),
        (
            "Accessibility issue pages",
            summary.get("files_with_accessibility_issues", 0),
        ),
        ("Visual flag pages", summary.get("files_with_visual_flags", 0)),
    ]
    draft_filename = _default_draft_filename(converted_zip)
    review_inputs_json = json.dumps(
        {
            "generated_utc": report.get("generated_utc", ""),
            "inputs": report.get("inputs", {}),
            "draft_filename": draft_filename,
        }
    )
    banner_catalog = _build_banner_catalog(converted_zip)
    banner_catalog_json = json.dumps(banner_catalog)
    banner_select_options = "".join(
        f'<option value="{html.escape(filename)}">{html.escape(entry["label"])}</option>'
        for filename, entry in sorted(banner_catalog.items(), key=lambda x: x[0])
    )
    banner_select_html = (
        f'<select class="banner-picker" data-banner-picker title="Swap page banner">'
        f'<option value="">— Banner —</option>'
        f"{banner_select_options}"
        f"</select>"
    )
    icon_catalog = _build_icon_catalog(converted_zip)
    icon_catalog_json = json.dumps(icon_catalog)
    icon_select_options = "".join(
        f'<option value="{html.escape(entry["basename"])}">{html.escape(entry["label"])}</option>'
        for entry in icon_catalog
    )
    icon_select_html = (
        f'<select class="icon-picker" data-icon-picker title="Change heading icon and label">'
        f'<option value="">— Icon —</option>'
        f"{icon_select_options}"
        f"</select>"
    )
    rows: list[str] = []
    for row in report.get("files", []):
        if not isinstance(row, dict):
            continue
        page_path = str(row.get("path", "")).strip()
        editor_payload = editor_payloads.get(page_path, {})
        raw_body_html = str(editor_payload.get("converted_body_html", "")).strip()
        asset_map = _build_preview_asset_map(
            zip_path=converted_zip,
            page_path=page_path,
            body_html=raw_body_html,
        )
        preview_body_html = _apply_preview_asset_map(raw_body_html, asset_map)
        editor_id = _editor_dom_id(page_path)
        manual_items = [
            _issue_reason_text(item)
            for item in row.get("manual_review_issues", [])
            if _issue_reason_text(item)
        ]
        accessibility_items = [
            _issue_reason_text(item)
            for item in row.get("accessibility_issues", [])
            if _issue_reason_text(item)
        ]
        change_items = [
            str(item.get("description", "")).strip()
            for item in row.get("applied_changes", [])
            if isinstance(item, dict) and str(item.get("description", "")).strip()
        ]
        _conv_m = row.get("converted_metrics") or {}
        rows.append(
            f"""
            <section class="page-card" data-page-name="{html.escape(page_path, quote=True)}" data-priority="{html.escape(str(row.get('priority', 'low')), quote=True)}" data-has-images="{'1' if _conv_m.get('image_count', 0) > 0 else '0'}" data-has-accordions="{'1' if _conv_m.get('accordion_count', 0) > 0 else '0'}" data-has-tables="{'1' if _conv_m.get('table_count', 0) > 0 else '0'}" data-has-iframes="{'1' if _conv_m.get('iframe_count', 0) > 0 else '0'}">
              <div class="page-head">
                <div>
                  <h2>{html.escape(page_path)}</h2>
                  <p class="title-row">{html.escape(str(((row.get("titles") or {}).get("converted", ""))))}</p>
                </div>
                <div class="page-meta">
                  {_badge(str(row.get("priority", "low")))}
                  <span class="score">Score {int(row.get("review_score", 0) or 0)}</span>
                </div>
              </div>
              <div class="metrics">
                <div><strong>Headings</strong><span>{html.escape(_metric_cell(row, "heading_count"))}</span></div>
                <div><strong>Images</strong><span>{html.escape(_metric_cell(row, "image_count"))}</span></div>
                <div><strong>Accordions</strong><span>{html.escape(_metric_cell(row, "accordion_count"))}</span></div>
                <div><strong>Iframes</strong><span>{html.escape(_metric_cell(row, "iframe_count"))}</span></div>
                <div><strong>Tables</strong><span>{html.escape(_metric_cell(row, "table_count"))}</span></div>
                <div><strong>Lists</strong><span>{html.escape(_metric_cell(row, "list_count"))}</span></div>
                <div><strong>Words</strong><span>{html.escape(_metric_cell(row, "word_count"))}</span></div>
              </div>
              <div class="issue-grid">
                {_render_issue_list("Manual Review", manual_items[:5])}
                {_render_issue_list("Accessibility", accessibility_items[:5])}
                {_render_issue_list("Visual Flags", [str(item) for item in row.get("visual_reasons", [])[:5]])}
                {_render_issue_list("Structural Drift", [str(item) for item in row.get("structural_reasons", [])[:5]])}
                {_render_issue_list("Deterministic Changes Applied", change_items[:5])}
              </div>
              <div class="compare-grid">
                <div class="compare-column">
                  <h3>Original Outline</h3>
                  <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in row.get("original_outline", [])[:8]) or "<li>No heading outline extracted.</li>"}</ul>
                  <h3>Original Preview</h3>
                  <pre>{html.escape(chr(10).join(row.get("original_preview", [])))}</pre>
                </div>
                <div class="compare-column">
                  <h3>Converted Outline</h3>
                  <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in row.get("converted_outline", [])[:8]) or "<li>No heading outline extracted.</li>"}</ul>
                  <h3>Converted Preview</h3>
                  <pre>{html.escape(chr(10).join(row.get("converted_preview", [])))}</pre>
                </div>
              </div>
              <div class="editor-shell" id="{editor_id}" data-page-path="{html.escape(page_path, quote=True)}" data-page-title="{html.escape(str(((row.get("titles") or {}).get("converted", ""))), quote=True)}">
                <div class="editor-header">
                  <div>
                    <h3>Approval Editor</h3>
                    <p class="editor-note">Edit the Canvas body HTML inline. Use <strong>Export Review Draft</strong> to save approved changes for write-back.</p>
                  </div>
                </div>
                <div class="editor-toolbar">
                  <div class="toolbar-group">
                    <span class="toolbar-label">Text</span>
                    <button type="button" data-editor-command="bold" title="Bold">B</button>
                    <button type="button" data-editor-command="italic" title="Italic"><em>I</em></button>
                    <button type="button" data-editor-command="insertUnorderedList" title="Bullet list">&#8226; List</button>
                    <button type="button" data-editor-command="insertOrderedList" title="Numbered list">1. List</button>
                    <button type="button" data-editor-block="h2" title="Heading 2">H2</button>
                    <button type="button" data-editor-block="h3" title="Heading 3">H3</button>
                  </div>
                  <div class="toolbar-group">
                    <span class="toolbar-label">Media</span>
                    <button type="button" data-editor-image-size="320" title="Resize image to 320 px">320</button>
                    <button type="button" data-editor-image-size="480" title="Resize image to 480 px">480</button>
                    <button type="button" data-editor-image-size="640" title="Resize image to 640 px">640</button>
                    <button type="button" data-editor-image-size="full" title="Full-width image">Full</button>
                    <button type="button" data-editor-image-align="left" title="Align image left">&#8592; Left</button>
                    <button type="button" data-editor-image-align="center" title="Align image center">Center</button>
                    <button type="button" data-editor-image-align="right" title="Align image right">Right &#8594;</button>
                    <button type="button" data-editor-image-wrap="left" title="Float image left">Wrap &#8592;</button>
                    <button type="button" data-editor-image-wrap="right" title="Float image right">Wrap &#8594;</button>
                    <select data-image-gap title="Spacing around selected image (gap when wrapped, padding when not wrapped)">
                      <option value="">&#8644; Gap</option>
                      <option value="0px">No gap</option>
                      <option value="8px">Small (8px)</option>
                      <option value="16px">Medium (16px)</option>
                      <option value="24px">Large (24px)</option>
                      <option value="32px">XL (32px)</option>
                    </select>
                    <button type="button" data-editor-image-clear title="Reset image styles">&#10006; Reset</button>
                  </div>
                  <div class="toolbar-group">
                    <span class="toolbar-label">Icon</span>
                    {icon_select_html}
                  </div>
                  <div class="toolbar-group">
                    <span class="toolbar-label">Page</span>
                    {banner_select_html}
                    <select class="accordion-mode-picker" data-accordion-mode title="Convert accordions on this page">
                      <option value="">— Accordions —</option>
                      <option value="flatten">Flatten to headings</option>
                      <option value="details">Convert to Details</option>
                      <option value="align-left">Align left</option>
                      <option value="align-center">Align center</option>
                    </select>
                  </div>
                  <div class="toolbar-group toolbar-group--history">
                    <span class="toolbar-label">History</span>
                    <button type="button" data-editor-undo title="Undo last change">&#8630; Undo</button>
                    <button type="button" data-editor-reset title="Reset page to original converted HTML">&#10226; Reset all</button>
                    <button type="button" data-editor-toggle-source title="Toggle raw HTML source">Source</button>
                    <button type="button" data-editor-copy title="Copy body HTML to clipboard">Copy</button>
                  </div>
                </div>
                <div class="editor-surface" contenteditable="true">{preview_body_html}</div>
                <textarea class="editor-source is-hidden" spellcheck="false">{html.escape(raw_body_html)}</textarea>
                <textarea class="editor-initial-source is-hidden" spellcheck="false">{html.escape(raw_body_html)}</textarea>
                <script type="application/json" class="editor-asset-map">{json.dumps(asset_map)}</script>
              </div>
            </section>
            """
        )

    card_html = "".join(
        f'<div class="summary-card"><span>{html.escape(label)}</span><strong>{value}</strong></div>'
        for label, value in cards
    )
    document = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Page Review Workbench</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700&display=swap">
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f2e8;
      --panel: #fffdfa;
      --ink: #1f2937;
      --muted: #5b6472;
      --line: #d9cfbe;
      --accent: #ac1a2f;
    }}
    body {{
      margin: 0;
      padding: 24px;
      background: linear-gradient(180deg, #f4efe5 0%, #f9f7f2 100%);
      color: var(--ink);
      font: 15px/1.5 "Avenir Next", "Segoe UI", sans-serif;
    }}
    h1, h2, h3, h4 {{
      margin: 0 0 8px;
      font-family: "Avenir Next Condensed", "Segoe UI Semibold", sans-serif;
    }}
    .page {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    .intro {{
      margin-bottom: 20px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 16px 0 24px;
    }}
    .filter-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin: 0 0 18px;
      padding: 12px 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
    }}
    .page-search {{
      padding: 7px 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      font: inherit;
      min-width: 220px;
      background: #fff;
      color: var(--ink);
    }}
    .filter-chips {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}
    .chip {{
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 999px;
      padding: 5px 12px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      color: var(--ink);
    }}
    .chip.is-active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .filter-sep {{
      color: var(--line);
      font-size: 18px;
      padding: 0 4px;
    }}
    .filter-count {{
      color: var(--muted);
      font-size: 13px;
      margin-left: auto;
    }}
    /* Collapsed page cards — hide detail panels, keep head + metrics visible */
    .page-card.is-collapsed .compare-grid,
    .page-card.is-collapsed .issue-grid,
    .page-card.is-collapsed .editor-shell {{
      display: none;
    }}
    .page-head {{
      cursor: pointer;
      user-select: none;
    }}
    .page-head:hover {{
      background: rgba(0,0,0,.02);
      border-radius: 8px;
    }}
    .summary-card, .page-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 6px 18px rgba(89, 72, 39, 0.08);
    }}
    .summary-card {{
      padding: 14px 16px;
    }}
    .summary-card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
    }}
    .summary-card strong {{
      display: block;
      font-size: 28px;
      margin-top: 6px;
    }}
    .page-card {{
      padding: 18px;
      margin-bottom: 18px;
    }}
    .page-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 12px;
    }}
    .title-row {{
      color: var(--muted);
      margin: 0;
    }}
    .page-meta {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .badge {{
      color: white;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 999px;
      letter-spacing: 0.02em;
    }}
    .score {{
      color: var(--muted);
      font-size: 13px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}
    .metrics div {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      background: #fff;
    }}
    .metrics strong {{
      display: block;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .issue-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .issue-block {{
      border-left: 4px solid var(--accent);
      padding: 10px 12px;
      background: #fff8f8;
      border-radius: 8px;
    }}
    .issue-block ul {{
      margin: 8px 0 0 18px;
      padding: 0;
    }}
    .compare-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 12px;
    }}
    .compare-column {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #faf7f0;
      border-radius: 10px;
      padding: 12px;
      margin: 8px 0 0;
      font: 13px/1.45 "SFMono-Regular", Consolas, monospace;
    }}
    ul {{
      margin: 8px 0 0 18px;
      padding: 0;
    }}
    .editor-shell {{
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: #fcfaf4;
    }}
    .editor-header {{
      margin-bottom: 6px;
    }}
    .editor-note {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .editor-toolbar {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      border-radius: 12px 12px 0 0;
      padding: 6px 14px 8px;
      margin: 0 -14px 12px -14px;
      box-shadow: 0 2px 6px rgba(0,0,0,.06);
      display: flex;
      flex-wrap: wrap;
      gap: 6px 12px;
      align-items: flex-start;
    }}
    .toolbar-group {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 4px;
      padding: 4px 8px 4px 6px;
      border-right: 1px solid var(--line);
    }}
    .toolbar-group:last-child {{
      border-right: none;
    }}
    .toolbar-group--history {{
      margin-left: auto;
      border-right: none;
    }}
    .toolbar-label {{
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-right: 2px;
      align-self: center;
    }}
    .editor-toolbar button {{
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 4px;
      padding: 5px 10px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      white-space: nowrap;
    }}
    .editor-toolbar button:hover {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    .editor-toolbar select {{
      font: inherit;
      font-size: 12px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      cursor: pointer;
    }}
    .editor-surface {{
      min-height: 220px;
      max-height: 65vh;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 20px 24px;
      overflow-y: auto;
      /* Canvas-like typography */
      font-family: "Lato", "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.6;
      color: #2d3b45;
    }}
    .editor-surface:focus {{
      outline: 2px solid rgba(172, 26, 47, 0.25);
      border-color: var(--accent);
    }}
    .editor-surface img {{
      max-width: 100%;
      height: auto;
    }}
    .editor-surface img.is-selected,
    .editor-surface video.is-selected,
    .editor-surface iframe.is-selected {{
      outline: 3px solid rgba(172, 26, 47, 0.4);
      outline-offset: 3px;
    }}
    /* Disable pointer capture inside iframes/videos so clicks reach the
       contenteditable surface and the media selection handler fires. */
    .editor-surface iframe,
    .editor-surface video {{
      pointer-events: none;
    }}
    /* Banner images bleed edge-to-edge (compensates for surface padding) */
    .editor-surface img[src*="banner"] {{
      margin: 0 -24px;
      width: calc(100% + 48px);
      max-width: none;
      display: block;
    }}
    /* Canvas heading styles */
    .editor-surface h2 {{
      color: #ac1a2f;
      border-bottom: 10px solid #AC1A2F;
      padding: 10px;
      font-size: 1.5em;
      margin: 1em 0 0.5em;
    }}
    .editor-surface h3 {{
      color: #ac1a2f;
      border-bottom: 2px solid #cccccc;
      padding: 5px 0;
      font-size: 1.2em;
      margin: 1em 0 0.5em;
    }}
    .editor-surface h4 {{
      font-size: 1.05em;
      margin: 0.8em 0 0.4em;
      color: #2d3b45;
    }}
    .editor-surface a {{
      color: #0770a3;
      text-decoration: underline;
    }}
    .editor-surface hr {{
      border: none;
      border-top: 8px solid #AC1A2F;
      margin: 1.5em 0;
    }}
    .editor-surface p {{
      margin: 0 0 0.75em;
    }}
    .editor-surface ul, .editor-surface ol {{
      margin: 0 0 0.75em 1.5em;
      padding: 0;
    }}
    .editor-surface table {{
      border-collapse: collapse;
      width: 100%;
      margin-bottom: 1em;
    }}
    .editor-surface td, .editor-surface th {{
      border: 1px solid #c7cfd4;
      padding: 8px 10px;
    }}
    .editor-surface th {{
      background: #f5f5f5;
      font-weight: 600;
    }}
    .editor-source {{
      width: 100%;
      min-height: 180px;
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      font: 13px/1.45 "SFMono-Regular", Consolas, monospace;
      background: #fff;
      box-sizing: border-box;
    }}
    .is-hidden {{
      display: none;
    }}
    .page-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 12px;
    }}
    .draft-button {{
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 999px;
      padding: 10px 16px;
      cursor: pointer;
      font: inherit;
    }}
    .draft-status {{
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      body {{
        padding: 16px;
      }}
      .page-head {{
        flex-direction: column;
      }}
      .editor-header {{
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="intro">
      <h1>Page Review Workbench</h1>
      <p>Deterministic before/after review plus a lightweight local editor for top-priority Canvas page bodies. Focus on the pages at the top of this list first, then export a review draft for write-back in the app.</p>
      <div class="page-actions">
        <button type="button" class="draft-button" data-export-draft>Export Review Draft</button>
        <span class="draft-status" data-draft-status>No draft exported yet.</span>
      </div>
    </section>
    <section class="summary-grid">{card_html}</section>
    <div class="filter-bar" id="page-filter-bar">
      <input type="search" class="page-search" placeholder="Filter pages by name…" aria-label="Filter pages by name">
      <div class="filter-chips">
        <button class="chip chip-priority is-active" data-filter-priority="all">All priorities</button>
        <button class="chip chip-priority" data-filter-priority="high">High</button>
        <button class="chip chip-priority" data-filter-priority="medium">Medium</button>
        <button class="chip chip-priority" data-filter-priority="low">Low</button>
        <span class="filter-sep">|</span>
        <button class="chip" data-filter-content="images">Has Images</button>
        <button class="chip" data-filter-content="accordions">Has Accordions</button>
        <button class="chip" data-filter-content="tables">Has Tables</button>
        <button class="chip" data-filter-content="iframes">Has Iframes</button>
      </div>
      <span class="filter-count" data-filter-count></span>
    </div>
    {''.join(rows)}
  </main>
  <script type="application/json" id="review-inputs">{review_inputs_json}</script>
  <script type="application/json" id="banner-catalog">{banner_catalog_json}</script>
  <script type="application/json" id="icon-catalog">{icon_catalog_json}</script>
  <script>
    (() => {{
      function reviewInputs() {{
        const raw = document.getElementById('review-inputs')?.textContent || '{{}}';
        try {{
          return JSON.parse(raw);
        }} catch (error) {{
          return {{}};
        }}
      }}

      function parseAssetMap(shell) {{
        const raw = shell.querySelector('.editor-asset-map')?.textContent || '{{}}';
        try {{
          return JSON.parse(raw);
        }} catch (error) {{
          return {{}};
        }}
      }}

      function bannerCatalog() {{
        const raw = document.getElementById('banner-catalog')?.textContent || '{{}}';
        try {{
          return JSON.parse(raw);
        }} catch (error) {{
          return {{}};
        }}
      }}

      function iconCatalog() {{
        const raw = document.getElementById('icon-catalog')?.textContent || '[]';
        try {{
          return JSON.parse(raw);
        }} catch (error) {{
          return [];
        }}
      }}

      function getSurface(shell) {{
        return shell.querySelector('.editor-surface');
      }}

      // ── Per-shell undo stack (max 20 snapshots) ───────────────────────────
      const undoStacks = new WeakMap();
      function getUndoStack(shell) {{
        if (!undoStacks.has(shell)) undoStacks.set(shell, []);
        return undoStacks.get(shell);
      }}
      function pushUndo(shell) {{
        const surface = getSurface(shell);
        if (!surface) return;
        const stack = getUndoStack(shell);
        stack.push(surface.innerHTML);
        if (stack.length > 20) stack.shift();
      }}
      function popUndo(shell) {{
        const surface = getSurface(shell);
        if (!surface) return;
        const stack = getUndoStack(shell);
        if (!stack.length) return;
        surface.innerHTML = stack.pop();
        clearSelectedImages(surface);
        syncSource(shell);
      }}

      // Persist the last known selection per shell so toolbar buttons can restore
      // it after the brief focus-loss that some browsers (e.g. Brave) cause.
      const savedRanges = new WeakMap();
      document.addEventListener('selectionchange', () => {{
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        const range = sel.getRangeAt(0);
        document.querySelectorAll('.editor-shell').forEach((shell) => {{
          const surface = getSurface(shell);
          if (surface && surface.contains(range.commonAncestorContainer)) {{
            savedRanges.set(shell, range.cloneRange());
          }}
        }});
      }});

      function previewToRaw(htmlText, assetMap) {{
        let updated = htmlText;
        const entries = Object.entries(assetMap).sort((left, right) => right[1].length - left[1].length);
        for (const [rawRef, previewRef] of entries) {{
          updated = updated.split(previewRef).join(rawRef);
        }}
        return updated;
      }}

      function rawToPreview(htmlText, assetMap) {{
        let updated = htmlText;
        const entries = Object.entries(assetMap).sort((left, right) => right[0].length - left[0].length);
        for (const [rawRef, previewRef] of entries) {{
          updated = updated.split(rawRef).join(previewRef);
        }}
        return updated;
      }}

      function syncSource(shell) {{
        const surface = getSurface(shell);
        const source = shell.querySelector('.editor-source');
        const assetMap = parseAssetMap(shell);
        if (surface) source.value = previewToRaw(surface.innerHTML, assetMap);
      }}

      function applyAccordionMode(shell, mode) {{
        const surface = getSurface(shell);
        if (!surface) return;
        pushUndo(shell);
        const details = Array.from(surface.querySelectorAll('details'));
        if (mode === 'flatten') {{
          details.forEach((det) => {{
            const summary = det.querySelector('summary');
            const title = summary ? summary.textContent.trim() : '';
            const frag = document.createDocumentFragment();
            if (title) {{
              const h = document.createElement('h3');
              h.textContent = title;
              frag.appendChild(h);
            }}
            Array.from(det.childNodes).forEach((child) => {{
              if (child !== summary) frag.appendChild(child.cloneNode(true));
            }});
            det.parentNode.replaceChild(frag, det);
          }});
        }} else if (mode === 'details') {{
          // Flatten heading+body pairs back into <details> blocks
          const blocks = [];
          let i = 0;
          const children = Array.from(surface.children);
          while (i < children.length) {{
            const node = children[i];
            if (/^h[23]$/i.test(node.nodeName)) {{
              const heading = node;
              const bodyNodes = [];
              i++;
              while (i < children.length && !/^h[1-6]$/i.test(children[i].nodeName)) {{
                bodyNodes.push(children[i]);
                i++;
              }}
              const det = document.createElement('details');
              det.setAttribute('open', '');
              const sum = document.createElement('summary');
              sum.textContent = heading.textContent;
              det.appendChild(sum);
              bodyNodes.forEach((n) => det.appendChild(n.cloneNode(true)));
              blocks.push({{ original: [heading, ...bodyNodes], replacement: det }});
            }} else {{
              i++;
            }}
          }}
          blocks.forEach(({{original, replacement}}) => {{
            original[0].parentNode.insertBefore(replacement, original[0]);
            original.forEach((n) => n.parentNode?.removeChild(n));
          }});
        }} else if (mode === 'align-left' || mode === 'align-center') {{
          const align = mode === 'align-center' ? 'center' : 'left';
          details.forEach((det) => {{
            det.style.textAlign = align;
          }});
          if (details.length === 0) {{
            surface.querySelectorAll('h3, h2').forEach((h) => {{
              h.style.textAlign = align;
              let sib = h.nextElementSibling;
              while (sib && !/^h[1-6]$/i.test(sib.nodeName)) {{
                sib.style.textAlign = align;
                sib = sib.nextElementSibling;
              }}
            }});
          }}
        }}
        const sel = shell.querySelector('[data-accordion-mode]');
        if (sel) sel.value = '';
        syncSource(shell);
      }}

      function execBlock(shell, blockTag) {{
        const surface = getSurface(shell);
        if (!surface) return;
        pushUndo(shell);
        surface.focus();
        const saved = savedRanges.get(shell);
        if (saved) {{
          const s = window.getSelection();
          s.removeAllRanges();
          s.addRange(saved);
        }}
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        // Walk up from anchor node to find the direct child of surface
        let node = sel.getRangeAt(0).startContainer;
        if (node.nodeType === Node.TEXT_NODE) node = node.parentNode;
        while (node && node.parentNode !== surface) node = node.parentNode;
        if (!node || node === surface) {{
          // Couldn't locate a containing block — leave as-is
          return;
        }}
        // Swap the tag in-place, preserving innerHTML and style/class attrs
        const newBlock = document.createElement(blockTag);
        newBlock.innerHTML = node.innerHTML;
        for (const attr of node.attributes) {{
          if (attr.name === 'style' || attr.name === 'class') {{
            newBlock.setAttribute(attr.name, attr.value);
          }}
        }}
        node.parentNode.replaceChild(newBlock, node);
        // Leave cursor at end of the new block
        const newRange = document.createRange();
        newRange.selectNodeContents(newBlock);
        newRange.collapse(false);
        sel.removeAllRanges();
        sel.addRange(newRange);
        syncSource(shell);
      }}

      function execCommand(shell, command) {{
        const surface = getSurface(shell);
        if (!surface) return;
        pushUndo(shell);
        surface.focus();
        const saved = savedRanges.get(shell);
        if (saved) {{
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(saved);
        }}
        document.execCommand(command, false, null);
        syncSource(shell);
      }}

      function clearSelectedImages(scope) {{
        scope.querySelectorAll('img.is-selected, video.is-selected, iframe.is-selected').forEach((el) => el.classList.remove('is-selected'));
      }}

      function selectedImage(shell) {{
        return getSurface(shell)?.querySelector('img.is-selected');
      }}

      function selectedMedia(shell) {{
        return getSurface(shell)?.querySelector('img.is-selected, video.is-selected, iframe.is-selected') || null;
      }}

      function applyImagePreset(shell, size) {{
        const media = selectedMedia(shell);
        if (!media) {{
          return false;
        }}
        pushUndo(shell);
        media.removeAttribute('align');
        media.style.float = 'none';
        media.style.clear = 'both';
        media.style.display = 'block';
        media.style.maxWidth = '100%';
        if (media.tagName === 'IFRAME') {{
          const currW = parseFloat(media.getAttribute('width') || media.style.width) || 560;
          const currH = parseFloat(media.getAttribute('height') || media.style.height) || 315;
          const ratio = currH / currW;
          const targetW = size === 'full' ? 560 : Number(size);
          media.style.width = size === 'full' ? '100%' : `${{size}}px`;
          media.style.height = `${{Math.round(targetW * ratio)}}px`;
        }} else {{
          media.style.height = 'auto';
          media.style.width = size === 'full' ? '100%' : `${{size}}px`;
        }}
        syncSource(shell);
        return true;
      }}

      function applyImageAlignment(shell, alignment) {{
        const media = selectedMedia(shell);
        if (!media) {{
          return false;
        }}
        pushUndo(shell);
        media.removeAttribute('align');
        media.style.float = 'none';
        media.style.clear = 'both';
        media.style.display = 'block';
        media.style.maxWidth = '100%';
        if (media.tagName !== 'IFRAME') media.style.height = 'auto';
        if (alignment === 'left') {{
          media.style.margin = '16px auto 16px 0';
        }} else if (alignment === 'right') {{
          media.style.margin = '16px 0 16px auto';
        }} else {{
          media.style.margin = '16px auto';
        }}
        syncSource(shell);
        return true;
      }}

      function clearImageFormatting(shell) {{
        const media = selectedMedia(shell);
        if (!media) {{
          return false;
        }}
        pushUndo(shell);
        media.removeAttribute('align');
        if (media.tagName === 'IFRAME') {{
          media.style.cssText = 'display:block; width:100%; margin:16px auto; float:none; clear:both;';
        }} else {{
          media.removeAttribute('width');
          media.removeAttribute('height');
          media.style.cssText = 'display:block; max-width:100%; height:auto; margin:16px auto; float:none; clear:both;';
        }}
        clearSelectedImages(getSurface(shell));
        syncSource(shell);
        return true;
      }}

      function applyImageWrap(shell, direction) {{
        const media = selectedMedia(shell);
        if (!media) {{
          return false;
        }}
        pushUndo(shell);
        const width = media.style.width && media.style.width !== '100%' ? media.style.width : '320px';
        media.removeAttribute('align');
        media.style.clear = 'none';
        media.style.display = 'block';
        if (media.tagName !== 'IFRAME') media.style.height = 'auto';
        media.style.width = width;
        media.style.maxWidth = '45%';
        media.style.float = direction;
        media.style.margin = direction === 'right' ? '0 0 16px 16px' : '0 16px 16px 0';
        syncSource(shell);
        return true;
      }}

      function applyImageGap(shell, px) {{
        const media = selectedMedia(shell);
        if (!media) return false;
        pushUndo(shell);
        const floatDir = media.style.float;
        if (floatDir === 'left') {{
          // Wrapped-left: gap on the right side (facing text) + bottom spacing
          media.style.margin = `0 ${{px}} 16px 0`;
        }} else if (floatDir === 'right') {{
          // Wrapped-right: gap on the left side (facing text) + bottom spacing
          media.style.margin = `0 0 16px ${{px}}`;
        }} else {{
          // Not wrapped: apply as uniform padding around the image
          media.style.padding = px;
        }}
        syncSource(shell);
        return true;
      }}

      function resetEditor(shell) {{
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');
        const initialSource = shell.querySelector('.editor-initial-source')?.value || '';
        // Do not push undo here — reset clears the stack entirely
        undoStacks.set(shell, []);
        if (surface) surface.innerHTML = rawToPreview(initialSource, parseAssetMap(shell));
        if (surface) clearSelectedImages(surface);
        source.value = initialSource;
        syncSource(shell);
      }}

      function applyIconChange(shell, basename) {{
        // Locate the first icon heading in the editor that has a templateassets img
        const surface = getSurface(shell);
        if (!surface) return false;
        const catalog = iconCatalog();
        const entry = catalog.find((e) => e.basename === basename);
        if (!entry) return false;
        const headings = Array.from(surface.querySelectorAll('h1, h2, h3, h4, h5, h6'));
        // Prefer the heading that already contains a templateassets icon img
        let targetHeading = headings.find((h) => h.querySelector('img[src*="TemplateAssets"], img[src*="templateassets"]'));
        if (!targetHeading) {{
          // Fall back to the heading nearest the current cursor/selection
          const sel = surface.ownerDocument.getSelection();
          if (sel && sel.rangeCount) {{
            const anchorNode = sel.getRangeAt(0).startContainer;
            const anchorEl = anchorNode.nodeType === 1 ? anchorNode : anchorNode.parentElement;
            // Walk up from cursor — are we already inside a heading?
            let cursorHeading = anchorEl.closest('h1,h2,h3,h4,h5,h6');
            if (!cursorHeading) {{
              // Find the last heading that comes before the cursor in document order
              cursorHeading = [...headings].reverse().find((h) =>
                (h.compareDocumentPosition(anchorEl) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
              );
            }}
            targetHeading = cursorHeading || headings[0];
          }} else {{
            targetHeading = headings[0];
          }}
        }}
        if (!targetHeading) return false;
        pushUndo(shell);
        const iconImg = targetHeading.querySelector('img[src*="TemplateAssets"], img[src*="templateassets"]');
        const assetMap = parseAssetMap(shell);
        // Build a raw ref + preview src for the new icon
        const rawRef = `../TemplateAssets/${{basename}}`;
        const previewSrc = entry.data_uri || rawRef;
        if (iconImg) {{
          // Swap the existing icon src and update alt text
          iconImg.src = previewSrc || iconImg.src;
          iconImg.alt = entry.label;
          if (entry.data_uri) {{
            assetMap[rawRef] = entry.data_uri;
            const assetMapEl = shell.querySelector('.editor-asset-map');
            if (assetMapEl) assetMapEl.textContent = JSON.stringify(assetMap);
          }}
        }} else {{
          // No existing icon — prepend one
          const newImg = document.createElement('img');
          newImg.src = previewSrc;
          newImg.alt = entry.label;
          newImg.style.cssText = 'width:45px; height:auto; vertical-align:middle; margin-right:8px;';
          targetHeading.insertBefore(newImg, targetHeading.firstChild);
          if (entry.data_uri) {{
            assetMap[rawRef] = entry.data_uri;
            const assetMapEl = shell.querySelector('.editor-asset-map');
            if (assetMapEl) assetMapEl.textContent = JSON.stringify(assetMap);
          }}
        }}
        // Ensure heading has a <strong> label; create one if missing
        let strong = targetHeading.querySelector('strong');
        if (strong) {{
          strong.textContent = entry.label;
        }} else {{
          strong = document.createElement('strong');
          strong.textContent = entry.label;
          const theImg = targetHeading.querySelector('img');
          if (theImg) {{
            theImg.insertAdjacentElement('afterend', strong);
          }} else {{
            targetHeading.appendChild(strong);
          }}
        }}
        syncSource(shell);
        return true;
      }}

      async function copyHtml(shell) {{
        const source = shell.querySelector('.editor-source');
        syncSource(shell);
        try {{
          await navigator.clipboard.writeText(source.value);
        }} catch (error) {{
          source.classList.remove('is-hidden');
          source.focus();
          source.select();
        }}
      }}

      function draftPayload() {{
        const inputs = reviewInputs();
        const pages = [];
        document.querySelectorAll('.editor-shell').forEach((shell) => {{
          syncSource(shell);
          const source = shell.querySelector('.editor-source');
          const initialSource = shell.querySelector('.editor-initial-source')?.value || '';
          if (source.value.trim() === initialSource.trim()) {{
            return;
          }}
          pages.push({{
            path: shell.getAttribute('data-page-path') || '',
            title: shell.getAttribute('data-page-title') || '',
            original_body_html: initialSource,
            edited_body_html: source.value,
          }});
        }});
        return {{
          version: 1,
          generated_utc: new Date().toISOString(),
          source: inputs,
          pages,
        }};
      }}

      function exportDraft() {{
        const payload = draftPayload();
        const status = document.querySelector('[data-draft-status]');
        if (!payload.pages.length) {{
          if (status) {{
            status.textContent = 'No changed pages to export yet.';
          }}
          return;
        }}
        const draftName = reviewInputs().draft_filename || 'review-draft.json';
        const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = draftName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        if (status) {{
          status.textContent = 'Exported ' + payload.pages.length + ' edited page(s) as ' + draftName + '.';
        }}
      }}

      // Prevent toolbar buttons from stealing editor focus (preserves selection for execCommand)
      document.addEventListener('mousedown', (event) => {{
        if (event.target.closest(
          'button[data-editor-command], button[data-editor-block], ' +
          'button[data-editor-image-size], button[data-editor-image-align], ' +
          'button[data-editor-image-wrap], button[data-editor-image-clear], ' +
          'button[data-editor-toggle-source], button[data-editor-reset], button[data-editor-undo], button[data-editor-copy]'
        )) {{
          event.preventDefault();
        }}
      }}, true);

      document.querySelectorAll('.editor-shell').forEach((shell) => {{
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');

        // Media click: highlight selected image, video, or iframe.
        // Note: iframes/videos have pointer-events:none so event.target will
        // be whatever element is behind them — closest() won't find them.
        // Fall back to a bounding-rect hit test for those elements.
        surface.addEventListener('mousedown', (event) => {{
          let clickedMedia = event.target.closest('img, video, iframe');
          if (!clickedMedia) {{
            const x = event.clientX, y = event.clientY;
            clickedMedia = Array.from(surface.querySelectorAll('iframe, video')).find((el) => {{
              const r = el.getBoundingClientRect();
              return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
            }}) || null;
          }}
          if (!clickedMedia) return;
          clearSelectedImages(surface);
          clickedMedia.classList.add('is-selected');
        }});

        // Source textarea edits → update editor surface
        source.addEventListener('input', () => {{
          surface.innerHTML = rawToPreview(source.value, parseAssetMap(shell));
          clearSelectedImages(surface);
        }});

        // Pre-select the current banner variant in the picker (if any)
        const bannerSelect = shell.querySelector('[data-banner-picker]');
        if (bannerSelect) {{
          const catalog = bannerCatalog();
          const assetMapData = parseAssetMap(shell);
          for (const [filename, entry] of Object.entries(catalog)) {{
            if (assetMapData[entry.raw_ref]) {{
              bannerSelect.value = filename;
              break;
            }}
          }}
        }}

        syncSource(shell);
      }});

      document.addEventListener('click', (event) => {{
        const exportButton = event.target.closest('[data-export-draft]');
        if (exportButton) {{
          exportDraft();
          return;
        }}

        const commandButton = event.target.closest('[data-editor-command]');
        if (commandButton) {{
          execCommand(commandButton.closest('.editor-shell'), commandButton.getAttribute('data-editor-command'));
          return;
        }}

        const blockButton = event.target.closest('[data-editor-block]');
        if (blockButton) {{
          execBlock(blockButton.closest('.editor-shell'), blockButton.getAttribute('data-editor-block'));
          return;
        }}

        const imageSizeButton = event.target.closest('[data-editor-image-size]');
        if (imageSizeButton) {{
          applyImagePreset(
            imageSizeButton.closest('.editor-shell'),
            imageSizeButton.getAttribute('data-editor-image-size')
          );
          return;
        }}

        const imageAlignButton = event.target.closest('[data-editor-image-align]');
        if (imageAlignButton) {{
          applyImageAlignment(
            imageAlignButton.closest('.editor-shell'),
            imageAlignButton.getAttribute('data-editor-image-align')
          );
          return;
        }}

        const imageWrapButton = event.target.closest('[data-editor-image-wrap]');
        if (imageWrapButton) {{
          applyImageWrap(
            imageWrapButton.closest('.editor-shell'),
            imageWrapButton.getAttribute('data-editor-image-wrap')
          );
          return;
        }}

        const imageClearButton = event.target.closest('[data-editor-image-clear]');
        if (imageClearButton) {{
          clearImageFormatting(imageClearButton.closest('.editor-shell'));
          return;
        }}

        const toggleButton = event.target.closest('[data-editor-toggle-source]');
        if (toggleButton) {{
          const shell = toggleButton.closest('.editor-shell');
          const source = shell.querySelector('.editor-source');
          syncSource(shell);
          source.classList.toggle('is-hidden');
          return;
        }}

        const resetButton = event.target.closest('[data-editor-reset]');
        if (resetButton) {{
          resetEditor(resetButton.closest('.editor-shell'));
          return;
        }}

        const undoButton = event.target.closest('[data-editor-undo]');
        if (undoButton) {{
          popUndo(undoButton.closest('.editor-shell'));
          return;
        }}

        const copyButton = event.target.closest('[data-editor-copy]');
        if (copyButton) {{
          copyHtml(copyButton.closest('.editor-shell'));
        }}
      }});

      document.addEventListener('change', (event) => {{
        const accordionSelect = event.target.closest('[data-accordion-mode]');
        if (accordionSelect) {{
          const mode = accordionSelect.value;
          if (mode) applyAccordionMode(accordionSelect.closest('.editor-shell'), mode);
          return;
        }}

        const iconSelect = event.target.closest('[data-icon-picker]');
        if (iconSelect) {{
          const basename = iconSelect.value;
          if (basename) applyIconChange(iconSelect.closest('.editor-shell'), basename);
          iconSelect.value = '';
          return;
        }}

        const gapSelect = event.target.closest('[data-image-gap]');
        if (gapSelect) {{
          const px = gapSelect.value;
          if (px) applyImageGap(gapSelect.closest('.editor-shell'), px);
          gapSelect.value = '';
          return;
        }}

        const bannerSelect = event.target.closest('[data-banner-picker]');
        if (!bannerSelect) {{
          return;
        }}
        const shell = bannerSelect.closest('.editor-shell');
        const filename = bannerSelect.value;
        if (!filename) {{
          return;
        }}
        const catalog = bannerCatalog();
        const newEntry = catalog[filename];
        if (!newEntry) {{
          return;
        }}
        const surface = getSurface(shell);
        // Locate the existing banner img: check data URIs from catalog, then by src attribute
        let bannerImg = null;
        for (const [fn, entry] of Object.entries(catalog)) {{
          const img = surface.querySelector(`img[src="${{entry.data_uri}}"]`);
          if (img) {{
            bannerImg = img;
            break;
          }}
        }}
        if (!bannerImg) {{
          bannerImg = Array.from(surface.querySelectorAll('img')).find((img) =>
            /TemplateAssets.*banner/i.test(img.getAttribute('src') || '') ||
            /banner.*[.](png|jpg)/i.test(img.getAttribute('src') || '')
          ) || null;
        }}
        if (!bannerImg) {{
          return;
        }}
        // Swap the asset map entry for the banner
        const assetMapEl = shell.querySelector('.editor-asset-map');
        const assetMap = parseAssetMap(shell);
        for (const [fn, entry] of Object.entries(catalog)) {{
          delete assetMap[entry.raw_ref];
        }}
        assetMap[newEntry.raw_ref] = newEntry.data_uri;
        assetMapEl.textContent = JSON.stringify(assetMap);
        // Update the img src and sync the source textarea
        bannerImg.src = newEntry.data_uri;
        syncSource(shell);
      }});

      // ── Collapsible page cards ────────────────────────────────────────────
      const allCards = Array.from(document.querySelectorAll('.page-card'));
      // Collapse every card except the first (highest priority)
      allCards.forEach((card, idx) => {{
        if (idx > 0) card.classList.add('is-collapsed');
      }});
      // Click the page-head to expand/collapse
      document.querySelectorAll('.page-head').forEach((head) => {{
        head.addEventListener('click', (event) => {{
          if (event.target.closest('button, a, input, select')) return;
          head.closest('.page-card')?.classList.toggle('is-collapsed');
        }});
      }});

      // ── Page filter bar ───────────────────────────────────────────────────
      const searchInput = document.querySelector('.page-search');
      const filterCountEl = document.querySelector('[data-filter-count]');
      let activePriority = 'all';
      const activeContentFilters = new Set();

      function applyPageFilters() {{
        const query = (searchInput?.value || '').toLowerCase();
        let visibleCount = 0;
        allCards.forEach((card) => {{
          const name = (card.getAttribute('data-page-name') || '').toLowerCase();
          const priority = card.getAttribute('data-priority') || 'low';
          const passes = (
            (!query || name.includes(query)) &&
            (activePriority === 'all' || priority === activePriority) &&
            (!activeContentFilters.has('images')     || card.getAttribute('data-has-images')     === '1') &&
            (!activeContentFilters.has('accordions') || card.getAttribute('data-has-accordions') === '1') &&
            (!activeContentFilters.has('tables')     || card.getAttribute('data-has-tables')     === '1') &&
            (!activeContentFilters.has('iframes')    || card.getAttribute('data-has-iframes')    === '1')
          );
          card.style.display = passes ? '' : 'none';
          if (passes) visibleCount++;
        }});
        if (filterCountEl) filterCountEl.textContent = `${{visibleCount}} of ${{allCards.length}} pages`;
      }}

      if (searchInput) searchInput.addEventListener('input', applyPageFilters);
      document.querySelectorAll('[data-filter-priority]').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          activePriority = btn.getAttribute('data-filter-priority') || 'all';
          document.querySelectorAll('.chip-priority').forEach((b) => b.classList.toggle('is-active', b === btn));
          applyPageFilters();
        }});
      }});
      document.querySelectorAll('[data-filter-content]').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const key = btn.getAttribute('data-filter-content');
          if (activeContentFilters.has(key)) {{
            activeContentFilters.delete(key);
            btn.classList.remove('is-active');
          }} else {{
            activeContentFilters.add(key);
            btn.classList.add('is-active');
          }}
          applyPageFilters();
        }});
      }});
      applyPageFilters();

    }})();
  </script>
</body>
</html>
"""
    output_html.write_text(document.strip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lms-review-pack",
        description="Build a deterministic page-level review pack from original and converted course packages.",
    )
    parser.add_argument(
        "--original-zip",
        type=Path,
        required=True,
        help="Path to the original D2L export zip",
    )
    parser.add_argument(
        "--converted-zip",
        type=Path,
        required=True,
        help="Path to the converted canvas-ready zip",
    )
    parser.add_argument(
        "--migration-report-json",
        type=Path,
        default=None,
        help="Optional migration report JSON",
    )
    parser.add_argument(
        "--visual-audit-json",
        type=Path,
        default=None,
        help="Optional visual audit JSON",
    )
    parser.add_argument(
        "--output-json", type=Path, default=None, help="Optional output JSON path"
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional output Markdown path",
    )
    parser.add_argument(
        "--output-html", type=Path, default=None, help="Optional output HTML path"
    )
    args = parser.parse_args()

    if not args.original_zip.exists():
        parser.error(f"Original zip does not exist: {args.original_zip}")
    if not args.converted_zip.exists():
        parser.error(f"Converted zip does not exist: {args.converted_zip}")

    json_path, markdown_path, html_path = build_review_pack(
        original_zip=args.original_zip,
        converted_zip=args.converted_zip,
        migration_report_json=args.migration_report_json,
        visual_audit_json=args.visual_audit_json,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        output_html_path=args.output_html,
    )
    print(f"Review pack JSON: {json_path}")
    print(f"Review pack Markdown: {markdown_path}")
    print(f"Review pack HTML: {html_path}")


if __name__ == "__main__":
    main()

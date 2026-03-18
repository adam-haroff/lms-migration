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
_TITLE_RE = re.compile(r"<title\b[^>]*>(?P<body>.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body\b[^>]*>(?P<body>.*?)</body>", flags=re.IGNORECASE | re.DOTALL)
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
_SRC_ATTR_RE = re.compile(r'(?P<prefix>\bsrc\s*=\s*)(?P<quote>["\'])(?P<src>[^"\']+)(?P=quote)', flags=re.IGNORECASE)
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
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), normalized_ref))
    normalized = normalized.lstrip("./")
    if normalized in name_set:
        return normalized
    return lower_map.get(normalized.lower())


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
        reasons.append("Converted page still repeats the title in the first content block.")
    if int(row.get("converted_shared_template_refs", 0) or 0) > 0:
        reasons.append("Converted page still references shared Brightspace template assets.")
    if int(row.get("converted_title_tags", 0) or 0) > 0:
        reasons.append("Converted page still contains one or more <title> tags.")
    if int(row.get("converted_hr_nonstandard", 0) or 0) > 0:
        reasons.append("Converted page still contains nonstandard divider styling.")
    if int(row.get("converted_template_icons_missing_size_style", 0) or 0) > 0:
        reasons.append("Converted page still contains template icons without standard sizing.")
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
        visual_audit = build_visual_audit(original_zip=original_zip, converted_zip=converted_zip)

    migration_index = _migration_issue_index(migration_report if isinstance(migration_report, dict) else None)
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

        manual_issues = issue_row.get("manual_review_issues", []) if isinstance(issue_row, dict) else []
        if not isinstance(manual_issues, list):
            manual_issues = []
        accessibility_issues = issue_row.get("accessibility_issues", []) if isinstance(issue_row, dict) else []
        if not isinstance(accessibility_issues, list):
            accessibility_issues = []
        applied_changes = issue_row.get("applied_changes", []) if isinstance(issue_row, dict) else []
        if not isinstance(applied_changes, list):
            applied_changes = []

        structural_reasons = _metric_drift(original_metrics, converted_metrics)
        visual_reasons = _visual_reasons(visual_row if isinstance(visual_row, dict) else None)
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
        "files_with_high_priority_review": sum(1 for row in files if row.get("priority") == "high"),
        "files_with_medium_priority_review": sum(1 for row in files if row.get("priority") == "medium"),
        "files_with_manual_issues": sum(1 for row in files if row.get("manual_review_issues")),
        "files_with_accessibility_issues": sum(1 for row in files if row.get("accessibility_issues")),
        "files_with_visual_flags": sum(1 for row in files if row.get("visual_reasons")),
        "files_with_structural_drift": sum(1 for row in files if row.get("structural_reasons")),
    }

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "original_zip": str(original_zip),
            "converted_zip": str(converted_zip),
            "migration_report_json": str(migration_report_json) if migration_report_json is not None else "",
            "visual_audit_json": str(visual_audit_json) if visual_audit_json is not None else "",
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
    return str(((row.get("original_metrics") or {}).get(key, 0))) + " -> " + str(
        ((row.get("converted_metrics") or {}).get(key, 0))
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
                    filter(None, (_issue_reason_text(item) for item in manual_issues[:3]))
                )
            )
        if accessibility_issues:
            lines.append(
                "  - Accessibility: "
                + "; ".join(
                    filter(None, (_issue_reason_text(item) for item in accessibility_issues[:3]))
                )
            )
        if visual_reasons:
            lines.append("  - Visual: " + "; ".join(str(item) for item in visual_reasons[:3]))
        if structural_reasons:
            lines.append("  - Structure: " + "; ".join(str(item) for item in structural_reasons[:3]))
        converted_outline = row.get("converted_outline", [])
        if converted_outline:
            lines.append("  - Converted outline: " + " | ".join(str(item) for item in converted_outline[:4]))
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
    return f"<div class=\"issue-block\"><h4>{html.escape(title)}</h4><ul>{rendered}</ul></div>"


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
        ("Accessibility issue pages", summary.get("files_with_accessibility_issues", 0)),
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
    rows: list[str] = []
    for row in report.get("top_review_pages", []):
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
        rows.append(
            f"""
            <section class="page-card">
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
                    <p class="editor-note">Edit the Canvas body HTML locally. Use <strong>Export Review Draft</strong> to write your approved changes into a deterministic JSON file the app can apply back into a reviewed package.</p>
                  </div>
                  <div class="editor-toolbar">
                    <button type="button" data-editor-command="bold">Bold</button>
                    <button type="button" data-editor-command="italic">Italic</button>
                    <button type="button" data-editor-command="insertUnorderedList">Bullets</button>
                    <button type="button" data-editor-command="insertOrderedList">Numbered</button>
                    <button type="button" data-editor-block="h2">H2</button>
                    <button type="button" data-editor-block="h3">H3</button>
                    <button type="button" data-editor-image-size="320">Image 320</button>
                    <button type="button" data-editor-image-size="480">Image 480</button>
                    <button type="button" data-editor-image-size="640">Image 640</button>
                    <button type="button" data-editor-image-size="full">Image Full</button>
                    <button type="button" data-editor-image-align="left">Image Left</button>
                    <button type="button" data-editor-image-align="center">Image Center</button>
                    <button type="button" data-editor-image-align="right">Image Right</button>
                    <button type="button" data-editor-image-wrap="left">Wrap Left</button>
                    <button type="button" data-editor-image-wrap="right">Wrap Right</button>
                    <button type="button" data-editor-image-clear>Reset Image</button>
                    <button type="button" data-editor-toggle-source>Source</button>
                    <button type="button" data-editor-reset>Reset</button>
                    <button type="button" data-editor-copy>Copy HTML</button>
                  </div>
                </div>
                <p class="editor-hint">Click an image inside the editor before using the image buttons. Block alignments are safest. Wrap Left/Right is available when you intentionally want text wrapping similar to the original D2L layout.</p>
                <div class="editor-surface" contenteditable="true">{preview_body_html or '<p>No converted body HTML available for this page.</p>'}</div>
                <textarea class="editor-source is-hidden" spellcheck="false">{html.escape(raw_body_html)}</textarea>
                <template class="editor-initial-preview">{preview_body_html}</template>
                <textarea class="editor-initial-source is-hidden" spellcheck="false">{html.escape(raw_body_html)}</textarea>
                <script type="application/json" class="editor-asset-map">{json.dumps(asset_map)}</script>
              </div>
            </section>
            """
        )

    card_html = "".join(
        f"<div class=\"summary-card\"><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in cards
    )
    document = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Page Review Workbench</title>
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
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 10px;
    }}
    .editor-note {{
      margin: 0;
      color: var(--muted);
    }}
    .editor-hint {{
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .editor-toolbar {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .editor-toolbar button {{
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 999px;
      padding: 7px 12px;
      cursor: pointer;
      font: inherit;
    }}
    .editor-toolbar button:hover {{
      border-color: var(--accent);
    }}
    .editor-surface {{
      min-height: 220px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 16px;
      overflow: auto;
    }}
    .editor-surface:focus {{
      outline: 2px solid rgba(172, 26, 47, 0.25);
      border-color: var(--accent);
    }}
    .editor-surface img {{
      max-width: 100%;
      height: auto;
    }}
    .editor-surface img.is-selected {{
      outline: 3px solid rgba(172, 26, 47, 0.4);
      outline-offset: 3px;
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
    {''.join(rows)}
  </main>
  <script type="application/json" id="review-inputs">{review_inputs_json}</script>
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
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');
        const assetMap = parseAssetMap(shell);
        source.value = previewToRaw(surface.innerHTML, assetMap);
      }}

      function execBlock(shell, blockTag) {{
        const surface = shell.querySelector('.editor-surface');
        surface.focus();
        document.execCommand('formatBlock', false, blockTag);
        syncSource(shell);
      }}

      function execCommand(shell, command) {{
        const surface = shell.querySelector('.editor-surface');
        surface.focus();
        document.execCommand(command, false, null);
        syncSource(shell);
      }}

      function clearSelectedImages(scope) {{
        scope.querySelectorAll('img.is-selected').forEach((image) => image.classList.remove('is-selected'));
      }}

      function selectedImage(shell) {{
        return shell.querySelector('.editor-surface img.is-selected');
      }}

      function applyImagePreset(shell, size) {{
        const image = selectedImage(shell);
        if (!image) {{
          return false;
        }}
        image.removeAttribute('align');
        image.style.float = 'none';
        image.style.clear = 'both';
        image.style.display = 'block';
        image.style.height = 'auto';
        image.style.maxWidth = '100%';
        if (size === 'full') {{
          image.style.width = '100%';
        }} else {{
          image.style.width = `${{size}}px`;
        }}
        syncSource(shell);
        return true;
      }}

      function applyImageAlignment(shell, alignment) {{
        const image = selectedImage(shell);
        if (!image) {{
          return false;
        }}
        image.removeAttribute('align');
        image.style.float = 'none';
        image.style.clear = 'both';
        image.style.display = 'block';
        image.style.maxWidth = '100%';
        image.style.height = 'auto';
        if (alignment === 'left') {{
          image.style.margin = '16px auto 16px 0';
        }} else if (alignment === 'right') {{
          image.style.margin = '16px 0 16px auto';
        }} else {{
          image.style.margin = '16px auto';
        }}
        syncSource(shell);
        return true;
      }}

      function clearImageFormatting(shell) {{
        const image = selectedImage(shell);
        if (!image) {{
          return false;
        }}
        image.removeAttribute('align');
        image.style.float = 'none';
        image.style.clear = 'both';
        image.style.display = 'block';
        image.style.width = '';
        image.style.maxWidth = '100%';
        image.style.height = 'auto';
        image.style.margin = '16px auto';
        syncSource(shell);
        return true;
      }}

      function applyImageWrap(shell, direction) {{
        const image = selectedImage(shell);
        if (!image) {{
          return false;
        }}
        const width = image.style.width && image.style.width !== '100%' ? image.style.width : '320px';
        image.removeAttribute('align');
        image.style.clear = 'none';
        image.style.display = 'block';
        image.style.height = 'auto';
        image.style.width = width;
        image.style.maxWidth = '45%';
        image.style.float = direction;
        image.style.margin = direction === 'right' ? '0 0 16px 16px' : '0 16px 16px 0';
        syncSource(shell);
        return true;
      }}

      function resetEditor(shell) {{
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');
        const initialPreview = shell.querySelector('.editor-initial-preview')?.innerHTML || '';
        const initialSource = shell.querySelector('.editor-initial-source')?.value || '';
        surface.innerHTML = initialPreview;
        source.value = initialSource;
        clearSelectedImages(shell);
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

      document.querySelectorAll('.editor-shell').forEach((shell) => {{
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');
        syncSource(shell);
        surface.addEventListener('input', () => syncSource(shell));
        surface.addEventListener('click', (event) => {{
          const clickedImage = event.target.closest('img');
          if (!clickedImage) {{
            return;
          }}
          clearSelectedImages(shell);
          clickedImage.classList.add('is-selected');
        }});
        source.addEventListener('input', () => {{
          surface.innerHTML = rawToPreview(source.value, parseAssetMap(shell));
          clearSelectedImages(shell);
        }});
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

        const copyButton = event.target.closest('[data-editor-copy]');
        if (copyButton) {{
          copyHtml(copyButton.closest('.editor-shell'));
        }}
      }});
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
    parser.add_argument("--original-zip", type=Path, required=True, help="Path to the original D2L export zip")
    parser.add_argument("--converted-zip", type=Path, required=True, help="Path to the converted canvas-ready zip")
    parser.add_argument("--migration-report-json", type=Path, default=None, help="Optional migration report JSON")
    parser.add_argument("--visual-audit-json", type=Path, default=None, help="Optional visual audit JSON")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path")
    parser.add_argument("--output-markdown", type=Path, default=None, help="Optional output Markdown path")
    parser.add_argument("--output-html", type=Path, default=None, help="Optional output HTML path")
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

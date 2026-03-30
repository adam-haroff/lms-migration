from __future__ import annotations

import argparse
import base64
import csv
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
_FOCUS_LABELS = {
    "layout-risk": "Layout Risk",
    "content-loss": "Content Loss",
    "manual-fix": "Manual Fix",
    "accessibility": "Accessibility",
}
_LAYOUT_SANITIZER_SIGNAL_MAP: tuple[tuple[str, str], ...] = (
    ("Removed position: absolute/fixed", "absolute/fixed positioning removed"),
    ("Degraded display: flex/grid", "flex/grid layout degraded"),
    ("Removed multi-column CSS layout properties", "multi-column layout removed"),
    ("Wrapped floated content blocks", "floated blocks wrapped"),
    ("Promoted Bootstrap grid classes to CSS flexbox", "bootstrap grid converted to flex"),
)


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


def _layout_sanitizer_flags(applied_changes: list[dict]) -> list[str]:
    flags: list[str] = []
    for change in applied_changes:
        if not isinstance(change, dict):
            continue
        description = str(change.get("description", ""))
        for needle, label in _LAYOUT_SANITIZER_SIGNAL_MAP:
            if needle in description and label not in flags:
                flags.append(label)
    return flags


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


_NEUTRAL_PREVIEW_CSS = (
    "body{font-family:Georgia,serif;font-size:14px;line-height:1.6;"
    "padding:12px;margin:0;color:#202020;word-wrap:break-word}"
    "img{max-width:100%;height:auto;display:inline-block}"
    "table{border-collapse:collapse;width:100%;max-width:100%}"
    "td,th{padding:4px 8px;border:1px solid #ddd;vertical-align:top}"
    "h1{font-size:1.5em;margin:.5em 0 .25em}h2{font-size:1.3em;margin:.5em 0 .25em}"
    "h3{font-size:1.1em;margin:.5em 0 .25em}"
    "p{margin:.4em 0}ul,ol{margin:.4em 0;padding-left:1.5em}"
    "details>summary{cursor:pointer;font-weight:600}"
)

# Strips class="" attributes so Brightspace/Bootstrap selectors can't interfere.
# All layout that matters (float, flex, padding) is already in inline style.
_CLASS_ATTR_RE = re.compile(r'\s+class="[^"]*"', re.IGNORECASE)
_CLASS_ATTR_SQ_RE = re.compile(r"\s+class='[^']*'", re.IGNORECASE)


def _neutralize_body_html(body: str) -> str:
    """Strip class attributes so platform-specific CSS selectors have nothing to match.

    Inline style attributes are intentionally preserved — the pipeline promotes
    layout-relevant Bootstrap utility / grid classes to inline CSS before stripping.
    """
    body = _CLASS_ATTR_RE.sub("", body)
    body = _CLASS_ATTR_SQ_RE.sub("", body)
    return body


_PREVIEW_HEAD = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    "<style>" + _NEUTRAL_PREVIEW_CSS + "</style>"
    "</head><body>"
)
_PREVIEW_TAIL = "</body></html>"


def _build_neutral_srcdoc(body_html: str) -> str:
    """Wrap a neutralized body fragment in a minimal self-contained HTML document."""
    return _PREVIEW_HEAD + _neutralize_body_html(body_html) + _PREVIEW_TAIL


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
    if original["divider_count"] != converted["divider_count"]:
        reasons.append(
            f"Divider count changed {original['divider_count']} -> {converted['divider_count']}."
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


def _priority(
    score: int,
    *,
    manual_count: int,
    accessibility_count: int,
    template_count: int = 0,
    content_loss: bool = False,
) -> str:
    if manual_count >= 4 or accessibility_count >= 3 or score >= 10 or content_loss:
        return "high"
    if manual_count > 0 or accessibility_count > 0 or template_count > 0 or score >= 4:
        return "medium"
    return "low"


def _review_focus_tags(
    *,
    manual_count: int,
    accessibility_count: int,
    layout_sanitizer_flags: list[str],
    visual_reasons: list[str],
    structural_reasons: list[str],
    content_loss: bool,
) -> list[str]:
    tags: list[str] = []
    if layout_sanitizer_flags or visual_reasons or structural_reasons:
        tags.append("layout-risk")
    if content_loss:
        tags.append("content-loss")
    if manual_count:
        tags.append("manual-fix")
    if accessibility_count:
        tags.append("accessibility")
    return tags


def _review_reason_summary(
    *,
    layout_sanitizer_flags: list[str],
    visual_reasons: list[str],
    structural_reasons: list[str],
    manual_issues: list[dict],
    accessibility_issues: list[dict],
    content_loss: bool,
    missing_images: bool,
    limit: int = 4,
) -> list[str]:
    reasons: list[str] = []

    if content_loss:
        reasons.append("possible text/content loss during conversion")
    if missing_images:
        reasons.append("original images may be missing from the converted page")

    reasons.extend(str(item).strip() for item in layout_sanitizer_flags if str(item).strip())
    reasons.extend(str(item).strip() for item in visual_reasons if str(item).strip())
    reasons.extend(str(item).strip() for item in structural_reasons if str(item).strip())
    reasons.extend(
        reason
        for reason in (_issue_reason_text(item) for item in manual_issues)
        if reason
    )
    reasons.extend(
        reason
        for reason in (_issue_reason_text(item) for item in accessibility_issues)
        if reason
    )

    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        normalized = reason.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(reason)
        if len(deduped) >= limit:
            break
    return deduped


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


def _default_output_shortlist_csv(output_json: Path) -> Path:
    return output_json.with_name(f"{output_json.stem}-shortlist.csv")


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
        layout_sanitizer_flags = _layout_sanitizer_flags(applied_changes)

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

        template_issues = [
            i
            for i in manual_issues
            if isinstance(i, dict) and i.get("category", "content") == "template"
        ]
        non_template_manual = [
            i
            for i in manual_issues
            if not (isinstance(i, dict) and i.get("category", "content") == "template")
        ]
        score = (
            (len(non_template_manual) * 4)
            + (len(accessibility_issues) * 3)
            + (len(visual_reasons) * 2)
            + len(structural_reasons)
            + (len(template_issues) * 2)
        )
        score += min(len(layout_sanitizer_flags), 3)
        if preview_similarity and preview_similarity < 0.55:
            score += 2
        elif preview_similarity and preview_similarity < 0.72:
            score += 1

        # Smarter signals
        orig_words = int((original_metrics or {}).get("word_count", 0) or 0)
        conv_words = int((converted_metrics or {}).get("word_count", 0) or 0)
        content_loss = orig_words >= 50 and conv_words < orig_words * 0.75
        if content_loss:
            score += 3

        orig_images = int((original_metrics or {}).get("image_count", 0) or 0)
        conv_images = int((converted_metrics or {}).get("image_count", 0) or 0)
        if orig_images > 0 and conv_images == 0:
            score += 2

        template_mapped = sum(
            int(c.get("count", 0) or 0)
            for c in (applied_changes if isinstance(applied_changes, list) else [])
            if isinstance(c, dict)
            and c.get("category") == "template_overlay"
            and "Mapped" in str(c.get("description", ""))
        )
        if template_mapped == 0 and orig_words >= 100:
            score += 1

        priority = _priority(
            score,
            manual_count=len(non_template_manual),
            accessibility_count=len(accessibility_issues),
            template_count=len(template_issues),
            content_loss=content_loss,
        )
        layout_risk_score = (
            (len(layout_sanitizer_flags) * 3)
            + (len(visual_reasons) * 2)
            + len(structural_reasons)
        )
        if preview_similarity and preview_similarity < 0.72:
            layout_risk_score += 1
        if orig_images > 0 and conv_images == 0:
            layout_risk_score += 1

        content_loss_score = 0
        if content_loss:
            content_loss_score += 3
        missing_images = orig_images > 0 and conv_images == 0
        if missing_images:
            content_loss_score += 2

        review_focus = _review_focus_tags(
            manual_count=len(non_template_manual),
            accessibility_count=len(accessibility_issues),
            layout_sanitizer_flags=layout_sanitizer_flags,
            visual_reasons=visual_reasons,
            structural_reasons=structural_reasons,
            content_loss=content_loss,
        )
        review_reason_summary = _review_reason_summary(
            layout_sanitizer_flags=layout_sanitizer_flags,
            visual_reasons=visual_reasons,
            structural_reasons=structural_reasons,
            manual_issues=non_template_manual,
            accessibility_issues=accessibility_issues,
            content_loss=content_loss,
            missing_images=missing_images,
        )
        files.append(
            {
                "path": path,
                "priority": priority,
                "review_score": score,
                "layout_risk_score": layout_risk_score,
                "content_loss_score": content_loss_score,
                "review_focus": review_focus,
                "review_reason_summary": review_reason_summary,
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
                "manual_review_issues": non_template_manual,
                "template_issues": template_issues,
                "accessibility_issues": accessibility_issues,
                "applied_changes": applied_changes,
                "layout_sanitizer_flags": layout_sanitizer_flags,
                "structural_reasons": structural_reasons,
                "visual_reasons": visual_reasons,
                "content_loss": content_loss,
            }
        )
        editor_payloads[path] = {
            "converted_body_html": _extract_body_html(converted),
            "original_body_html": _extract_body_html(original),
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
        "files_with_template_issues": sum(
            1 for row in files if row.get("template_issues")
        ),
        "files_with_accessibility_issues": sum(
            1 for row in files if row.get("accessibility_issues")
        ),
        "files_with_layout_sanitizer_flags": sum(
            1 for row in files if row.get("layout_sanitizer_flags")
        ),
        "files_with_visual_flags": sum(1 for row in files if row.get("visual_reasons")),
        "files_with_structural_drift": sum(
            1 for row in files if row.get("structural_reasons")
        ),
        "files_with_content_loss": sum(1 for row in files if row.get("content_loss")),
    }

    top_layout_risk_pages = sorted(
        [row for row in files if int(row.get("layout_risk_score", 0) or 0) > 0],
        key=lambda row: (
            -int(row.get("layout_risk_score", 0) or 0),
            -int(row.get("review_score", 0) or 0),
            str(row.get("path", "")),
        ),
    )[:12]
    top_content_loss_pages = sorted(
        [row for row in files if int(row.get("content_loss_score", 0) or 0) > 0],
        key=lambda row: (
            -int(row.get("content_loss_score", 0) or 0),
            -int(row.get("review_score", 0) or 0),
            str(row.get("path", "")),
        ),
    )[:12]
    top_manual_issue_pages = sorted(
        [row for row in files if row.get("manual_review_issues")],
        key=lambda row: (
            -len(row.get("manual_review_issues", [])),
            -int(row.get("review_score", 0) or 0),
            str(row.get("path", "")),
        ),
    )[:12]

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
        "top_layout_risk_pages": top_layout_risk_pages,
        "top_content_loss_pages": top_content_loss_pages,
        "top_manual_issue_pages": top_manual_issue_pages,
        "files": files,
    }

    output_json = output_json_path or _default_output_json(converted_zip)
    output_markdown = output_markdown_path or _default_output_markdown(output_json)
    output_html = output_html_path or _default_output_html(output_json)
    output_shortlist = _default_output_shortlist_csv(output_json)

    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, output_markdown)
    _write_shortlist_csv(report, output_shortlist)
    _write_html(
        report,
        output_html,
        original_zip=original_zip,
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
    def _write_page_list(
        lines: list[str], title: str, rows: list[dict], *, score_key: str
    ) -> None:
        lines.extend(["## " + title, ""])
        if not rows:
            lines.append("- None")
            lines.append("")
            return
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('path', '')}` | priority={row.get('priority', 'low')} | "
                f"score={row.get('review_score', 0)} | {score_key}={row.get(score_key, 0)}"
            )
            review_reason_summary = [
                str(item).strip()
                for item in row.get("review_reason_summary", [])
                if str(item).strip()
            ]
            if review_reason_summary:
                lines.append("  - Why: " + "; ".join(review_reason_summary[:3]))
            layout_sanitizer_flags = row.get("layout_sanitizer_flags", [])
            visual_reasons = row.get("visual_reasons", [])
            structural_reasons = row.get("structural_reasons", [])
            manual_issues = row.get("manual_review_issues", [])
            accessibility_issues = row.get("accessibility_issues", [])
            if score_key == "layout_risk_score" and (
                layout_sanitizer_flags or visual_reasons or structural_reasons
            ):
                reasons = (
                    [str(item) for item in layout_sanitizer_flags[:2]]
                    + [str(item) for item in visual_reasons[:1]]
                    + [str(item) for item in structural_reasons[:1]]
                )
                if reasons:
                    lines.append("  - Signals: " + "; ".join(reasons))
            if score_key == "content_loss_score" and row.get("content_loss"):
                lines.append("  - Signals: content-loss heuristic triggered")
            if score_key == "review_score" and manual_issues:
                lines.append(
                    "  - Manual: "
                    + "; ".join(
                        filter(
                            None,
                            (_issue_reason_text(item) for item in manual_issues[:3]),
                        )
                    )
                )
            if score_key == "review_score" and accessibility_issues:
                lines.append(
                    "  - Accessibility: "
                    + "; ".join(
                        filter(
                            None,
                            (
                                _issue_reason_text(item)
                                for item in accessibility_issues[:3]
                            ),
                        )
                    )
                )
        lines.append("")

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
        f"- Pages with layout sanitizer flags: {summary.get('files_with_layout_sanitizer_flags', 0)}",
        f"- Pages with visual flags: {summary.get('files_with_visual_flags', 0)}",
        f"- Pages with structural drift: {summary.get('files_with_structural_drift', 0)}",
        f"- Pages with content loss: {summary.get('files_with_content_loss', 0)}",
        "",
    ]

    _write_page_list(
        lines,
        "Top Layout-Risk Pages",
        report.get("top_layout_risk_pages", []),
        score_key="layout_risk_score",
    )
    _write_page_list(
        lines,
        "Top Content-Loss Pages",
        report.get("top_content_loss_pages", []),
        score_key="content_loss_score",
    )
    _write_page_list(
        lines,
        "Top Review Pages",
        report.get("top_review_pages", []),
        score_key="review_score",
    )
    output_markdown.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_shortlist_csv(report: dict, output_csv: Path) -> None:
    rows = [
        row
        for row in report.get("files", [])
        if isinstance(row, dict)
        and (
            int(row.get("review_score", 0) or 0) > 0
            or bool(row.get("review_focus"))
        )
    ]
    fieldnames = [
        "path",
        "converted_title",
        "priority",
        "review_score",
        "layout_risk_score",
        "content_loss_score",
        "review_focus",
        "why_flagged",
        "preview_similarity",
        "manual_issue_count",
        "accessibility_issue_count",
        "layout_transform_count",
        "visual_flag_count",
        "structural_drift_count",
        "original_dividers",
        "converted_dividers",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            titles = row.get("titles") or {}
            original_metrics = row.get("original_metrics") or {}
            converted_metrics = row.get("converted_metrics") or {}
            writer.writerow(
                {
                    "path": str(row.get("path", "")),
                    "converted_title": str(titles.get("converted", "")),
                    "priority": str(row.get("priority", "low")),
                    "review_score": int(row.get("review_score", 0) or 0),
                    "layout_risk_score": int(row.get("layout_risk_score", 0) or 0),
                    "content_loss_score": int(row.get("content_loss_score", 0) or 0),
                    "review_focus": "; ".join(
                        str(item).strip()
                        for item in row.get("review_focus", [])
                        if str(item).strip()
                    ),
                    "why_flagged": "; ".join(
                        str(item).strip()
                        for item in row.get("review_reason_summary", [])
                        if str(item).strip()
                    ),
                    "preview_similarity": row.get("preview_similarity", ""),
                    "manual_issue_count": len(row.get("manual_review_issues", [])),
                    "accessibility_issue_count": len(
                        row.get("accessibility_issues", [])
                    ),
                    "layout_transform_count": len(
                        row.get("layout_sanitizer_flags", [])
                    ),
                    "visual_flag_count": len(row.get("visual_reasons", [])),
                    "structural_drift_count": len(row.get("structural_reasons", [])),
                    "original_dividers": int(
                        original_metrics.get("divider_count", 0) or 0
                    ),
                    "converted_dividers": int(
                        converted_metrics.get("divider_count", 0) or 0
                    ),
                }
            )


def _badge(priority: str) -> str:
    colors = {"high": "#ac1a2f", "medium": "#d97706", "low": "#2563eb"}
    background = colors.get(priority, "#475569")
    return (
        f'<span class="badge" style="background:{background};">'
        f"{html.escape(priority.title())}</span>"
    )


def _render_issue_list(title: str, items: list[str], *, category: str = "") -> str:
    if not items:
        return ""
    cat_attr = f' data-category="{html.escape(category)}"' if category else ""
    rendered = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f'<div class="issue-block"{cat_attr}><h4>{html.escape(title)}</h4><ul>{rendered}</ul></div>'


def _editor_dom_id(path: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return normalized or "page-review-editor"


def _write_html(
    report: dict,
    output_html: Path,
    *,
    original_zip: Path,
    converted_zip: Path,
    editor_payloads: dict[str, dict[str, str]],
) -> None:
    summary = report.get("summary", {})
    cards = [
        ("Files scanned", summary.get("files_scanned", 0)),
        ("High priority", summary.get("files_with_high_priority_review", 0)),
        ("Medium priority", summary.get("files_with_medium_priority_review", 0)),
        ("Manual issue pages", summary.get("files_with_manual_issues", 0)),
        ("Template issue pages", summary.get("files_with_template_issues", 0)),
        (
            "Accessibility issue pages",
            summary.get("files_with_accessibility_issues", 0),
        ),
        (
            "Layout transform pages",
            summary.get("files_with_layout_sanitizer_flags", 0),
        ),
        ("Visual flag pages", summary.get("files_with_visual_flags", 0)),
        ("Content loss pages", summary.get("files_with_content_loss", 0)),
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
        editor_body_html = raw_body_html
        # Pages with no icon-template heading (h* containing an <img>) get a
        # thick red hr prepended so Canvas sees the same visual divider that
        # icon-template pages get from their post-heading styled <hr>.
        # 10 px matches the border-bottom on Introduction-style h2 headings.
        _stripped = editor_body_html.lstrip()
        _already_has_top_hr = (
            _stripped.startswith("<hr") and "border-top" in _stripped[:120]
        )
        _has_icon_heading = bool(
            re.search(
                r"<h[1-6][^>]*>(?:(?!</h[1-6]>).){0,500}<img\b",
                editor_body_html,
                re.IGNORECASE | re.DOTALL,
            )
        )
        if not _has_icon_heading and not _already_has_top_hr:
            editor_body_html = (
                '<hr style="border-top: 10px solid #AC1A2F; border-bottom: none;'
                ' margin: 0 0 16px 0;">\n' + editor_body_html
            )
        asset_map = _build_preview_asset_map(
            zip_path=converted_zip,
            page_path=page_path,
            body_html=raw_body_html,
        )
        preview_body_html = _apply_preview_asset_map(raw_body_html, asset_map)
        editor_asset_map = _build_preview_asset_map(
            zip_path=converted_zip,
            page_path=page_path,
            body_html=editor_body_html,
        )
        editor_preview_body_html = _apply_preview_asset_map(
            editor_body_html, editor_asset_map
        )
        # Build neutral-render iframes — strip class attrs so platform CSS doesn't fire;
        # inline style (float, flex, padding) is preserved and renders correctly in both.
        _orig_raw = str(editor_payload.get("original_body_html", "")).strip()
        if _orig_raw:
            _orig_asset_map = _build_preview_asset_map(
                zip_path=original_zip,
                page_path=page_path,
                body_html=_orig_raw,
            )
            _orig_rendered = _apply_preview_asset_map(_orig_raw, _orig_asset_map)
            _orig_srcdoc = _build_neutral_srcdoc(_orig_rendered)
            original_preview_block = (
                f'<iframe class="preview-frame" sandbox="allow-same-origin"'
                f' loading="lazy" srcdoc="{html.escape(_orig_srcdoc, quote=True)}"></iframe>'
            )
        else:
            original_preview_block = (
                '<p class="no-preview">No original HTML available.</p>'
            )
        # Converted neutral-render iframe (same stylesheet so layout diff is visible)
        if preview_body_html:
            _conv_srcdoc = _build_neutral_srcdoc(preview_body_html)
            converted_preview_block = (
                f'<iframe class="preview-frame" sandbox="allow-same-origin"'
                f' loading="lazy" srcdoc="{html.escape(_conv_srcdoc, quote=True)}"></iframe>'
            )
        else:
            converted_preview_block = (
                '<p class="no-preview">No converted HTML available.</p>'
            )
        editor_id = _editor_dom_id(page_path)
        manual_items = [
            _issue_reason_text(item)
            for item in row.get("manual_review_issues", [])
            if _issue_reason_text(item)
        ]
        template_items = [
            _issue_reason_text(item)
            for item in row.get("template_issues", [])
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
        review_focus = [
            str(item)
            for item in row.get("review_focus", [])
            if str(item).strip() in _FOCUS_LABELS
        ]
        review_reason_summary = [
            str(item).strip()
            for item in row.get("review_reason_summary", [])
            if str(item).strip()
        ]
        focus_pills_html = "".join(
            f'<span class="focus-pill focus-pill--{html.escape(tag)}">{html.escape(_FOCUS_LABELS[tag])}</span>'
            for tag in review_focus
        )
        focus_summary_html = (
            '<p class="focus-summary"><strong>Why flagged:</strong> '
            + html.escape("; ".join(review_reason_summary[:4]))
            + "</p>"
            if review_reason_summary
            else ""
        )
        _conv_m = row.get("converted_metrics") or {}
        rows.append(
            f"""
            <section class="page-card" data-page-name="{html.escape(page_path, quote=True)}" data-priority="{html.escape(str(row.get('priority', 'low')), quote=True)}" data-has-images="{'1' if _conv_m.get('image_count', 0) > 0 else '0'}" data-has-accordions="{'1' if _conv_m.get('accordion_count', 0) > 0 else '0'}" data-has-tables="{'1' if _conv_m.get('table_count', 0) > 0 else '0'}" data-has-iframes="{'1' if _conv_m.get('iframe_count', 0) > 0 else '0'}" data-has-layout-risk="{'1' if 'layout-risk' in review_focus else '0'}" data-has-content-loss="{'1' if 'content-loss' in review_focus else '0'}" data-has-manual-fix="{'1' if 'manual-fix' in review_focus else '0'}" data-has-accessibility="{'1' if 'accessibility' in review_focus else '0'}">
              <div class="page-head">
                <div>
                  <h2>{html.escape(page_path)}</h2>
                  <p class="title-row">{html.escape(str(((row.get("titles") or {}).get("converted", ""))))}</p>
                  <div class="focus-pills">{focus_pills_html}</div>
                  {focus_summary_html}
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
                <div><strong>Dividers</strong><span>{html.escape(_metric_cell(row, "divider_count"))}</span></div>
                <div><strong>Lists</strong><span>{html.escape(_metric_cell(row, "list_count"))}</span></div>
                <div><strong>Words</strong><span>{html.escape(_metric_cell(row, "word_count"))}</span></div>
              </div>
              <div class="issue-grid">
                {_render_issue_list("Manual Review", manual_items[:5])}
                {_render_issue_list("Template Issues", template_items[:5], category="template")}
                {_render_issue_list("Accessibility", accessibility_items[:5])}
                {_render_issue_list("Layout Transforms", [str(item) for item in row.get("layout_sanitizer_flags", [])[:5]])}
                {_render_issue_list("Visual Flags", [str(item) for item in row.get("visual_reasons", [])[:5]])}
                {_render_issue_list("Structural Drift", [str(item) for item in row.get("structural_reasons", [])[:5]])}
                {_render_issue_list("Deterministic Changes Applied", change_items[:5])}
              </div>
              <div class="compare-grid">
                <div class="compare-column">
                  <h3>D2L Outline</h3>
                  <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in row.get("original_outline", [])[:8]) or "<li>No heading outline extracted.</li>"}</ul>
                  <h3>D2L Layout Preview</h3>
                  {original_preview_block}
                </div>
                <div class="compare-column">
                  <h3>Canvas Outline</h3>
                  <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in row.get("converted_outline", [])[:8]) or "<li>No heading outline extracted.</li>"}</ul>
                  <h3>Canvas Layout Preview</h3>
                  {converted_preview_block}
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
                    <span class="toolbar-label">Dividers</span>
                    <button type="button" data-editor-hr="thick10" title="Insert or change to accent divider (10 px) — page opener">Red 10px</button>
                    <button type="button" data-editor-hr="thick8" title="Insert or change to accent divider (8 px) — page footer">Red 8px</button>
                    <button type="button" data-editor-hr="thin" title="Insert or change to thin grey divider">Grey &#8212;</button>
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
                <div class="editor-status" data-editor-status>Click inside the white editor area below, then use the toolbar buttons</div>
                <div class="editor-surface" contenteditable="true"></div>
                <script type="application/json" class="editor-preview-html">{json.dumps(editor_preview_body_html)}</script>
                <textarea class="editor-source is-hidden" spellcheck="false">{html.escape(editor_body_html)}</textarea>
                <textarea class="editor-initial-source is-hidden" spellcheck="false">{html.escape(editor_body_html)}</textarea>
                <script type="application/json" class="editor-asset-map">{json.dumps(editor_asset_map)}</script>
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
    .focus-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }}
    .focus-pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.01em;
      background: #efe7d7;
      color: #5a4a27;
    }}
    .focus-pill--layout-risk {{
      background: #f6ddd9;
      color: #8a2130;
    }}
    .focus-pill--content-loss {{
      background: #fbe4cf;
      color: #9a4b00;
    }}
    .focus-pill--manual-fix {{
      background: #e6eefc;
      color: #1d4ed8;
    }}
    .focus-pill--accessibility {{
      background: #e0f2e8;
      color: #166534;
    }}
    .focus-summary {{
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.45;
      max-width: 70ch;
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
    .issue-block[data-category="template"] {{
      border-left-color: #d97706;
      background: #fffbf0;
    }}
    .issue-block[data-category="template"] h4 {{
      color: #d97706;
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
    .preview-frame {{
      width: 100%;
      height: 480px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      margin-top: 8px;
      display: block;
    }}
    .no-preview {{
      color: var(--muted);
      font-style: italic;
      margin: 8px 0;
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
    .editor-status {{
      font-size: 11px;
      color: var(--muted);
      background: #f0eff0;
      border-radius: 0 0 8px 8px;
      padding: 3px 12px 4px;
      margin: -10px -14px 10px -14px;
      min-height: 20px;
    }}
    .editor-status.is-active {{ color: #1a7f3c; background: #edf7f0; }}
    .editor-status.is-error {{ color: #9c1a1a; background: #fdf0f0; }}
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
      padding: 20px 24px 32px;
      overflow: auto;
      /* Canvas-like typography */
      font-family: "Lato", "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.6;
      color: #2d3b45;
      cursor: text;
    }}
    .editor-surface:hover {{
      border-color: rgba(172, 26, 47, 0.4);
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
      border-top: 1px solid #c7cfd4;
      margin: 1.5em 0 0;
      cursor: pointer;
      /* Canonical closing hr carries inline style="border-top: 8px solid #AC1A2F;"
         which overrides this default — bare <hr> dividers stay thin grey.
         No margin-bottom: the container's padding-bottom (32px) provides spacing. */
    }}
    .editor-surface hr.is-selected-hr {{
      outline: 2px dashed #ac1a2f;
      outline-offset: 3px;
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
      <p>Deterministic before/after review plus a lightweight local editor for top-priority Canvas page bodies. Use the Layout Risk and Content Loss filters first, then export a review draft for write-back in the app.</p>
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
        <button class="chip" data-filter-focus="layout-risk">Layout Risk</button>
        <button class="chip" data-filter-focus="content-loss">Content Loss</button>
        <button class="chip" data-filter-focus="manual-fix">Manual Fix</button>
        <button class="chip" data-filter-focus="accessibility">Accessibility</button>
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

      // ── Selection tracker ────────────────────────────────────────────────
      // Captured whenever the cursor moves or text is selected inside any
      // editor surface.  Toolbar click handlers restore this snapshot before
      // calling document.execCommand() so the command always targets the right
      // content even in browsers where a button press briefly shifts focus.
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

      // Restore the last saved selection into window.getSelection().
      function _restoreSel(shell) {{
        const saved = savedRanges.get(shell);
        if (!saved) return false;
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(saved);
        return true;
      }}

      // Update the status bar for the given shell.
      function _setStatus(shell, msg, isError) {{
        const bar = shell.querySelector('[data-editor-status]');
        if (!bar) return;
        bar.textContent = msg;
        bar.classList.toggle('is-error', !!isError);
        bar.classList.toggle('is-active', !isError);
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
        const surface = getSurface(shell);
        const source = shell.querySelector('.editor-source');
        const assetMap = parseAssetMap(shell);
        if (!surface) return;
        source.value = previewToRaw(surface.innerHTML, assetMap);
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

      // ── Editing commands ─────────────────────────────────────────────────
      // Uses document.execCommand — the universally-supported browser API for
      // contenteditable editing.  _restoreSel() puts the user's selection back
      // into window.getSelection() before each command so the operation always
      // targets the correct text regardless of any transient focus change.

      function execCommand(shell, command) {{
        const surface = getSurface(shell);
        if (!surface) return;
        if (!savedRanges.has(shell)) {{
          _setStatus(shell, 'Click inside the editor text area first, then try again', true);
          return;
        }}
        pushUndo(shell);
        _restoreSel(shell);
        const ok = document.execCommand(command, false, null);
        syncSource(shell);
        _setStatus(shell, ok ? command + ' applied' : command + ' had no effect (select text first)', !ok);
      }}

      function execBlock(shell, blockTag) {{
        const surface = getSurface(shell);
        if (!surface) return;
        if (!savedRanges.has(shell)) {{
          _setStatus(shell, 'Click inside the editor text area first, then try again', true);
          return;
        }}
        pushUndo(shell);
        _restoreSel(shell);
        const ok = document.execCommand('formatBlock', false, blockTag);
        syncSource(shell);
        _setStatus(shell, ok ? 'Changed to ' + blockTag.toUpperCase() : 'formatBlock had no effect — click in text first', !ok);
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

      // ── HR divider helpers ──────────────────────────────────────────────
      const HR_STYLES = {{
        thick10: 'border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;',
        thick8:  'border-top: 8px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;',
        thin:    '',
      }};

      function selectedHr(shell) {{
        return getSurface(shell)?.querySelector('hr.is-selected-hr') || null;
      }}

      function clearSelectedHrs(scope) {{
        scope?.querySelectorAll('hr.is-selected-hr').forEach((el) => el.classList.remove('is-selected-hr'));
      }}

      function applyHrChange(shell, hrType) {{
        const surface = getSurface(shell);
        if (!surface) return;
        const hr = selectedHr(shell);
        pushUndo(shell);
        if (hr) {{
          // Modify an existing selected HR
          const style = HR_STYLES[hrType];
          if (style) {{
            hr.setAttribute('style', style);
          }} else {{
            hr.removeAttribute('style');
          }}
          clearSelectedHrs(surface);
        }} else {{
          // Insert a new HR after the block that contains the cursor
          const newHr = document.createElement('hr');
          const style = HR_STYLES[hrType];
          if (style) newHr.setAttribute('style', style);
          const saved = savedRanges.get(shell);
          let inserted = false;
          if (saved) {{
            let anchor = saved.endContainer;
            while (anchor.parentNode && anchor.parentNode !== surface) {{
              anchor = anchor.parentNode;
            }}
            if (anchor.parentNode === surface) {{
              anchor.after(newHr);
              inserted = true;
            }}
          }}
          if (!inserted) surface.appendChild(newHr);
        }}
        syncSource(shell);
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
          const ratio = currW > 0 ? currH / currW : 1;
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
        // Locate the first icon heading in the editor that has a templateassets img.
        // In the editor-surface all TemplateAssets/ image srcs are replaced with
        // base64 data URIs, so we reverse-map via assetMap instead of checking src paths.
        const surface = getSurface(shell);
        if (!surface) return false;
        const catalog = iconCatalog();
        const entry = catalog.find((e) => e.basename === basename);
        if (!entry) return false;
        const assetMap = parseAssetMap(shell);

        // Build a set of data-URI values that correspond to TemplateAssets paths
        const templateAssetDataUris = new Set(
          Object.entries(assetMap)
            .filter(([rawRef]) => rawRef.toLowerCase().includes('templateassets'))
            .map(([, dataUri]) => dataUri)
        );
        // An img is a template-assets icon if its src is one of those data URIs,
        // or (fallback for newly-regenerated pages) if the src itself references TemplateAssets
        const isIconImg = (img) =>
          templateAssetDataUris.has(img.src) ||
          img.src.toLowerCase().includes('templateassets');

        const headings = Array.from(surface.querySelectorAll('h1, h2, h3, h4, h5, h6'));
        // Priority 1: heading nearest last cursor position (savedRanges)
        // This lets the user click into a specific section heading, then pick its icon.
        let targetHeading = null;
        const savedRange = savedRanges.get(shell);
        if (savedRange) {{
          const anchorNode = savedRange.startContainer;
          const anchorEl = anchorNode.nodeType === 1 ? anchorNode : anchorNode.parentElement;
          if (surface.contains(anchorEl)) {{
            // Walk up from cursor — are we directly inside a heading?
            let cursorHeading = anchorEl.closest('h1,h2,h3,h4,h5,h6');
            if (!cursorHeading) {{
              // Find the last heading that precedes the cursor in document order
              cursorHeading = [...headings].reverse().find((h) =>
                (h.compareDocumentPosition(anchorEl) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
              );
            }}
            targetHeading = cursorHeading || null;
          }}
        }}
        // Priority 2: first heading that already has a template-assets icon img
        if (!targetHeading) {{
          targetHeading = headings.find((h) =>
            Array.from(h.querySelectorAll('img')).some(isIconImg)
          );
        }}
        // Priority 3: first heading of any kind
        if (!targetHeading) targetHeading = headings[0];
        // Priority 4: no heading at all — create one at the cursor position
        if (!targetHeading) {{
          targetHeading = document.createElement('h2');
          targetHeading.setAttribute('style', 'color: #ac1a2f;');
          // Try to insert at cursor position from savedRanges
          const savedRange = savedRanges.get(shell);
          let cursorInserted = false;
          if (savedRange) {{
            let anchor = savedRange.startContainer;
            while (anchor.parentNode && anchor.parentNode !== surface) {{
              anchor = anchor.parentNode;
            }}
            if (anchor.parentNode === surface) {{
              anchor.before(targetHeading);
              cursorInserted = true;
            }}
          }}
          if (!cursorInserted) {{
            // Fallback: insert after any leading <hr> (accent divider), otherwise as first child
            const firstHr = surface.querySelector(':scope > hr:first-child');
            if (firstHr) {{
              firstHr.after(targetHeading);
            }} else {{
              surface.insertBefore(targetHeading, surface.firstChild);
            }}
          }}
        }}
        pushUndo(shell);
        // Find existing icon img (using the same data-URI-aware matcher)
        const iconImg = Array.from(targetHeading.querySelectorAll('img')).find(isIconImg) || null;
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

      async function exportDraft() {{
        const payload = draftPayload();
        const status = document.querySelector('[data-draft-status]');
        if (!payload.pages.length) {{
          if (status) {{
            status.textContent = 'No changed pages to export yet.';
          }}
          return;
        }}
        const draftName = reviewInputs().draft_filename || 'review-draft.json';
        const jsonStr = JSON.stringify(payload, null, 2);
        // Use File System Access API when available (Chrome/Edge) so the user can
        // save directly to the output directory — no hunting in ~/Downloads.
        if ('showSaveFilePicker' in window) {{
          try {{
            const handle = await window.showSaveFilePicker({{
              suggestedName: draftName,
              types: [{{ description: 'Review Draft JSON', accept: {{ 'application/json': ['.json'] }} }}],
            }});
            const writable = await handle.createWritable();
            await writable.write(jsonStr);
            await writable.close();
            if (status) {{
              status.textContent = 'Saved ' + payload.pages.length + ' edited page(s). Ready to apply in the UI.';
            }}
            return;
          }} catch (e) {{
            if (e.name === 'AbortError') return; // user cancelled — do nothing
            // Any other error: fall through to the standard download below
          }}
        }}
        // Fallback: trigger browser download (Safari, Firefox, or file:// contexts
        // where showSaveFilePicker is unavailable).
        const blob = new Blob([jsonStr], {{ type: 'application/json' }});
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = draftName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        if (status) {{
          status.textContent = 'Downloaded ' + payload.pages.length + ' edited page(s) as ' + draftName + '.';
        }}
      }}

      // ── Per-shell setup ───────────────────────────────────────────────
      document.querySelectorAll('.editor-shell').forEach((shell) => {{
        const surface = shell.querySelector('.editor-surface');
        const source = shell.querySelector('.editor-source');
        if (!surface || !source) return;  // guard: skip shells without editor DOM

        // Inject the preview HTML via JS rather than embedding it in the outer
        // page's HTML source.  When complex page content (nested <footer>,
        // tables, etc.) is embedded directly, Chrome's HTML5 parser can
        // implicitly close the editor-surface <div> early, pushing the
        // trailing <hr> and other tail content outside the editor box.
        const previewHtmlEl = shell.querySelector('.editor-preview-html');
        if (previewHtmlEl) surface.innerHTML = JSON.parse(previewHtmlEl.textContent);

        // Text / block formatting buttons: bind mousedown + click directly so
        // each button owns its own focus-preservation logic.  mousedown
        // preventDefault stops the button from stealing keyboard focus (and
        // thus the text selection) away from the editor surface.
        // The click handler restores the saved range, then calls execCommand /
        // execBlock which each use document.execCommand internally.
        shell.querySelectorAll('[data-editor-command]').forEach((btn) => {{
          btn.addEventListener('mousedown', (e) => {{ e.preventDefault(); }});
          btn.addEventListener('click', () => {{
            execCommand(shell, btn.getAttribute('data-editor-command'));
          }});
        }});
        shell.querySelectorAll('[data-editor-block]').forEach((btn) => {{
          btn.addEventListener('mousedown', (e) => {{ e.preventDefault(); }});
          btn.addEventListener('click', () => {{
            execBlock(shell, btn.getAttribute('data-editor-block'));
          }});
        }});

        // Status bar: live feedback on editor state.
        surface.addEventListener('focus', () => {{
          _setStatus(shell, 'Editor active — place cursor or select text, then click a toolbar button', false);
        }});
        surface.addEventListener('blur', () => {{
          const bar = shell.querySelector('[data-editor-status]');
          if (bar) {{
            bar.textContent = 'Editor inactive — click inside the white editor area below first';
            bar.classList.remove('is-active', 'is-error');
          }}
        }});
        surface.addEventListener('mouseup', () => {{
          const sel = window.getSelection();
          if (sel && sel.toString().trim()) {{
            _setStatus(shell, 'Selected: \u201c' + sel.toString().trim().slice(0, 40) + (sel.toString().length > 40 ? '\u2026' : '') + '\u201d — now click a toolbar button', false);
          }}
        }});
        surface.addEventListener('keyup', () => {{
          const sel = window.getSelection();
          if (sel && sel.rangeCount) {{
            const txt = sel.toString().trim();
            _setStatus(shell, txt
              ? 'Selected: \u201c' + txt.slice(0, 40) + (txt.length > 40 ? '\u2026' : '') + '\u201d'
              : 'Cursor placed — use toolbar buttons', false);
          }}
        }});

        // Keyboard shortcuts inside the editor surface.
        surface.addEventListener('keydown', (event) => {{
          if ((event.metaKey || event.ctrlKey) && event.key === 'z') {{
            event.preventDefault();
            popUndo(shell);
          }}
        }});

        // Media / HR click handling.
        // Note: iframes/videos have pointer-events:none so event.target will
        // be whatever element is behind them — closest() won't find them.
        // Fall back to a bounding-rect hit test for those elements.
        surface.addEventListener('mousedown', (event) => {{
          // HR click: select it for potential style change, or deselect on elsewhere.
          if (event.target.tagName === 'HR') {{
            clearSelectedImages(surface);
            const wasSelected = event.target.classList.contains('is-selected-hr');
            clearSelectedHrs(surface);
            if (!wasSelected) {{
              event.target.classList.add('is-selected-hr');
              const st = (event.target.getAttribute('style') || '').toLowerCase();
              let label = 'grey thin divider';
              if (st.includes('10px')) label = '10 px red divider';
              else if (st.includes('8px')) label = '8 px red divider';
              else if (st.includes('ac1a2f')) label = 'red divider';
              _setStatus(shell, 'Selected: ' + label + ' \u2014 use Divider buttons to change', false);
            }} else {{
              _setStatus(shell, 'Divider deselected', false);
            }}
            event.preventDefault();
            return;
          }}
          clearSelectedHrs(surface);

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
          // Explicitly record a range AT this element before surface.focus()
          // fires a selectionchange that could move savedRanges to the surface
          // start, making the icon picker target the wrong heading.
          try {{
            const r = document.createRange();
            r.selectNode(clickedMedia);
            savedRanges.set(shell, r);
          }} catch (_) {{}}
          surface.focus();
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

        const hrButton = event.target.closest('[data-editor-hr]');
        if (hrButton) {{
          applyHrChange(
            hrButton.closest('.editor-shell'),
            hrButton.getAttribute('data-editor-hr')
          );
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
          // No banner on the page — insert at cursor position or top of surface
          pushUndo(shell);
          bannerImg = document.createElement('img');
          bannerImg.style.cssText = 'display:block; width:100%; max-width:100%; height:auto; margin:0 0 8px 0;';
          bannerImg.alt = 'Page banner';
          const savedRange = savedRanges.get(shell);
          let cursorInserted = false;
          if (savedRange) {{
            let anchor = savedRange.startContainer;
            while (anchor.parentNode && anchor.parentNode !== surface) {{
              anchor = anchor.parentNode;
            }}
            if (anchor.parentNode === surface) {{
              anchor.before(bannerImg);
              cursorInserted = true;
            }}
          }}
          if (!cursorInserted) {{
            surface.insertBefore(bannerImg, surface.firstChild);
          }}
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
      // Scroll the first expanded card into view after initial render.
      requestAnimationFrame(() => {{
        const firstExpanded = allCards.find((c) => !c.classList.contains('is-collapsed'));
        if (firstExpanded) firstExpanded.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
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
      const activeFocusFilters = new Set();

      function applyPageFilters() {{
        const query = (searchInput?.value || '').toLowerCase();
        let visibleCount = 0;
        allCards.forEach((card) => {{
          const name = (card.getAttribute('data-page-name') || '').toLowerCase();
          const priority = card.getAttribute('data-priority') || 'low';
          const passes = (
            (!query || name.includes(query)) &&
            (activePriority === 'all' || priority === activePriority) &&
            (!activeFocusFilters.has('layout-risk')   || card.getAttribute('data-has-layout-risk')   === '1') &&
            (!activeFocusFilters.has('content-loss')  || card.getAttribute('data-has-content-loss')  === '1') &&
            (!activeFocusFilters.has('manual-fix')    || card.getAttribute('data-has-manual-fix')    === '1') &&
            (!activeFocusFilters.has('accessibility') || card.getAttribute('data-has-accessibility') === '1') &&
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
      document.querySelectorAll('[data-filter-focus]').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const key = btn.getAttribute('data-filter-focus');
          if (activeFocusFilters.has(key)) {{
            activeFocusFilters.delete(key);
            btn.classList.remove('is-active');
          }} else {{
            activeFocusFilters.add(key);
            btn.classList.add('is-active');
          }}
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
    print(f"Review pack shortlist CSV: {_default_output_shortlist_csv(json_path)}")


if __name__ == "__main__":
    main()

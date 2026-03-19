from __future__ import annotations

import html
import json
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from zipfile import ZipFile

from .html_tools import AppliedChange, ManualReviewIssue


_LINK_ATTR_PATTERN = re.compile(
    r"(?P<prefix>\b(?P<attr>href|src)\s*=\s*)(?P<quote>[\"'])(?P<url>[^\"']+)(?P=quote)",
    flags=re.IGNORECASE,
)
_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)

_SUPPORTED_TEMPLATE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".css",
    ".js",
    ".webp",
    ".mp4",
    ".webm",
    ".txt",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}
_MATERIALIZED_ASSET_DIR = "TemplateAssets"
_IGNORED_UNRESOLVED_BASENAMES = {
    "all.min.css",
    "bootstrap.min.css",
    "styles.min.css",
    "custom.css",
    "jquery-3.3.1.slim.min.js",
    "popper.min.js",
    "bootstrap.min.js",
    "scripts.min.js",
    "print.css",
    "print.js",
    "logo.png",
    "rule_brown_gradient.png",
}
_ICON_STYLE_SKIP_BASENAMES = {
    "course-card.png",
    "footer.png",
    "instructor-photo.png",
    "instructor-resource-course.png",
    "student-resource-course.png",
    "video-placeholder.png",
}
_DEFAULT_ICON_LABELS = {
    "checklist.png": "Checklist",
    "exclamation.png": "Important",
    "info.png": "Information",
}
_TEMPLATE_HEADING_ICON_STYLE = (
    "width: 45px; height: auto; vertical-align: middle; margin-right: 8px;"
)
_PAGE_TITLE_HEADING_STYLE = (
    "color: #ac1a2f",
    "border-bottom: 10px solid #AC1A2F",
    "padding: 10px",
)
_SECTION_HEADING_STYLE = ("color: #ac1a2f",)
_INTRO_HEADING_SPECS = {
    "introduction": {
        "icon_basename": "star.png",
        "label": "Introduction",
        "styles": _PAGE_TITLE_HEADING_STYLE,
    },
    "learning objectives": {
        "icon_basename": "bullseye.png",
        "label": "Module Objectives",
        "styles": _SECTION_HEADING_STYLE,
    },
    "module objectives": {
        "icon_basename": "bullseye.png",
        "label": "Module Objectives",
        "styles": _SECTION_HEADING_STYLE,
    },
    "checklist": {
        "icon_basename": "checkmark.png",
        "label": "Module Checklist",
        "styles": _SECTION_HEADING_STYLE,
    },
    "module checklist": {
        "icon_basename": "checkmark.png",
        "label": "Module Checklist",
        "styles": _SECTION_HEADING_STYLE,
    },
}
_ICON_BLOCK_PATTERN = (
    r"<(?P<wrapper>p|div)\b[^>]*>\s*"
    r"(?:\s|&nbsp;|</?(?:span|strong|em|b)\b[^>]*>)*"
    r"(?P<img><img\b[^>]*src\s*=\s*[\"'][^\"']*templateassets/[^\"']+[\"'][^>]*>)"
    r"(?:\s|&nbsp;|</?(?:span|strong|em|b)\b[^>]*>)*"
    r"</(?P=wrapper)>"
)
_HEADING_PATTERN = re.compile(
    r"<h(?P<level>[1-6])(?P<attrs>[^>]*)>(?P<body>.*?)</h(?P=level)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _normalize_basename(value: str) -> str:
    return posixpath.basename(value.strip().replace("\\", "/")).strip().lower()


def _is_brightspace_template_url(url: str) -> bool:
    cleaned = html.unescape(url.strip())
    base_part = cleaned.split("#", 1)[0].split("?", 1)[0]
    parsed = urlparse(base_part)
    parsed_path = parsed.path or ""
    if parsed.params:
        parsed_path = f"{parsed_path};{parsed.params}"
    path_source = parsed_path if (parsed.scheme or parsed.netloc) else base_part
    path_text = unquote(path_source).strip()
    normalized = path_text.lstrip("/").lower()
    return normalized.startswith("shared/brightspace_html_template/")


def _extract_template_basename(url: str) -> str:
    cleaned = html.unescape(url.strip())
    base_part = cleaned.split("#", 1)[0].split("?", 1)[0]
    parsed = urlparse(base_part)
    parsed_path = parsed.path or ""
    if parsed.params:
        parsed_path = f"{parsed_path};{parsed.params}"
    path_source = parsed_path if (parsed.scheme or parsed.netloc) else base_part
    path_text = unquote(path_source).strip().replace("\\", "/")
    return _normalize_basename(path_text)


def _load_template_assets_by_basename(
    template_package: Path,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    if not template_package.exists():
        raise ValueError(f"Template package does not exist: {template_package}")

    by_basename: dict[str, list[str]] = defaultdict(list)
    with ZipFile(template_package, "r") as zf:
        for name in zf.namelist():
            clean = name.strip()
            if not clean or clean.endswith("/"):
                continue
            suffix = Path(clean).suffix.lower()
            if suffix not in _SUPPORTED_TEMPLATE_EXTENSIONS:
                continue
            basename = _normalize_basename(clean)
            if not basename:
                continue
            by_basename[basename].append(clean)

    collisions = {
        key: len(values) for key, values in by_basename.items() if len(values) > 1
    }
    return dict(by_basename), collisions


def _load_alias_map(
    alias_map_json_path: Path | None,
) -> tuple[dict[str, tuple[str, ...]], str]:
    if alias_map_json_path is None:
        return {}, ""
    if not alias_map_json_path.exists():
        raise ValueError(
            f"Template alias map JSON does not exist: {alias_map_json_path}"
        )

    payload = json.loads(alias_map_json_path.read_text(encoding="utf-8"))
    raw_mapping = (
        payload.get("aliases")
        if isinstance(payload, dict) and isinstance(payload.get("aliases"), dict)
        else payload
    )
    if not isinstance(raw_mapping, dict):
        raise ValueError(
            "Template alias map JSON must be an object or include an object at key 'aliases'."
        )

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


def _canonical_icon_label(raw_label: str) -> str:
    text = html.unescape(raw_label).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    text = re.sub(r"\s+(?:or|and/or)\s+.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+also represents.*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" -:;,.")
    return text


def _extract_img_basename(tag_html: str) -> str:
    src_match = re.search(
        r'\bsrc\s*=\s*(["\'])(?P<src>[^"\']+)\1',
        tag_html,
        flags=re.IGNORECASE,
    )
    if src_match is None:
        return ""
    parsed = urlparse(src_match.group("src").strip())
    return _normalize_basename(unquote(parsed.path))


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value, flags=re.IGNORECASE)
    unescaped = html.unescape(without_tags).replace("\xa0", " ")
    return re.sub(r"\s+", " ", unescaped).strip()


def _normalize_heading_key(value: str) -> str:
    lowered = _plain_text(value).lower()
    lowered = lowered.replace("&", "and")
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _canonical_heading_label(raw_label: str, *, icon_basename: str = "") -> str:
    label = _canonical_icon_label(raw_label)
    key = _normalize_heading_key(label)
    basename = _normalize_basename(icon_basename)

    if basename == "video.png" or key in {"view", "watch"}:
        return "View"
    if basename == "bookmark.png" and key in {"syllabus", "title", "bookmark"}:
        return "Syllabus" if key == "syllabus" else "Title"
    if key in {"objectives", "learning objectives", "module objectives"}:
        return "Module Objectives"
    if key in {
        "course materials and outcomes",
        "course material and outcomes",
        "course material",
    }:
        return "Course Materials and Outcomes"
    # Template distinguishes these two icons:
    #   checklist.png  = generic checklist / policies list
    #   checkmark.png  = Module Checklist (what to do each module)
    if basename == "checklist.png" or key == "checklist":
        return "Checklist"
    if basename == "checkmark.png" or key == "module checklist":
        return "Module Checklist"
    if basename == "star.png" or key == "introduction":
        return "Introduction"
    if basename == "folder.png" or key in {
        "additional resources",
        "explore resources",
        "resources",
        "supplemental resource",
        "supplemental resources",
    }:
        return "Additional Resources"
    if basename == "circle-arrow.png" or key in {
        "practice",
        "practice games",
        "practice and review",
        "review",
        "flashcards",
    }:
        return "Practice"
    if basename == "exclamation.png" or key in {
        "important",
        "important note",
        "important information",
    }:
        return "Important"
    if basename == "info.png" or key in {"information", "info"}:
        return "Information"
    if basename == "calendar.png" or key in {"calendar", "due date", "due dates"}:
        return "Due Dates"
    if basename == "educator.png" or key in {
        "instructor information",
        "about the instructor",
        "contact information",
    }:
        return "Instructor Information"
    if basename == "gear.png" or key in {
        "technical support",
        "support",
        "canvas support",
    }:
        return "Technical Support"
    if basename == "flag.png" or key in {
        "main point",
        "guidelines",
        "guideline",
        "student support",
    }:
        if key == "main point":
            return "Main Point"
        if key == "student support":
            return "Student Support"
        return "Guidelines"
    if basename == "mail.png" or key in {"communication", "course communication"}:
        return "Communication"
    if basename == "question.png" or key in {"hints", "help links", "help"}:
        return "Help Links"
    if basename == "megaphone.png" or key in {"announcement", "announcements"}:
        return "Announcement"
    if basename == "reminder.png" or key in {"reminder", "reminders"}:
        return "Reminder"
    if basename == "book.png" or key in {"read", "read this"}:
        return "Read"
    if basename == "headphones.png" or key in {"listen", "hear this", "listen to this"}:
        return "Listen"
    if basename == "download.png" or key in {"download", "download this"}:
        return "Download"
    if basename == "paper.png" or key in {"do this"}:
        return "Do This"
    if basename == "pencil.png" or key in {"assignment", "instructions"}:
        return "Instructions"
    if basename == "ai-brain.png" or key in {
        "ai usage allowed",
        "ai usage",
        "artificial intelligence",
    }:
        return "AI Usage Allowed"
    return label


def _contains_heading_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    normalized = _normalize_heading_key(value)
    if not normalized:
        return False
    return any(phrase in normalized for phrase in phrases)


def _resolve_semantic_icon_basename(
    *,
    current_basename: str,
    label_text: str = "",
    original_title: str = "",
) -> str:
    combined = " ".join(part for part in (label_text, original_title) if part).strip()
    if not combined:
        return current_basename
    normalized_combined = _normalize_heading_key(combined)
    normalized_label = _normalize_heading_key(label_text)

    if _contains_heading_phrase(
        combined,
        (
            "practice",
            "review",
            "practice games",
            "flashcards",
            "publisher materials",
        ),
    ):
        return "circle-arrow.png"

    if _contains_heading_phrase(
        combined,
        (
            "resource",
            "resources",
            "explore resources",
            "additional resources",
            "supplemental resource",
            "supplemental resources",
            "sample forms",
            "forms",
            "files",
            "documents",
            "materials",
        ),
    ):
        return "folder.png"

    if _contains_heading_phrase(
        combined,
        (
            "quiz",
            "assessment",
            "knowledge check",
            "self check",
            "check your understanding",
        ),
    ):
        return "rocket.png"

    if _contains_heading_phrase(
        combined, ("read", "reading", "article", "chapter", "textbook")
    ):
        return "book.png"

    if _contains_heading_phrase(
        combined,
        (
            "technical support",
            "canvas support",
            "help desk",
            "helpdesk",
            "tech support",
        ),
    ):
        return "gear.png"

    if _contains_heading_phrase(
        combined,
        (
            "instructor information",
            "about the instructor",
            "contact information",
            "office hours",
        ),
    ):
        return "educator.png"

    if _contains_heading_phrase(
        combined,
        (
            "communication",
            "communicate",
            "course q and a",
            "course q a",
            "question board",
        ),
    ):
        return "mail.png"

    if _contains_heading_phrase(
        combined,
        ("main point", "guidelines", "classroom community", "student support"),
    ):
        return "flag.png"

    if _contains_heading_phrase(
        combined, ("calendar", "due date", "due dates", "course summary")
    ):
        return "calendar.png"

    if _contains_heading_phrase(combined, ("announcement", "announcements")):
        return "megaphone.png"

    if _contains_heading_phrase(
        combined, ("instructions", "assignment directions", "assignment instructions")
    ):
        return "pencil.png"

    if _contains_heading_phrase(combined, ("ai usage", "artificial intelligence")):
        return "ai-brain.png"

    # A generic "View" label in the source normally means the template bookmark
    # treatment, not a video cue. Only promote to the video icon when the title
    # adds an actual video-specific signal.
    if _contains_heading_phrase(
        combined,
        (
            "watch",
            "video",
            "lecture",
            "clip",
            "recording",
            "youtube",
            "vimeo",
            "ted talk",
        ),
    ):
        return "video.png"
    if current_basename in {
        "bookmark.png",
        "video.png",
        "view.png",
    } or normalized_label in {"view", "watch"}:
        if normalized_combined in {"view", "watch"} or normalized_combined.startswith(
            "view "
        ):
            return "bookmark.png"
        return "bookmark.png"

    if current_basename == "rocket.png":
        return "folder.png"
    if current_basename == "reminder.png":
        return "circle-arrow.png"
    return current_basename


def _render_icon_heading_block(
    *,
    level: int,
    attrs: str,
    img_tag: str,
    canonical_label: str,
    original_title: str = "",
) -> str:
    normalized_canonical = _normalize_heading_key(canonical_label)
    normalized_original = _normalize_heading_key(original_title)
    heading_html = f"<h{level}{attrs}>{img_tag} <strong>{html.escape(canonical_label)}</strong></h{level}>"
    if (
        original_title
        and normalized_original
        and normalized_canonical
        and normalized_original != normalized_canonical
    ):
        sub_level = min(level + 1, 6)
        heading_html += f"\n<h{sub_level}>{html.escape(original_title)}</h{sub_level}>"
    return heading_html


def _merge_style_attr(
    attrs: str,
    *,
    required_styles: tuple[str, ...],
    remove_style_keys: set[str] | None = None,
) -> str:
    working_attrs = attrs or ""
    style_match = re.search(
        r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
        working_attrs,
        flags=re.IGNORECASE,
    )
    removed_keys = {
        key.strip().lower() for key in (remove_style_keys or set()) if key.strip()
    }
    style_tokens: list[str] = []
    seen_keys: set[str] = set()

    if style_match is not None:
        for token in style_match.group("style").split(";"):
            cleaned = token.strip()
            if not cleaned or ":" not in cleaned:
                continue
            key = cleaned.split(":", 1)[0].strip().lower()
            if key in removed_keys:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            style_tokens.append(cleaned)

    for token in required_styles:
        key = token.split(":", 1)[0].strip().lower()
        style_tokens = [
            item
            for item in style_tokens
            if item.split(":", 1)[0].strip().lower() != key
        ]
        style_tokens.append(token)
        seen_keys.add(key)

    rebuilt_style = "; ".join(style_tokens).strip()
    if rebuilt_style and not rebuilt_style.endswith(";"):
        rebuilt_style += ";"

    if style_match is not None:
        return (
            working_attrs[: style_match.start("style")]
            + rebuilt_style
            + working_attrs[style_match.end("style") :]
        )
    if rebuilt_style:
        return f'{working_attrs} style="{rebuilt_style}"'
    return working_attrs


def _build_heading_icon_tag(*, basename: str) -> str:
    return (
        f'<img src="TemplateAssets/{basename}" role="presentation" alt="" '
        f'style="{_TEMPLATE_HEADING_ICON_STYLE}">'
    )


def _extract_non_template_heading_images(body: str) -> list[str]:
    images = re.findall(r"<img\b[^>]*>", body, flags=re.IGNORECASE)
    return [image for image in images if "templateassets/" not in image.lower()]


def _extract_heading_title_and_media(body: str) -> tuple[str, list[str]]:
    media = _extract_non_template_heading_images(body)
    cleaned = body
    for fragment in media:
        cleaned = cleaned.replace(fragment, " ")
    cleaned = re.sub(r"<br\b[^>]*>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("&nbsp;", " ")
    return _plain_text(cleaned), media


def _render_heading_media_blocks(media: list[str]) -> str:
    if not media:
        return ""
    return "".join(f"\n<p>{fragment}</p>" for fragment in media)


def _extract_numeric_dimension(tag_html: str, *, attr_name: str) -> int | None:
    attr_match = re.search(
        rf'\b{attr_name}\s*=\s*(["\'])(?P<value>\d{{2,4}})\1',
        tag_html,
        flags=re.IGNORECASE,
    )
    if attr_match is not None:
        try:
            return int(attr_match.group("value"))
        except ValueError:
            return None

    style_match = re.search(
        r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
        tag_html,
        flags=re.IGNORECASE,
    )
    if style_match is None:
        return None

    px_match = re.search(
        rf"(?:^|;)\s*{attr_name}\s*:\s*(?P<value>\d{{2,4}})px",
        style_match.group("style"),
        flags=re.IGNORECASE,
    )
    if px_match is None:
        return None
    try:
        return int(px_match.group("value"))
    except ValueError:
        return None


def _preferred_float_image_width(tag_html: str) -> int:
    width = _extract_numeric_dimension(tag_html, attr_name="width")
    if width is None:
        return 320
    return max(240, min(width, 360))


def _load_icon_label_map(template_package: Path) -> dict[str, str]:
    icon_labels: dict[str, str] = {}
    # Compiled once for the whole function.
    _h_re = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
    _icon_re = re.compile(
        r"<img\b[^>]*/icons/(?P<basename>[^/\"'>\s]+\.(?:png|jpg|jpeg|svg|gif|webp))[^>]*>",
        re.IGNORECASE,
    )
    try:
        with ZipFile(template_package, "r") as zf:
            candidate_pages = [
                name
                for name in zf.namelist()
                if name.lower().endswith(".html")
                and "template-image-customizations" in name.lower()
            ]
            for page in candidate_pages:
                html_text = zf.read(page).decode("utf-8", errors="ignore")
                # Search within complete headings so icon + label in sibling
                # elements (e.g. question.png structure) are captured correctly.
                for h_match in _h_re.finditer(html_text):
                    heading_body = h_match.group(1)
                    img_m = _icon_re.search(heading_body)
                    if not img_m:
                        continue
                    basename = _normalize_basename(img_m.group("basename"))
                    if not basename or basename in icon_labels:
                        continue
                    # Strip all img tags then use plain text as the label.
                    label_html = re.sub(
                        r"<img\b[^>]*>", "", heading_body, flags=re.IGNORECASE
                    )
                    canonical = _canonical_icon_label(_plain_text(label_html))
                    if canonical:
                        icon_labels[basename] = canonical
    except Exception:
        icon_labels = {}

    for basename, default_label in _DEFAULT_ICON_LABELS.items():
        icon_labels.setdefault(basename, default_label)
    return icon_labels


@dataclass(frozen=True)
class TemplateOverlayConfig:
    template_package: Path
    alias_map_json_path: Path | None = None
    apply_visual_standards: bool = True
    apply_color_standards: bool = True
    apply_divider_standards: bool = True
    image_layout_mode: str = "safe-block"


@dataclass
class TemplateOverlayContext:
    template_package: Path
    alias_map_source: str
    alias_map: dict[str, tuple[str, ...]]
    assets_by_basename: dict[str, list[str]]
    file_name_collisions: dict[str, int]
    icon_label_by_basename: dict[str, str]
    apply_visual_standards: bool
    apply_color_standards: bool
    apply_divider_standards: bool
    image_layout_mode: str


def build_template_overlay_context(
    config: TemplateOverlayConfig,
) -> TemplateOverlayContext:
    assets_by_basename, collisions = _load_template_assets_by_basename(
        config.template_package
    )
    alias_map, alias_source = _load_alias_map(config.alias_map_json_path)
    icon_label_map = _load_icon_label_map(config.template_package)
    # Backfill labels for legacy icon basenames when alias rules map them to
    # canonical template icons that already have glossary labels.
    for source_basename, targets in alias_map.items():
        if source_basename in icon_label_map:
            continue
        for target_basename in targets:
            label = icon_label_map.get(target_basename, "")
            if label:
                icon_label_map[source_basename] = label
                break
    return TemplateOverlayContext(
        template_package=config.template_package,
        alias_map_source=alias_source,
        alias_map=alias_map,
        assets_by_basename=assets_by_basename,
        file_name_collisions=collisions,
        icon_label_by_basename=icon_label_map,
        apply_visual_standards=bool(config.apply_visual_standards),
        apply_color_standards=bool(config.apply_color_standards),
        apply_divider_standards=bool(config.apply_divider_standards),
        image_layout_mode=str(config.image_layout_mode or "safe-block").strip().lower(),
    )


def _filter_required_styles(
    styles: tuple[str, ...],
    *,
    context: TemplateOverlayContext,
) -> tuple[str, ...]:
    filtered: list[str] = []
    for token in styles:
        key = token.split(":", 1)[0].strip().lower()
        if key == "color" and not context.apply_color_standards:
            continue
        if key == "border-bottom" and not context.apply_divider_standards:
            continue
        filtered.append(token)
    return tuple(filtered)


def _template_remove_style_keys(
    *,
    context: TemplateOverlayContext,
    include_padding: bool = True,
) -> set[str]:
    keys: set[str] = set()
    if context.apply_color_standards:
        keys.add("color")
    if context.apply_divider_standards:
        keys.add("border-bottom")
    if include_padding:
        keys.add("padding")
    return keys


def _template_heading_attrs(
    attrs: str,
    *,
    context: TemplateOverlayContext,
) -> str:
    working_attrs = attrs or ""
    if not context.apply_color_standards:
        return working_attrs
    style_match = re.search(
        r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
        working_attrs,
        flags=re.IGNORECASE,
    )
    if style_match is not None:
        style_text = style_match.group("style")
        if re.search(r"(?:^|;)\s*color\s*:", style_text, flags=re.IGNORECASE):
            return working_attrs
        rebuilt_style = style_text.strip()
        if rebuilt_style and not rebuilt_style.endswith(";"):
            rebuilt_style += ";"
        rebuilt_style += " color: #ac1a2f;"
        return (
            working_attrs[: style_match.start("style")]
            + rebuilt_style
            + working_attrs[style_match.end("style") :]
        )
    return f'{working_attrs} style="color: #ac1a2f;"'


def _resolve_target_basename(
    *,
    source_basename: str,
    context: TemplateOverlayContext,
) -> tuple[str | None, str]:
    direct = context.assets_by_basename.get(source_basename, [])
    if len(direct) == 1:
        return source_basename, "direct"
    if len(direct) > 1:
        return None, "collision"

    alias_candidates = context.alias_map.get(source_basename, ())
    for alias_basename in alias_candidates:
        alias_matches = context.assets_by_basename.get(alias_basename, [])
        if len(alias_matches) == 1:
            return alias_basename, "alias"
        if len(alias_matches) > 1:
            return None, "alias_collision"

    return None, "unmapped"


def apply_template_overlay(
    content: str,
    *,
    file_path: str,
    context: TemplateOverlayContext,
) -> tuple[str, list[AppliedChange], list[ManualReviewIssue], dict]:
    updated = content
    direct_mapped = 0
    alias_mapped = 0
    unresolved = 0
    ignored_unresolved = 0
    unresolved_refs: list[str] = []
    unresolved_basenames: list[str] = []
    ignored_basenames: list[str] = []
    matched_alias_pairs: set[str] = set()

    def replace_attr(match: re.Match[str]) -> str:
        nonlocal direct_mapped
        nonlocal alias_mapped
        nonlocal unresolved
        nonlocal ignored_unresolved

        original_url = str(match.group("url")).strip()
        is_brightspace = _is_brightspace_template_url(original_url)
        is_standard_images = (
            not is_brightspace and "standardimages/" in original_url.lower()
        )
        if not is_brightspace and not is_standard_images:
            return match.group(0)

        source_basename = _extract_template_basename(original_url)
        if not source_basename:
            if is_brightspace:
                unresolved += 1
                unresolved_refs.append(original_url)
                unresolved_basenames.append("")
            return match.group(0)

        # standardImages icons are only remapped when they have an alias entry;
        # un-aliased standardImages files (photos, decorative images) are left as-is.
        if is_standard_images and source_basename not in context.alias_map:
            return match.group(0)

        target_basename, mode = _resolve_target_basename(
            source_basename=source_basename,
            context=context,
        )
        if not target_basename:
            if source_basename in _IGNORED_UNRESOLVED_BASENAMES:
                ignored_unresolved += 1
                ignored_basenames.append(source_basename)
                return match.group(0)
            if is_brightspace:
                unresolved += 1
                unresolved_refs.append(original_url)
                unresolved_basenames.append(source_basename)
            return match.group(0)

        parsed = urlparse(original_url)
        rebuilt = f"{_MATERIALIZED_ASSET_DIR}/{target_basename}"
        if parsed.query:
            rebuilt = f"{rebuilt}?{parsed.query}"
        if parsed.fragment:
            rebuilt = f"{rebuilt}#{parsed.fragment}"

        if mode == "alias":
            alias_mapped += 1
            matched_alias_pairs.add(f"{source_basename}->{target_basename}")
        else:
            direct_mapped += 1
        return f'{match.group("prefix")}{match.group("quote")}{rebuilt}{match.group("quote")}'

    updated = _LINK_ATTR_PATTERN.sub(replace_attr, updated)

    icon_style_updates = 0
    banner_style_updates = 0
    icon_alt_updates = 0
    icon_title_updates = 0
    icon_label_heading_updates = 0
    icon_block_heading_merges = 0
    responsive_image_updates = 0
    promoted_icon_headings = 0
    page_heading_updates = 0
    leading_divider_removals = 0

    if context.apply_visual_standards:
        known_template_asset_basenames = set(context.assets_by_basename.keys())
        known_alias_basenames = set(context.alias_map.keys())
        for alias_targets in context.alias_map.values():
            known_alias_basenames.update(alias_targets)
        known_visual_basenames = (
            known_template_asset_basenames
            | known_alias_basenames
            | set(context.icon_label_by_basename.keys())
        )
        normalized_file_path = file_path.replace("\\", "/").lower()
        learning_activities_match = re.search(
            r"(?:^|/)learning activities(?:\s*-\s*[^/]+)?\.html$",
            normalized_file_path,
        )

        def template_section_level(default_level: int) -> int:
            if learning_activities_match:
                return 2
            return default_level

        def normalize_template_icon_tag(match: re.Match[str]) -> str:
            nonlocal icon_style_updates
            nonlocal banner_style_updates
            nonlocal icon_alt_updates
            nonlocal icon_title_updates
            nonlocal responsive_image_updates

            tag = match.group(0)

            def append_attribute(tag_html: str, attribute_text: str) -> str:
                if re.search(r"/\s*>$", tag_html):
                    return re.sub(r"/\s*>$", f" {attribute_text} />", tag_html)
                return tag_html[:-1] + f" {attribute_text}>"

            src_match = re.search(
                r'\bsrc\s*=\s*(["\'])(?P<src>[^"\']+)\1',
                tag,
                flags=re.IGNORECASE,
            )
            if src_match is None:
                return tag

            src_value = src_match.group("src").strip()
            lowered_src = src_value.lower()
            parsed_src = urlparse(src_value)
            src_basename = _normalize_basename(unquote(parsed_src.path))
            if not src_basename:
                return tag
            is_template_asset = "templateassets/" in lowered_src
            is_known_visual_asset = src_basename in known_visual_basenames
            canonical_icon_label = context.icon_label_by_basename.get(src_basename, "")

            if not is_template_asset and not is_known_visual_asset:
                updated_tag = tag
                initial_style_match = re.search(
                    r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                float_direction = ""
                if initial_style_match is not None:
                    float_match = re.search(
                        r"(?:^|;)\s*float\s*:\s*(left|right)",
                        initial_style_match.group("style"),
                        flags=re.IGNORECASE,
                    )
                    if float_match is not None:
                        float_direction = float_match.group(1).strip().lower()
                has_large_width = bool(
                    re.search(
                        r'\bwidth\s*=\s*(["\'])\s*(?:[2-9]\d{2,}|\d{4,})\s*\1',
                        updated_tag,
                        flags=re.IGNORECASE,
                    )
                    or re.search(
                        r"width\s*:\s*(?:[2-9]\d{2,}|\d{4,})px",
                        updated_tag,
                        flags=re.IGNORECASE,
                    )
                )
                if not has_large_width and float_direction not in {"left", "right"}:
                    return tag

                updated_tag = re.sub(
                    r'\s(?:width|height)\s*=\s*(["\']).*?\1',
                    "",
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                style_match = re.search(
                    r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                style_tokens: list[str] = []
                seen_keys: set[str] = set()
                if style_match is not None:
                    for token in style_match.group("style").split(";"):
                        cleaned = token.strip()
                        if not cleaned:
                            continue
                        key = cleaned.split(":", 1)[0].strip().lower()
                        if key == "float" and ":" in cleaned:
                            float_direction = cleaned.split(":", 1)[1].strip().lower()
                        if key in {
                            "width",
                            "height",
                            "max-width",
                            "float",
                            "display",
                            "clear",
                            "margin",
                            "margin-left",
                            "margin-right",
                            "margin-top",
                            "margin-bottom",
                            "padding",
                            "padding-left",
                            "padding-right",
                            "padding-top",
                            "padding-bottom",
                        }:
                            continue
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        style_tokens.append(cleaned)

                if (
                    float_direction in {"left", "right"}
                    and context.image_layout_mode == "preserve-wrap"
                ):
                    preferred_width = _preferred_float_image_width(tag)
                    style_tokens.extend(
                        [
                            f"width: {preferred_width}px",
                            "max-width: 45%",
                            "height: auto",
                            f"float: {float_direction}",
                            "display: block",
                            "clear: none",
                            (
                                "margin: 4px 0 20px 24px"
                                if float_direction == "right"
                                else "margin: 4px 24px 20px 0"
                            ),
                        ]
                    )
                elif float_direction in {"left", "right"}:
                    preferred_width = _preferred_float_image_width(tag)
                    style_tokens.extend(
                        [
                            f"width: {preferred_width}px",
                            "max-width: 100%",
                            "height: auto",
                            "display: block",
                            "clear: both",
                            (
                                "margin: 20px 0 20px auto"
                                if float_direction == "right"
                                else "margin: 20px auto 20px 0"
                            ),
                        ]
                    )
                else:
                    style_tokens.extend(
                        [
                            "max-width: 100%",
                            "height: auto",
                            "display: block",
                            "margin: 20px auto",
                            "clear: both",
                        ]
                    )
                rebuilt_style = "; ".join(style_tokens).strip()
                if rebuilt_style and not rebuilt_style.endswith(";"):
                    rebuilt_style += ";"
                if style_match is not None:
                    updated_tag = (
                        updated_tag[: style_match.start("style")]
                        + rebuilt_style
                        + updated_tag[style_match.end("style") :]
                    )
                elif rebuilt_style:
                    updated_tag = append_attribute(
                        updated_tag, f'style="{rebuilt_style}"'
                    )

                if updated_tag != tag:
                    responsive_image_updates += 1
                return updated_tag

            if src_basename.startswith("banner"):
                updated_tag = tag
                updated_tag = re.sub(
                    r'\s(?:width|height)\s*=\s*(["\']).*?\1',
                    "",
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                style_match = re.search(
                    r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                style_tokens: list[str] = []
                if style_match is not None:
                    existing_style = style_match.group("style")
                    for token in existing_style.split(";"):
                        cleaned = token.strip()
                        if not cleaned:
                            continue
                        lowered = cleaned.lower().replace(" ", "")
                        if lowered.startswith(
                            (
                                "max-width:",
                                "min-width:",
                                "width:",
                                "height:",
                                "display:",
                            )
                        ):
                            continue
                        style_tokens.append(cleaned)

                style_tokens.extend(["width: 100%", "height: auto", "display: block"])
                rebuilt_style = "; ".join(style_tokens).strip()
                if rebuilt_style and not rebuilt_style.endswith(";"):
                    rebuilt_style += ";"

                if style_match is not None:
                    updated_tag = (
                        updated_tag[: style_match.start("style")]
                        + rebuilt_style
                        + updated_tag[style_match.end("style") :]
                    )
                elif rebuilt_style:
                    updated_tag = append_attribute(
                        updated_tag, f'style="{rebuilt_style}"'
                    )

                updated_tag = re.sub(
                    r'\s+alt\s*=\s*(["\']).*?\1',
                    ' alt=""',
                    updated_tag,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if not re.search(r"\balt\s*=", updated_tag, flags=re.IGNORECASE):
                    updated_tag = append_attribute(updated_tag, 'alt=""')
                updated_tag, role_updates = re.subn(
                    r'\s+role\s*=\s*(["\']).*?\1',
                    ' role="presentation"',
                    updated_tag,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if role_updates == 0:
                    updated_tag = append_attribute(updated_tag, 'role="presentation"')

                if updated_tag != tag:
                    banner_style_updates += 1
                return updated_tag

            if src_basename in _ICON_STYLE_SKIP_BASENAMES:
                return tag

            updated_tag = re.sub(
                r'\s(?:width|height)\s*=\s*(["\']).*?\1',
                "",
                tag,
                flags=re.IGNORECASE,
            )
            style_match = re.search(
                r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
                updated_tag,
                flags=re.IGNORECASE,
            )
            style_tokens: list[str] = []
            has_float_style = False
            if style_match is not None:
                existing_style = style_match.group("style")
                for token in existing_style.split(";"):
                    cleaned = token.strip()
                    if not cleaned:
                        continue
                    key = cleaned.split(":", 1)[0].strip().lower()
                    if key == "float":
                        has_float_style = True
                    style_tokens.append(cleaned)

            normalized_style_tokens: list[str] = []
            seen_style_keys: set[str] = set()
            for token in style_tokens:
                key = token.split(":", 1)[0].strip().lower()
                lowered = token.lower().replace(" ", "")
                if key == "max-width" and not has_float_style:
                    continue
                if (
                    key == "height"
                    and lowered.startswith("height:auto")
                    and not has_float_style
                ):
                    continue
                if key in seen_style_keys:
                    continue
                seen_style_keys.add(key)
                normalized_style_tokens.append(token)

            if has_float_style:
                if "width" not in seen_style_keys:
                    normalized_style_tokens.append("width: 45px")
                if "max-width" not in seen_style_keys:
                    normalized_style_tokens.append("max-width: 100%")
                if "height" not in seen_style_keys:
                    normalized_style_tokens.append("height: auto")
            else:
                if "width" not in seen_style_keys:
                    normalized_style_tokens.append("width: 45px")
                if "height" not in seen_style_keys:
                    normalized_style_tokens.append("height: auto")
                if "vertical-align" not in seen_style_keys:
                    normalized_style_tokens.append("vertical-align: middle")
                if "margin-right" not in seen_style_keys:
                    normalized_style_tokens.append("margin-right: 8px")

            rebuilt_style = "; ".join(normalized_style_tokens).strip()
            if rebuilt_style and not rebuilt_style.endswith(";"):
                rebuilt_style += ";"

            if style_match is not None:
                updated_tag = (
                    updated_tag[: style_match.start("style")]
                    + rebuilt_style
                    + updated_tag[style_match.end("style") :]
                )
            elif rebuilt_style:
                updated_tag = append_attribute(updated_tag, f'style="{rebuilt_style}"')

            if updated_tag != tag:
                icon_style_updates += 1

            alt_match = re.search(
                r'(?<=\s)alt\s*=\s*(["\'])(?P<alt>[^"\']*)\1',
                updated_tag,
                flags=re.IGNORECASE,
            )
            alt_text = alt_match.group("alt").strip() if alt_match is not None else ""
            if alt_match is not None:
                if alt_text:
                    updated_tag = (
                        updated_tag[: alt_match.start("alt")]
                        + ""
                        + updated_tag[alt_match.end("alt") :]
                    )
                    icon_alt_updates += 1
            else:
                updated_tag = append_attribute(updated_tag, 'alt=""')
                icon_alt_updates += 1

            title_match = re.search(
                r'\s+title\s*=\s*(["\'])(?P<title>[^"\']*)\1',
                updated_tag,
                flags=re.IGNORECASE,
            )
            if title_match is not None:
                updated_tag = (
                    updated_tag[: title_match.start()]
                    + updated_tag[title_match.end() :]
                )
                icon_title_updates += 1

            role_match = re.search(
                r'(?<=\s)role\s*=\s*(["\'])(?P<role>[^"\']*)\1',
                updated_tag,
                flags=re.IGNORECASE,
            )
            role_value = (
                role_match.group("role").strip().lower()
                if role_match is not None
                else ""
            )
            if role_match is not None:
                if role_value != "presentation":
                    updated_tag = (
                        updated_tag[: role_match.start("role")]
                        + "presentation"
                        + updated_tag[role_match.end("role") :]
                    )
                    icon_style_updates += 1
            else:
                updated_tag = append_attribute(updated_tag, 'role="presentation"')
                icon_style_updates += 1

            return updated_tag

        updated = _IMG_TAG_PATTERN.sub(normalize_template_icon_tag, updated)

        def normalize_icon_only_heading(match: re.Match[str]) -> str:
            nonlocal icon_label_heading_updates
            full_heading = match.group(0)
            img_tag = match.group("img")
            src_basename = _extract_img_basename(img_tag)
            if not src_basename:
                return full_heading
            label = _canonical_heading_label(
                context.icon_label_by_basename.get(src_basename, ""),
                icon_basename=src_basename,
            )
            if not label:
                return full_heading
            heading_attrs = _template_heading_attrs(
                match.group("attrs"), context=context
            )
            replacement = _render_icon_heading_block(
                level=template_section_level(int(match.group("level"))),
                attrs=heading_attrs,
                img_tag=_build_heading_icon_tag(basename=src_basename),
                canonical_label=label,
            )
            if replacement != full_heading:
                icon_label_heading_updates += 1
            return replacement

        updated = re.sub(
            r"<h(?P<level>[1-6])(?P<attrs>[^>]*)>\s*(?P<img><img\b[^>]*>)\s*</h(?P=level)>",
            normalize_icon_only_heading,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def normalize_icon_only_paragraph(match: re.Match[str]) -> str:
            """Convert a <p> containing only a TemplateAssets icon into a proper
            icon+label heading, matching the template icon placement guidelines."""
            nonlocal icon_label_heading_updates
            img_tag = match.group("img")
            src_basename = _extract_img_basename(img_tag)
            if not src_basename:
                return match.group(0)
            label = _canonical_heading_label(
                context.icon_label_by_basename.get(src_basename, ""),
                icon_basename=src_basename,
            )
            if not label:
                return match.group(0)
            attrs = ' style="color: #ac1a2f;"' if context.apply_color_standards else ""
            replacement = _render_icon_heading_block(
                level=template_section_level(3),
                attrs=attrs,
                img_tag=_build_heading_icon_tag(basename=src_basename),
                canonical_label=label,
            )
            icon_label_heading_updates += 1
            return replacement

        # Match <p> or <div> that contains ONLY a TemplateAssets icon (already
        # remapped from standardImages), with no surrounding text content.
        updated = re.sub(
            r"<(?P<wrapper>p|div)\b[^>]*>\s*(?P<img><img\b[^>]*templateassets/[^>]+>)\s*</(?P=wrapper)>",
            normalize_icon_only_paragraph,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def merge_icon_with_label_block(match: re.Match[str]) -> str:
            nonlocal icon_block_heading_merges
            img_tag = match.group("img")
            icon_basename = _extract_img_basename(img_tag)
            label_body = match.group("body").strip()
            if not label_body:
                return match.group(0)
            if re.search(
                r"<(?:a|iframe|ul|ol|table|h[1-6]|details)\b",
                label_body,
                flags=re.IGNORECASE,
            ):
                return match.group(0)
            label_text, label_media = _extract_heading_title_and_media(label_body)
            if not label_text or len(label_text) > 100 or len(label_text.split()) > 12:
                return match.group(0)
            icon_basename = _resolve_semantic_icon_basename(
                current_basename=icon_basename,
                label_text=label_text,
                original_title=label_text,
            )
            canonical_label = _canonical_heading_label(
                context.icon_label_by_basename.get(icon_basename, "") or label_text,
                icon_basename=icon_basename,
            )
            replacement = _render_icon_heading_block(
                level=template_section_level(3),
                attrs=(
                    ' style="color: #ac1a2f;"' if context.apply_color_standards else ""
                ),
                img_tag=_build_heading_icon_tag(basename=icon_basename),
                canonical_label=canonical_label,
                original_title=label_text,
            )
            replacement += _render_heading_media_blocks(label_media)
            icon_block_heading_merges += 1
            return replacement

        updated = re.sub(
            _ICON_BLOCK_PATTERN
            + r"\s*<(?P<label_wrapper>p|div)\b[^>]*>(?P<body>.*?)</(?P=label_wrapper)>",
            merge_icon_with_label_block,
            updated,
            count=1_000,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def merge_icon_block_with_heading(match: re.Match[str]) -> str:
            nonlocal icon_block_heading_merges
            img_tag = match.group("img")
            icon_basename = _extract_img_basename(img_tag)
            heading_body = match.group("body").strip()
            if not heading_body:
                return match.group(0)
            heading_attrs = _template_heading_attrs(
                match.group("hattrs"), context=context
            )
            original_title, heading_media = _extract_heading_title_and_media(
                heading_body
            )
            if not original_title:
                return match.group(0)
            resolved_icon_basename = _resolve_semantic_icon_basename(
                current_basename=icon_basename,
                label_text=context.icon_label_by_basename.get(icon_basename, ""),
                original_title=original_title,
            )
            canonical_label = _canonical_heading_label(
                context.icon_label_by_basename.get(resolved_icon_basename, "")
                or original_title,
                icon_basename=resolved_icon_basename,
            )
            replacement = _render_icon_heading_block(
                level=template_section_level(int(match.group("level"))),
                attrs=heading_attrs,
                img_tag=_build_heading_icon_tag(basename=resolved_icon_basename),
                canonical_label=canonical_label,
                original_title=original_title,
            )
            replacement += _render_heading_media_blocks(heading_media)
            icon_block_heading_merges += 1
            return replacement

        updated = re.sub(
            _ICON_BLOCK_PATTERN
            + r"\s*(?:</div>\s*){0,4}(?:<p\b[^>]*>(?:\s|&nbsp;|</?(?:span|strong|em|b)\b[^>]*>)*</p>\s*)*(?:<h[1-6][^>]*>(?:\s|&nbsp;|</?(?:span|strong|em|b)\b[^>]*>)*</h[1-6]>\s*)*(?:<(?:p|div)\b[^>]*>\s*</(?:p|div)>\s*)*<div\b[^>]*>\s*(?:<br\s*/?>\s*)*<h(?P<level>[1-6])(?P<hattrs>[^>]*)>(?P<body>.*?)</h(?P=level)>\s*</div>",
            merge_icon_block_with_heading,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

        updated = re.sub(
            _ICON_BLOCK_PATTERN
            + r"\s*(?:</div>\s*){0,4}(?:<p\b[^>]*>(?:\s|&nbsp;|</?(?:span|strong|em|b)\b[^>]*>)*</p>\s*)*<h(?P<level>[1-6])(?P<hattrs>[^>]*)>(?P<body>.*?)</h(?P=level)>",
            merge_icon_block_with_heading,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def promote_template_icon_heading(match: re.Match[str]) -> str:
            nonlocal promoted_icon_headings
            level = int(match.group("level"))
            if level != 4 or "templateassets/" not in match.group("body").lower():
                return match.group(0)
            promoted_icon_headings += 1
            attrs = _template_heading_attrs(match.group("attrs"), context=context)
            return f'<h{template_section_level(3)}{attrs}>{match.group("body")}</h{template_section_level(3)}>'

        updated = _HEADING_PATTERN.sub(promote_template_icon_heading, updated)

        def remove_leading_divider(payload: str, *, icon_basename: str) -> str:
            nonlocal leading_divider_removals
            updated_payload, removed = re.subn(
                rf"(<body[^>]*>\s*(?:<div\b[^>]*>\s*){{0,4}}(?:<p\b[^>]*>\s*(?:<span\b[^>]*>\s*)?(?:&nbsp;|\s)*(?:</span>\s*)?</p>\s*)?)<hr\b[^>]*>\s*(?:</div>\s*){{0,4}}(?=(?:\s*<div\b[^>]*>\s*){{0,4}}<h2\b[^>]*>.*?(?:\.\./)?TemplateAssets/{re.escape(icon_basename)})",
                r"\1",
                payload,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if removed:
                leading_divider_removals += removed
            return updated_payload

        if normalized_file_path.endswith("introduction and objectives.html"):
            seen_intro_heading_keys: set[str] = set()

            def normalize_intro_heading(match: re.Match[str]) -> str:
                nonlocal page_heading_updates
                heading_key = _normalize_heading_key(match.group("body"))
                spec = _INTRO_HEADING_SPECS.get(heading_key)
                if spec is None or heading_key in seen_intro_heading_keys:
                    return match.group(0)
                seen_intro_heading_keys.add(heading_key)
                attrs = _merge_style_attr(
                    match.group("attrs"),
                    required_styles=_filter_required_styles(
                        tuple(spec["styles"]), context=context
                    ),
                    remove_style_keys=_template_remove_style_keys(context=context),
                )
                heading_text = html.escape(str(spec["label"]))
                _, heading_media = _extract_heading_title_and_media(match.group("body"))
                replacement = (
                    f'<h2{attrs}>{_build_heading_icon_tag(basename=str(spec["icon_basename"]))} '
                    f"<strong>{heading_text}</strong></h2>"
                )
                replacement += _render_heading_media_blocks(heading_media)
                if replacement != match.group(0):
                    page_heading_updates += 1
                return replacement

            updated = _HEADING_PATTERN.sub(normalize_intro_heading, updated)
            if context.apply_divider_standards:
                updated = remove_leading_divider(updated, icon_basename="star.png")
        if learning_activities_match:
            page_title_done = False

            def normalize_learning_title(match: re.Match[str]) -> str:
                nonlocal page_heading_updates
                nonlocal page_title_done
                if page_title_done:
                    return match.group(0)
                if "templateassets/" in match.group("body").lower():
                    return match.group(0)
                heading_text = _plain_text(match.group("body"))
                if not heading_text:
                    return match.group(0)
                page_title_done = True
                attrs = _merge_style_attr(
                    match.group("attrs"),
                    required_styles=_filter_required_styles(
                        _PAGE_TITLE_HEADING_STYLE, context=context
                    ),
                    remove_style_keys=_template_remove_style_keys(context=context),
                )
                replacement = (
                    f'<h2{attrs}><strong>{_build_heading_icon_tag(basename="bookmark.png")} '
                    f"{html.escape(heading_text)}</strong></h2>"
                )
                if replacement != match.group(0):
                    page_heading_updates += 1
                return replacement

            updated = _HEADING_PATTERN.sub(normalize_learning_title, updated, count=1)
            if context.apply_divider_standards:
                updated = remove_leading_divider(updated, icon_basename="bookmark.png")

    applied_changes: list[AppliedChange] = []
    if direct_mapped:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Mapped Brightspace template links to Canvas template assets (direct basename match)",
                count=direct_mapped,
            )
        )
    if alias_mapped:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Mapped Brightspace template links to Canvas template assets (alias map)",
                count=alias_mapped,
            )
        )
    if icon_style_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Normalized mapped template icon image sizing for Canvas rendering",
                count=icon_style_updates,
            )
        )
    if banner_style_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Normalized mapped template banner image sizing and decorative semantics for Canvas rendering",
                count=banner_style_updates,
            )
        )
    if icon_alt_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Filled missing alt text for mapped template icon images",
                count=icon_alt_updates,
            )
        )
    if icon_title_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Standardized mapped template icon title text using template glossary labels",
                count=icon_title_updates,
            )
        )
    if icon_label_heading_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Added visible text labels next to icon-only headings using template glossary labels",
                count=icon_label_heading_updates,
            )
        )
    if icon_block_heading_merges:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Merged standalone icon blocks into following headings for template-style icon+label layout",
                count=icon_block_heading_merges,
            )
        )
    if promoted_icon_headings:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Promoted template icon section headings to maintain Canvas-safe heading order",
                count=promoted_icon_headings,
            )
        )
    if page_heading_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Normalized page and section headings to match template heading styles",
                count=page_heading_updates,
            )
        )
    if leading_divider_removals:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Removed redundant leading dividers ahead of template-styled page headings",
                count=leading_divider_removals,
            )
        )
    if responsive_image_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description=(
                    "Preserved wrapped image layouts within Canvas-safe width limits"
                    if context.image_layout_mode == "preserve-wrap"
                    else "Normalized content image spacing and responsive sizing for Canvas-safe layouts"
                ),
                count=responsive_image_updates,
            )
        )

    manual_issues: list[ManualReviewIssue] = []
    if unresolved:
        evidence = (
            unresolved_refs[0][:120]
            if unresolved_refs
            else "unresolved template references"
        )
        manual_issues.append(
            ManualReviewIssue(
                reason="Template asset reference not mapped to Canvas template package",
                evidence=evidence,
            )
        )

    file_summary = {
        "path": file_path,
        "mapped_direct": direct_mapped,
        "mapped_alias": alias_mapped,
        "unresolved": unresolved,
        "ignored_unresolved": ignored_unresolved,
        "alias_pairs_used": sorted(matched_alias_pairs),
        "unresolved_basenames": sorted({name for name in unresolved_basenames if name})[
            :50
        ],
        "ignored_basenames": sorted({name for name in ignored_basenames if name})[:50],
        "unresolved_refs_sample": unresolved_refs[:20],
    }
    return updated, applied_changes, manual_issues, file_summary


def materialize_template_assets(
    *,
    context: TemplateOverlayContext,
    destination_root: Path,
) -> dict:
    output_dir = destination_root / _MATERIALIZED_ASSET_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped_collisions = 0
    skipped_existing = 0
    copied_basenames: list[str] = []

    with ZipFile(context.template_package, "r") as zf:
        for basename, paths in sorted(context.assets_by_basename.items()):
            if len(paths) != 1:
                skipped_collisions += 1
                continue
            source_name = paths[0]
            target = output_dir / basename
            if target.exists():
                skipped_existing += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(source_name))
            copied += 1
            copied_basenames.append(basename)

    return {
        "asset_dir": _MATERIALIZED_ASSET_DIR,
        "assets_copied": copied,
        "assets_skipped_collisions": skipped_collisions,
        "assets_skipped_existing": skipped_existing,
        "copied_basenames_sample": copied_basenames[:30],
    }


def build_template_overlay_report(
    *,
    context: TemplateOverlayContext,
    file_summaries: list[dict],
    output_json_path: Path,
    materialization: dict | None = None,
) -> dict:
    totals = Counter()
    unresolved_basenames = Counter()
    ignored_basenames = Counter()
    alias_pairs_used: set[str] = set()
    unresolved_file_count = 0

    for row in file_summaries:
        if not isinstance(row, dict):
            continue
        totals["mapped_direct"] += int(row.get("mapped_direct", 0))
        totals["mapped_alias"] += int(row.get("mapped_alias", 0))
        totals["unresolved"] += int(row.get("unresolved", 0))
        totals["ignored_unresolved"] += int(row.get("ignored_unresolved", 0))
        if int(row.get("unresolved", 0)) > 0:
            unresolved_file_count += 1
        for basename in row.get("unresolved_basenames", []):
            unresolved_basenames[str(basename)] += 1
        for basename in row.get("ignored_basenames", []):
            ignored_basenames[str(basename)] += 1
        for pair in row.get("alias_pairs_used", []):
            alias_pairs_used.add(str(pair))

    payload = {
        "inputs": {
            "template_package": str(context.template_package),
            "alias_map_json": context.alias_map_source,
        },
        "summary": {
            "files_scanned": len(file_summaries),
            "files_with_unresolved_template_refs": unresolved_file_count,
            "mapped_direct": totals["mapped_direct"],
            "mapped_alias": totals["mapped_alias"],
            "mapped_total": totals["mapped_direct"] + totals["mapped_alias"],
            "unresolved_total": totals["unresolved"],
            "ignored_unresolved_total": totals["ignored_unresolved"],
            "template_asset_basenames": len(context.assets_by_basename),
            "alias_rules_loaded": len(context.alias_map),
            "alias_pairs_used": len(alias_pairs_used),
            "template_file_name_collisions": len(context.file_name_collisions),
        },
        "materialization": materialization or {},
        "top_unresolved_basenames": unresolved_basenames.most_common(30),
        "top_ignored_unresolved_basenames": ignored_basenames.most_common(20),
        "alias_pairs_used": sorted(alias_pairs_used),
        "template_file_name_collisions": context.file_name_collisions,
        "files": file_summaries,
    }

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

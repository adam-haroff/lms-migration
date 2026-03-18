from __future__ import annotations

import html
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

from .math_tools import (
    find_equation_image_tags,
    normalize_math_handling,
    strip_empty_mathml_stubs,
)
from .rules import BannerRule, LinkRewrite, ManualTrigger, RegexReplacement


@dataclass(frozen=True)
class AppliedChange:
    category: str
    description: str
    count: int


@dataclass(frozen=True)
class ManualReviewIssue:
    reason: str
    evidence: str


@dataclass(frozen=True)
class TemplateCheckPolicy:
    check_instructor_notes: bool = True
    check_template_placeholders: bool = True
    check_legacy_quiz_wording: bool = True
    require_mc_closing_bullet: bool = True


@dataclass(frozen=True)
class CanvasSanitizerPolicy:
    sanitize_brightspace_assets: bool = True
    neutralize_legacy_d2l_links: bool = True
    use_alt_text_for_removed_template_images: bool = True
    repair_missing_local_references: bool = True
    strip_bootstrap_grid_classes: bool = True
    normalize_divider_styling: bool = True
    math_handling: str = "preserve-semantic"
    accordion_handling: str = "smart"
    accordion_summary_alignment: str = "left"
    accordion_flatten_hints: tuple[str, ...] = ()
    accordion_details_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class BestPracticeEnforcerPolicy:
    enabled: bool = False
    enforce_module_checklist_closer: bool = False
    ensure_external_links_new_tab: bool = False


_BSP_TEMPLATE_RE = re.compile(r"^/?shared/brightspace_html_template/", flags=re.IGNORECASE)
_BSP_FONT_RE = re.compile(r"^https?://s\.brightspace\.com/", flags=re.IGNORECASE)
_LEGACY_D2L_RE = re.compile(r"^/?d2l/", flags=re.IGNORECASE)
_LEGACY_ENFORCED_RE = re.compile(r"^/?content/enforced/", flags=re.IGNORECASE)
_D2L_QUICKLINK_PATH_RE = re.compile(
    r"^/?d2l/common/dialogs/quicklink/quicklink\.d2l$",
    flags=re.IGNORECASE,
)
_BOOTSTRAP_GRID_CLASS_RE = re.compile(
    r"^(?:container(?:-fluid)?|row|col(?:-[a-z]+)?-\d{1,2}|offset(?:-[a-z]+)?-\d{1,2})$",
    flags=re.IGNORECASE,
)
_BOOTSTRAP_UTILITY_CLASS_RE = re.compile(
    r"^(?:"
    r"(?:m|p)(?:[trblxy])?-(?:0|1|2|3|4|5|auto)|"
    r"bg-(?:light|white)|"
    r"text-(?:left|center|right)|"
    r"float-(?:left|right)|"
    r"w-100|h-100"
    r")$",
    flags=re.IGNORECASE,
)
_LEGACY_TEMPLATE_CLASS_RE = re.compile(
    r"^(?:banner-img|courseLink|courseTable|grade|datatable|courseHeader|accordion|card|card-body|card-header|card-title|collapse)$",
    flags=re.IGNORECASE,
)
_ACCORDION_CARD_PATTERN = re.compile(
    r"<div\b[^>]*class\s*=\s*[\"'][^\"']*\bcard\b[^\"']*[\"'][^>]*>\s*"
    r"<div\b[^>]*class\s*=\s*[\"'][^\"']*\bcard-header\b[^\"']*[\"'][^>]*>\s*(?P<header>.*?)\s*</div>\s*"
    r"<div\b[^>]*class\s*=\s*[\"'][^\"']*\bcollapse\b[^\"']*[\"'][^>]*>\s*"
    r"<div\b[^>]*class\s*=\s*[\"'][^\"']*\bcard-body\b[^\"']*[\"'][^>]*>\s*(?P<body>.*?)\s*</div>\s*</div>\s*</div>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TITLE_TEXT_RE = re.compile(r"<title\b[^>]*>(?P<body>.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_FIRST_HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>(?P<body>.*?)</h[1-6]>", flags=re.IGNORECASE | re.DOTALL)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_SMART_ACCORDION_FLATTEN_HINTS = (
    "syllabus",
    "instructor guide",
    "faculty",
    "artificial intelligence in this course",
    "policy",
    "do not publish",
)
_SMART_ACCORDION_DETAILS_HINTS = (
    "lesson",
    "student resources",
    "support",
    "faq",
    "how do i",
    "help",
    "resource",
)
_EDITOR_ARTIFACT_ATTR_RE = re.compile(
    r"\s(?:"
    r"data-start|data-end|data-ccp-props|data-contrast|data-is-last-node|"
    r"data-is-only-node|data-d2l-editor-default-img-style"
    r")\s*=\s*(?:([\"']).*?\1|[^\s>]+)",
    flags=re.IGNORECASE | re.DOTALL,
)
_D2L_STYLE_TOKEN_RE = re.compile(
    r"(?:^|;)\s*--d2l-[a-z0-9-]+\s*:\s*[^;]+",
    flags=re.IGNORECASE,
)
# CSS property equivalents for Bootstrap utility classes that carry visible layout
# intent (float, alignment, background, spacing).  Applied as inline styles before
# the class tokens are stripped so their visual effect is preserved in Canvas.
_BOOTSTRAP_UTILITY_CSS_MAP: dict[str, dict[str, str]] = {
    "float-left":  {"float": "left",  "margin-right": "12px", "margin-bottom": "8px"},
    "float-right": {"float": "right", "margin-left":  "12px", "margin-bottom": "8px"},
    "text-center": {"text-align": "center"},
    "text-right":  {"text-align": "right"},
    "bg-light":    {"background-color": "#f8f9fa", "padding": "0.75rem"},
    "bg-white":    {"background-color": "#ffffff"},
    "w-100":       {"width": "100%"},
    "p-0": {"padding": "0"},
    "p-1": {"padding": "0.25rem"},
    "p-2": {"padding": "0.5rem"},
    "p-3": {"padding": "1rem"},
    "p-4": {"padding": "1.5rem"},
    "p-5": {"padding": "3rem"},
    "mt-1": {"margin-top": "0.25rem"},
    "mt-2": {"margin-top": "0.5rem"},
    "mt-3": {"margin-top": "1rem"},
    "mb-1": {"margin-bottom": "0.25rem"},
    "mb-2": {"margin-bottom": "0.5rem"},
    "mb-3": {"margin-bottom": "1rem"},
    "py-1": {"padding-top": "0.25rem",  "padding-bottom": "0.25rem"},
    "py-2": {"padding-top": "0.5rem",   "padding-bottom": "0.5rem"},
    "py-3": {"padding-top": "1rem",     "padding-bottom": "1rem"},
    "px-1": {"padding-left": "0.25rem", "padding-right": "0.25rem"},
    "px-2": {"padding-left": "0.5rem",  "padding-right": "0.5rem"},
    "px-3": {"padding-left": "1rem",    "padding-right": "1rem"},
}
# Accordion card-header text values that are generic D2L template placeholders
# rather than meaningful section titles.  In flatten mode these are silently
# suppressed so no spurious headings appear in the converted document.
_ACCORDION_PLACEHOLDER_TITLES: frozenset[str] = frozenset({
    "section", "item", "content", "note", "card", "panel", "block", "unit",
})
_DISPLAY_EQUATION_BLOCK_RE = re.compile(
    r"<(?P<tag>p|div)\b(?P<attrs>[^>]*)>\s*"
    r"(?P<body>(?:<span\b[^>]*>\s*)?(?:<img\b[^>]*>|<math\b[^>]*>.*?</math>)(?:\s*</span>)?)\s*"
    r"</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_DISPLAY_MATHML_RE = re.compile(
    r"<math\b[^>]*(?:\bdisplay\s*=\s*([\"'])block\1|\bmode\s*=\s*([\"'])display\2)[^>]*>.*?</math>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _plain_text(value: str) -> str:
    cleaned = _STRIP_TAGS_RE.sub(" ", value)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_attr_value(tag_html: str, attr_name: str) -> str | None:
    match = re.search(
        rf"\b{attr_name}\s*=\s*([\"'])(?P<value>.*?)\1",
        tag_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    return html.unescape(match.group("value"))


def _set_or_add_attr(tag_html: str, attr_name: str, value: str) -> str:
    pattern = re.compile(
        rf"(\b{attr_name}\s*=\s*)([\"'])(?P<value>.*?)(\2)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if pattern.search(tag_html):
        return pattern.sub(lambda m: f'{m.group(1)}"{html.escape(value, quote=True)}"', tag_html, count=1)
    if tag_html.endswith("/>"):
        return f'{tag_html[:-2].rstrip()} {attr_name}="{html.escape(value, quote=True)}" />'
    return f'{tag_html[:-1].rstrip()} {attr_name}="{html.escape(value, quote=True)}">'


def _remove_attr(tag_html: str, attr_name: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"\s{attr_name}\s*=\s*(?:([\"']).*?\1|[^\s>]+)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    updated, removed = pattern.subn("", tag_html, count=1)
    return updated, bool(removed)


def _merge_inline_style(tag_html: str, additions: dict[str, str]) -> tuple[str, bool]:
    style_pattern = re.compile(
        r'(\bstyle\s*=\s*)(["\'])(?P<value>.*?)(\2)',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = style_pattern.search(tag_html)
    style_text = match.group("value") if match is not None else ""
    declarations: list[str] = []
    index_by_key: dict[str, int] = {}
    for chunk in style_text.split(";"):
        piece = chunk.strip()
        if not piece or ":" not in piece:
            continue
        key, raw_value = piece.split(":", 1)
        lowered = key.strip().lower()
        index_by_key[lowered] = len(declarations)
        declarations.append(f"{lowered}: {raw_value.strip()}")

    changed = False
    for key, value in additions.items():
        lowered = key.strip().lower()
        desired = f"{lowered}: {value.strip()}"
        if lowered in index_by_key:
            index = index_by_key[lowered]
            if declarations[index] != desired:
                declarations[index] = desired
                changed = True
            continue
        index_by_key[lowered] = len(declarations)
        declarations.append(desired)
        changed = True

    if not declarations:
        return tag_html, False
    merged_style = "; ".join(declarations).strip() + ";"
    if match is None:
        return _set_or_add_attr(tag_html, "style", merged_style), True
    rebuilt = (
        tag_html[: match.start("value")]
        + merged_style
        + tag_html[match.end("value") :]
    )
    return rebuilt, changed


def _remove_inline_style_keys(tag_html: str, keys: set[str]) -> tuple[str, bool]:
    style_pattern = re.compile(
        r'(\bstyle\s*=\s*)(["\'])(?P<value>.*?)(\2)',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = style_pattern.search(tag_html)
    if match is None:
        return tag_html, False

    kept: list[str] = []
    changed = False
    for chunk in match.group("value").split(";"):
        piece = chunk.strip()
        if not piece or ":" not in piece:
            continue
        key, raw_value = piece.split(":", 1)
        lowered = key.strip().lower()
        if lowered in keys:
            changed = True
            continue
        kept.append(f"{lowered}: {raw_value.strip()}")

    if not changed:
        return tag_html, False

    if not kept:
        rebuilt = tag_html[: match.start()] + tag_html[match.end() :]
        rebuilt = re.sub(r"\s{2,}", " ", rebuilt)
        rebuilt = rebuilt.replace("< ", "<")
        return rebuilt, True

    merged_style = "; ".join(kept).strip() + ";"
    rebuilt = (
        tag_html[: match.start("value")]
        + merged_style
        + tag_html[match.end("value") :]
    )
    return rebuilt, True


def _merge_class_names(tag_html: str, class_names: Iterable[str]) -> tuple[str, bool]:
    class_pattern = re.compile(
        r'(\bclass\s*=\s*)(["\'])(?P<value>.*?)(\2)',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = class_pattern.search(tag_html)
    desired = [token.strip() for token in class_names if token.strip()]
    if not desired:
        return tag_html, False

    if match is None:
        return _set_or_add_attr(tag_html, "class", " ".join(desired)), True

    existing_tokens = [token for token in match.group("value").split() if token]
    merged_tokens = list(existing_tokens)
    changed = False
    for token in desired:
        if token not in merged_tokens:
            merged_tokens.append(token)
            changed = True
    if not changed:
        return tag_html, False

    rebuilt = (
        tag_html[: match.start("value")]
        + " ".join(merged_tokens)
        + tag_html[match.end("value") :]
    )
    return rebuilt, True


def _normalize_equation_image_styles(content: str) -> tuple[str, int]:
    styled = 0

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal styled
        tag_html = match.group(0)
        updated_tag, changed = _merge_inline_style(
            tag_html,
            {
                "max-width": "100%",
                "height": "auto",
                "vertical-align": "middle",
            },
        )
        if changed:
            styled += 1
        return updated_tag

    updated = re.sub(
        r"<img\b[^>]*>",
        lambda match: replace_tag(match) if "equation_image" in match.group(0).lower() else match.group(0),
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return updated, styled


def _is_syllabus_like_page(*, file_path: str, document_title_text: str, content: str) -> bool:
    context = " ".join(
        filter(
            None,
            (
                file_path.replace("\\", "/").lower(),
                document_title_text.lower(),
                _extract_document_heading_text(content).lower(),
            ),
        )
    )
    if "syllabus quiz" in context:
        return False
    return "syllabus" in context or "class meeting schedule" in context


def _top_level_table_spans(fragment: str) -> list[tuple[int, int]]:
    token_pattern = re.compile(r"</?table\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
    starts: list[int] = []
    spans: list[tuple[int, int]] = []
    for match in token_pattern.finditer(fragment):
        tag_html = match.group(0).lower()
        if tag_html.startswith("</table"):
            if not starts:
                continue
            start = starts.pop()
            if not starts:
                spans.append((start, match.end()))
            continue
        starts.append(match.start())
    return spans


def _normalize_syllabus_tables(fragment: str) -> tuple[str, int]:
    spans = _top_level_table_spans(fragment)
    if not spans:
        return fragment, 0

    pieces: list[str] = []
    last_index = 0
    total_updates = 0
    for start, end in spans:
        pieces.append(fragment[last_index:start])
        normalized_table, table_updates, section_heading = _normalize_single_syllabus_table(fragment[start:end])
        if section_heading and pieces:
            pieces[-1] = re.sub(
                r"(?is)<h3>\s*Section\s*</h3>\s*<div>\s*$",
                "<div>",
                pieces[-1],
                count=1,
            )
        pieces.append(normalized_table)
        total_updates += table_updates
        last_index = end
    pieces.append(fragment[last_index:])
    return "".join(pieces), total_updates


def _flatten_nested_table_markup(value_html: str) -> str:
    if "<table" not in value_html.lower():
        return value_html.strip()
    updated = value_html
    updated = re.sub(r"</(?:td|th)>\s*<(?:td|th)\b[^>]*>", "<br>", updated, flags=re.IGNORECASE | re.DOTALL)
    updated = re.sub(r"</tr>\s*<tr\b[^>]*>", "<br>", updated, flags=re.IGNORECASE | re.DOTALL)
    updated = re.sub(r"</?(?:table|tbody|thead|tfoot|tr|td|th)\b[^>]*>", "", updated, flags=re.IGNORECASE | re.DOTALL)
    updated = re.sub(r"(?:<br\s*/?>\s*){3,}", "<br><br>", updated, flags=re.IGNORECASE)
    return updated.strip()


def _convert_syllabus_row_header_table_to_list(table_html: str, section_heading: str) -> str | None:
    table_match = re.match(r"<table\b[^>]*>(?P<body>.*?)</table>\s*$", table_html, flags=re.IGNORECASE | re.DOTALL)
    if table_match is None:
        return None

    table_body = re.sub(r"<caption\b[^>]*>.*?</caption>", "", table_match.group("body"), flags=re.IGNORECASE | re.DOTALL)
    items: list[str] = []
    for row_match in re.finditer(r"<tr\b[^>]*>(?P<body>.*?)</tr>", table_body, flags=re.IGNORECASE | re.DOTALL):
        row_body = row_match.group("body")
        header_match = re.search(r"<th\b[^>]*>(?P<body>.*?)</th>", row_body, flags=re.IGNORECASE | re.DOTALL)
        value_match = re.search(r"<td\b[^>]*>(?P<body>.*?)</td>", row_body, flags=re.IGNORECASE | re.DOTALL)
        if header_match is None or value_match is None:
            continue

        label_text = _plain_text(header_match.group("body")).rstrip(":").strip()
        value_html = _flatten_nested_table_markup(value_match.group("body"))
        value_text = _plain_text(value_html)
        if not label_text and not value_text:
            continue
        if not label_text:
            items.append(f"<li>{value_html}</li>")
            continue
        if not value_text:
            items.append(f"<li><strong>{html.escape(label_text, quote=False)}</strong></li>")
            continue
        if re.search(r"<(?:div|p|ul|ol|h[1-6]|details|blockquote)\b", value_html, flags=re.IGNORECASE):
            items.append(
                f"<li><strong>{html.escape(label_text, quote=False)}</strong><div>{value_html}</div></li>"
            )
        else:
            items.append(f"<li><strong>{html.escape(label_text, quote=False)}</strong>: {value_html}</li>")

    if len(items) < 3:
        return None
    return f"<h3>{html.escape(section_heading, quote=False)}</h3>\n<ul>\n" + "\n".join(items) + "\n</ul>"


def _normalize_single_syllabus_table(table_html: str) -> tuple[str, int, str]:
    opening_match = re.match(r"<table\b[^>]*>", table_html, flags=re.IGNORECASE | re.DOTALL)
    closing_match = re.search(r"</table>\s*$", table_html, flags=re.IGNORECASE | re.DOTALL)
    if opening_match is None or closing_match is None:
        return table_html, 0, ""

    opening_tag = opening_match.group(0)
    inner_html = table_html[opening_match.end() : closing_match.start()]
    normalized_inner, nested_updates = _normalize_syllabus_tables(inner_html)

    text_snapshot = _plain_text(normalized_inner).lower()
    summary_text = (_extract_attr_value(opening_tag, "summary") or "").strip()
    existing_caption_match = re.search(
        r"<caption\b[^>]*>(?P<body>.*?)</caption>",
        normalized_inner,
        flags=re.IGNORECASE | re.DOTALL,
    )
    existing_caption_text = (
        _plain_text(existing_caption_match.group("body")) if existing_caption_match is not None else ""
    )

    role = ""
    if (
        "topics" in text_snapshot
        and "assignments" in text_snapshot
        and "due date" in text_snapshot
    ) or "16-week" in text_snapshot or "12-week" in text_snapshot:
        role = "course-outline"
    elif "date" in text_snapshot and "time" in text_snapshot and "location or zoom link" in text_snapshot:
        role = "class-meeting-schedule"
    elif "percent of total grade" in text_snapshot or summary_text.lower() == "grading policy":
        role = "assignment-weights"
    elif "grading scale" in text_snapshot or summary_text.lower() == "grading scale":
        role = "grading-scale"
    elif "course title:" in text_snapshot and "credit hours:" in text_snapshot:
        role = "course-materials"
    elif "instructor:" in text_snapshot and "sinclair email:" in text_snapshot:
        role = "instructor-information"
    elif "office hours" in text_snapshot:
        role = "office-hours"

    caption_text = existing_caption_text
    if not caption_text:
        normalized_summary = summary_text.lower()
        if role == "course-outline":
            if "16-week" in text_snapshot:
                caption_text = "Course Outline (16-Week)"
            elif "12-week" in text_snapshot:
                caption_text = "Course Outline (12-Week)"
            else:
                caption_text = "Course Outline"
        elif role == "class-meeting-schedule":
            caption_text = "Class Meeting Schedule"
        elif role == "assignment-weights":
            caption_text = "Assignment Weights"
        elif role == "grading-scale":
            caption_text = "Grading Scale"
        elif role == "course-materials":
            caption_text = "Course Materials and Outcomes"
        elif role == "instructor-information":
            caption_text = "Instructor Information"
        elif role == "office-hours":
            caption_text = "Office Hours"
        elif normalized_summary:
            caption_text = summary_text

    updated_opening_tag = opening_tag
    changed = False
    updated_opening_tag, removed_summary = _remove_attr(updated_opening_tag, "summary")
    changed = changed or removed_summary
    updated_opening_tag, removed_table_height = _remove_inline_style_keys(updated_opening_tag, {"height"})
    changed = changed or removed_table_height

    table_styles = {"border-collapse": "collapse"}
    class_text = (_extract_attr_value(updated_opening_tag, "class") or "").lower()
    if role in {"course-outline", "class-meeting-schedule"} or "coursetable" in class_text:
        table_styles["width"] = "100%"
    updated_opening_tag, merged_table_style = _merge_inline_style(updated_opening_tag, table_styles)
    changed = changed or merged_table_style

    if role in {"course-outline", "class-meeting-schedule"}:
        updated_opening_tag, merged_classes = _merge_class_names(
            updated_opening_tag,
            ("ic-Table", "ic-Table--hover-row", "ic-Table--striped"),
        )
        changed = changed or merged_classes
        if _extract_attr_value(updated_opening_tag, "border") != "1":
            updated_opening_tag = _set_or_add_attr(updated_opening_tag, "border", "1")
            changed = True

    normalized_inner_before_caption = normalized_inner
    if caption_text:
        caption_html = (
            f"<caption><h3>{html.escape(caption_text, quote=False)}</h3></caption>"
            if role in {"course-outline", "class-meeting-schedule"}
            else f"<caption>{html.escape(caption_text, quote=False)}</caption>"
        )
        if existing_caption_match is not None:
            normalized_inner = (
                normalized_inner[: existing_caption_match.start()]
                + caption_html
                + normalized_inner[existing_caption_match.end() :]
            )
        else:
            normalized_inner = caption_html + normalized_inner
        changed = changed or normalized_inner != normalized_inner_before_caption

    if role in {"course-materials", "instructor-information"} and caption_text:
        list_markup = _convert_syllabus_row_header_table_to_list(
            updated_opening_tag + normalized_inner + "</table>",
            caption_text,
        )
        if list_markup is not None:
            return list_markup, nested_updates + 1, caption_text

    row_tag_pattern = re.compile(r"<tr\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
    normalized_rows = 0

    def normalize_row_tag(match: re.Match[str]) -> str:
        nonlocal normalized_rows
        tag_html = match.group(0)
        updated_tag, removed = _remove_inline_style_keys(tag_html, {"height"})
        if removed:
            normalized_rows += 1
        return updated_tag

    normalized_inner = row_tag_pattern.sub(normalize_row_tag, normalized_inner)

    cell_tag_pattern = re.compile(r"<(?:th|td)\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
    normalized_cells = 0

    def normalize_cell_tag(match: re.Match[str]) -> str:
        nonlocal normalized_cells
        tag_html = match.group(0)
        updated_tag = tag_html
        changed_local = False

        updated_tag, removed_height = _remove_inline_style_keys(updated_tag, {"height"})
        changed_local = changed_local or removed_height

        scope_value = (_extract_attr_value(updated_tag, "scope") or "").strip().lower()
        if scope_value == "column":
            updated_tag = _set_or_add_attr(updated_tag, "scope", "col")
            changed_local = True
            scope_value = "col"

        if scope_value == "col":
            updated_tag, merged_header_style = _merge_inline_style(
                updated_tag,
                {
                    "background-color": "#000000",
                    "color": "#ffffff",
                },
            )
            changed_local = changed_local or merged_header_style

        if changed_local:
            normalized_cells += 1
        return updated_tag

    normalized_inner = cell_tag_pattern.sub(normalize_cell_tag, normalized_inner)

    normalized_table = updated_opening_tag + normalized_inner + "</table>"
    return normalized_table, nested_updates + (1 if normalized_table != table_html else 0), ""


def _wrap_display_equations(content: str) -> tuple[str, int]:
    wrapped = 0

    def replace_equation_block(match: re.Match[str]) -> str:
        nonlocal wrapped
        attrs = (match.group("attrs") or "").lower()
        body = match.group("body") or ""
        if "migration-display-equation" in attrs:
            return match.group(0)
        has_equation_image = bool(find_equation_image_tags(body))
        has_display_mathml = bool(_DISPLAY_MATHML_RE.search(body))
        if not has_equation_image and not has_display_mathml:
            return match.group(0)
        alignment = "center" if "text-align: center" in attrs else "left"
        wrapped += 1
        return (
            '<div class="migration-display-equation" '
            'style="margin: 16px 0; overflow-x: auto; overflow-y: hidden;">'
            f'<div class="migration-display-equation__inner" style="text-align: {alignment};">{body}</div>'
            "</div>"
        )

    updated = _DISPLAY_EQUATION_BLOCK_RE.sub(replace_equation_block, content)
    return updated, wrapped


def _extract_document_heading_text(content: str) -> str:
    for pattern in (_TITLE_TEXT_RE, _FIRST_HEADING_RE):
        match = pattern.search(content)
        if match is None:
            continue
        text = _plain_text(match.group("body"))
        if text:
            return text
    return ""


def _extract_accordion_title_text(header_html: str) -> str:
    heading_match = re.search(
        r"<h[1-6]\b[^>]*>(?P<title>.*?)</h[1-6]>",
        header_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    title_source = heading_match.group("title") if heading_match is not None else header_html
    text = _plain_text(title_source)
    if text:
        return text

    for attr_name in ("alt", "title"):
        attr_match = re.search(
            rf"\b{attr_name}\s*=\s*([\"'])(?P<value>.*?)\1",
            header_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if attr_match is None:
            continue
        attr_text = _plain_text(attr_match.group("value"))
        if attr_text:
            return attr_text
    return "Section"


def _resolve_accordion_mode(*, requested_mode: str, file_path: str = "", content: str = "") -> str:
    return _resolve_accordion_mode_with_policy(
        requested_mode=requested_mode,
        file_path=file_path,
        content=content,
    )


def _normalize_accordion_hints(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in normalized:
            continue
        normalized.append(text)
    return tuple(normalized)


def _resolve_accordion_mode_with_policy(
    *,
    requested_mode: str,
    file_path: str = "",
    content: str = "",
    flatten_hints: Iterable[str] | None = None,
    details_hints: Iterable[str] | None = None,
) -> str:
    normalized_mode = str(requested_mode).strip().lower()
    if normalized_mode in {"details", "flatten"}:
        return normalized_mode
    if normalized_mode != "smart":
        return ""

    normalized_path = file_path.replace("\\", "/").lower()
    document_heading = _extract_document_heading_text(content).lower()
    context = " ".join(filter(None, (normalized_path, document_heading)))
    normalized_flatten_hints = _normalize_accordion_hints(flatten_hints)
    normalized_details_hints = _normalize_accordion_hints(details_hints)

    if any(hint in context for hint in normalized_flatten_hints):
        return "flatten"
    if any(hint in context for hint in normalized_details_hints):
        return "details"
    if any(hint in context for hint in _SMART_ACCORDION_FLATTEN_HINTS):
        return "flatten"
    if any(hint in context for hint in _SMART_ACCORDION_DETAILS_HINTS):
        return "details"
    if re.search(r"(?:^|/)(topic|module)\s*\d+", normalized_path):
        return "details"
    return "details"


def _accordion_summary_alignment(value: str) -> str:
    return "center" if str(value or "").strip().lower() == "center" else "left"


def _accordion_details_style() -> str:
    return (
        "margin: 16px 0; border: 1px solid #c8d1db; border-radius: 6px; "
        "background-color: #ffffff; overflow: hidden;"
    )


def _accordion_summary_style(alignment: str) -> str:
    return (
        "cursor: pointer; display: block; padding: 12px 16px; "
        "font-weight: 700; line-height: 1.4; "
        f"text-align: {_accordion_summary_alignment(alignment)}; "
        "background-color: #f4f6f8;"
    )


def _accordion_panel_style() -> str:
    return "padding: 12px 16px; text-align: left;"


def _convert_bootstrap_accordion_cards(content: str, mode: str, *, alignment: str = "left") -> tuple[str, int]:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"details", "flatten"}:
        return content, 0

    converted = 0

    def replace_card(match: re.Match[str]) -> str:
        nonlocal converted
        header_html = match.group("header").strip()
        body_html = match.group("body").strip()
        title_text = _extract_accordion_title_text(header_html)
        title_html = html.escape(title_text, quote=False)

        converted += 1
        if normalized_mode == "details":
            summary_label = title_html or "Section"
            return (
                f'<details class="migration-accordion" style="{_accordion_details_style()}">\n'
                f'  <summary style="{_accordion_summary_style(alignment)}">{summary_label}</summary>\n'
                f'  <div class="migration-accordion__panel" style="{_accordion_panel_style()}">{body_html}</div>\n'
                f"</details>"
            )

        # Flatten mode: suppress the heading when the title is empty or is a
        # generic D2L template placeholder such as "Section" so that no spurious
        # headings appear in the converted document.
        normalized_title = (title_text or "").lower().strip()
        if title_html and normalized_title not in _ACCORDION_PLACEHOLDER_TITLES:
            return f"<h3>{title_html}</h3>\n<div>{body_html}</div>"
        return f"<div>{body_html}</div>"

    updated = _ACCORDION_CARD_PATTERN.sub(replace_card, content)
    return updated, converted


def _re_flags(flag_string: str) -> int:
    flags = 0
    lowered = flag_string.lower()
    if "i" in lowered:
        flags |= re.IGNORECASE
    if "m" in lowered:
        flags |= re.MULTILINE
    if "s" in lowered:
        flags |= re.DOTALL
    return flags


def apply_replacements(content: str, replacements: Iterable[RegexReplacement]) -> tuple[str, list[AppliedChange]]:
    updated = content
    applied: list[AppliedChange] = []

    for replacement in replacements:
        pattern = re.compile(replacement.pattern, flags=_re_flags(replacement.flags))
        updated, count = pattern.subn(replacement.replacement, updated)
        if count:
            applied.append(
                AppliedChange(category="replacement", description=replacement.description, count=count)
            )

    return updated, applied


def apply_link_rewrites(content: str, rewrites: Iterable[LinkRewrite]) -> tuple[str, list[AppliedChange]]:
    updated = content
    applied: list[AppliedChange] = []

    for rewrite in rewrites:
        count = updated.count(rewrite.source)
        if count:
            updated = updated.replace(rewrite.source, rewrite.target)
            applied.append(
                AppliedChange(category="link_rewrite", description=rewrite.description, count=count)
            )

    return updated, applied


def apply_banner_rule(content: str, banner_rule: BannerRule) -> tuple[str, list[AppliedChange]]:
    if not banner_rule.enabled or not banner_rule.html.strip():
        return content, []

    updated = content
    lower_content = content.lower()
    if banner_rule.html.strip().lower() in lower_content:
        return content, []

    if banner_rule.insert_mode == "prepend_body":
        body_pattern = re.compile(r"<body[^>]*>", flags=re.IGNORECASE)
        body_match = body_pattern.search(content)
        if body_match:
            updated = (
                content[: body_match.end()]
                + "\n"
                + banner_rule.html
                + "\n"
                + content[body_match.end() :]
            )
        else:
            updated = banner_rule.html + "\n" + content
    else:
        updated = banner_rule.html + "\n" + content

    return updated, [AppliedChange(category="template", description="Injected course template banner", count=1)]


def _is_brightspace_template_ref(url: str) -> bool:
    cleaned = url.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned:
        return False
    return bool(_BSP_TEMPLATE_RE.match(cleaned) or _BSP_FONT_RE.match(cleaned))


def _is_legacy_d2l_link(url: str) -> bool:
    cleaned = url.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned:
        return False
    return bool(_LEGACY_D2L_RE.match(cleaned) or _LEGACY_ENFORCED_RE.match(cleaned))


def _html_unescape_repeated(value: str, *, max_rounds: int = 4) -> str:
    current = value
    for _ in range(max_rounds):
        unescaped = html.unescape(current)
        if unescaped == current:
            break
        current = unescaped
    return current


def _rewrite_quicklink_coursefile_href(raw_href: str) -> str | None:
    decoded_href = _html_unescape_repeated(raw_href.strip())
    parsed = urlparse(decoded_href)
    path_text = (parsed.path or "").strip()
    if parsed.params:
        path_text = f"{path_text};{parsed.params}"
    if not _D2L_QUICKLINK_PATH_RE.match(path_text):
        return None

    params = parse_qsl(parsed.query, keep_blank_values=True)
    if not params:
        return None

    link_type = ""
    file_id = ""
    for key, value in params:
        lowered = key.strip().lower()
        if lowered == "type" and not link_type:
            link_type = value.strip().lower()
        elif lowered == "fileid" and not file_id:
            file_id = value.strip()

    if link_type != "coursefile" or not file_id:
        return None

    normalized = unquote(file_id).strip().replace("\\", "/")
    if not normalized:
        return None
    normalized = posixpath.normpath(normalized).lstrip("./")
    if not normalized or normalized.startswith("../"):
        return None

    if parsed.fragment:
        return f"{normalized}#{parsed.fragment}"
    return normalized


def neutralize_legacy_d2l_hrefs_in_markup(content: str) -> tuple[str, int, int]:
    """
    Neutralize legacy D2L href values in raw/escaped markup payloads.

    This is used for D2L XML package files (for example ``news_d2l.xml``) where
    learner-facing HTML is often entity-escaped, so normal HTML parsing passes
    do not see those anchors.
    """
    href_pattern = re.compile(
        r'(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"\'])(?P<href>[^\"\']+)(?P=quote)',
        flags=re.IGNORECASE,
    )
    neutralized = 0
    rewritten_quicklinks = 0

    def replace_href(match: re.Match[str]) -> str:
        nonlocal neutralized
        nonlocal rewritten_quicklinks
        raw_href = match.group("href").strip()
        rewritten = _rewrite_quicklink_coursefile_href(raw_href)
        if rewritten:
            rewritten_quicklinks += 1
            return (
                f'{match.group("prefix")}{match.group("quote")}'
                f'{html.escape(rewritten, quote=True)}'
                f'{match.group("quote")}'
            )
        if not _is_legacy_d2l_link(html.unescape(raw_href)):
            return match.group(0)

        neutralized += 1
        escaped_href = html.escape(raw_href, quote=True)
        return (
            f'{match.group("prefix")}{match.group("quote")}#{match.group("quote")} '
            f'data-migration-link-status="needs-review" '
            f'data-migration-original-href="{escaped_href}"'
        )

    updated = href_pattern.sub(replace_href, content)
    return updated, rewritten_quicklinks, neutralized


def apply_canvas_sanitizer(
    content: str,
    policy: CanvasSanitizerPolicy | None = None,
    *,
    file_path: str = "",
) -> tuple[str, list[AppliedChange]]:
    """
    Apply local HTML cleanup to reduce Canvas import warnings from legacy D2L/Brightspace refs.
    """
    applied_policy = policy or CanvasSanitizerPolicy()
    updated = content
    applied: list[AppliedChange] = []
    title_tag_pattern = re.compile(r"<title\b[^>]*>(?P<title>.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
    document_title_text = ""
    title_match_initial = title_tag_pattern.search(updated)
    if title_match_initial is not None:
        document_title_text = html.unescape(re.sub(r"<[^>]+>", " ", title_match_initial.group("title"))).strip()
        updated, removed_title_tags = title_tag_pattern.subn("", updated)
        if removed_title_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed HTML <title> tags to prevent duplicate in-body headings in Canvas",
                    count=removed_title_tags,
                )
            )
    decorative_alt_values = {
        "banner",
        "logo",
        "image",
        "decorative",
        "horizontal line",
        "horizontal rule",
        "rule",
        "divider",
        "line",
    }

    math_handling = normalize_math_handling(applied_policy.math_handling)
    if math_handling != "audit-only":
        updated, removed_empty_mathml = strip_empty_mathml_stubs(updated)
        if removed_empty_mathml:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed empty MathML stubs left by legacy equation markup",
                    count=removed_empty_mathml,
                )
            )

        updated, styled_equation_images = _normalize_equation_image_styles(updated)
        if styled_equation_images:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Normalized Canvas equation images for responsive sizing",
                    count=styled_equation_images,
                )
            )

        updated, wrapped_display_equations = _wrap_display_equations(updated)
        if wrapped_display_equations:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Wrapped standalone display equations in responsive overflow containers",
                    count=wrapped_display_equations,
                )
            )

        if math_handling == "canvas-equation-compatible":
            wiris_annotation_pattern = re.compile(
                r"<annotation\b[^>]*\bencoding\s*=\s*([\"'])wiris\1[^>]*>.*?</annotation>",
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated, removed_wiris_annotations = wiris_annotation_pattern.subn("", updated)
            if removed_wiris_annotations:
                applied.append(
                    AppliedChange(
                        category="sanitizer",
                        description="Removed WIRIS annotation payloads for Canvas-equation compatibility mode",
                        count=removed_wiris_annotations,
                    )
                )

    requested_accordion_mode = str(applied_policy.accordion_handling or "").strip().lower()
    accordion_mode = _resolve_accordion_mode_with_policy(
        requested_mode=requested_accordion_mode,
        file_path=file_path,
        content=updated,
        flatten_hints=applied_policy.accordion_flatten_hints,
        details_hints=applied_policy.accordion_details_hints,
    )
    updated, converted_accordion_cards = _convert_bootstrap_accordion_cards(
        updated,
        accordion_mode,
        alignment=applied_policy.accordion_summary_alignment,
    )
    if converted_accordion_cards:
        description = (
            "Converted Bootstrap accordion cards to accessible <details>/<summary> blocks"
            if accordion_mode == "details"
            else "Flattened Bootstrap accordion cards into plain heading/content sections"
        )
        if requested_accordion_mode == "smart":
            description += " using page-aware smart accordion handling"
        applied.append(
            AppliedChange(
                category="sanitizer",
                description=description,
                count=converted_accordion_cards,
            )
        )

    if applied_policy.sanitize_brightspace_assets:
        if applied_policy.strip_bootstrap_grid_classes:
            # Pass 1 — before removing Bootstrap utility class tokens, promote
            # those that carry visible layout intent to inline CSS so the visual
            # effect (float, text-align, background, padding) is preserved.
            _full_tag_pat = re.compile(r"<[a-z][a-z0-9]*\b[^>]*>", flags=re.IGNORECASE)
            promoted_utility_css = 0

            def _promote_utility_cls(m: re.Match[str]) -> str:
                nonlocal promoted_utility_css
                tag = m.group(0)
                class_text = _extract_attr_value(tag, "class") or ""
                if not class_text:
                    return tag
                css_props: dict[str, str] = {}
                for token in class_text.split():
                    props = _BOOTSTRAP_UTILITY_CSS_MAP.get(token)
                    if props:
                        css_props.update(props)
                if not css_props:
                    return tag
                updated_tag, changed = _merge_inline_style(tag, css_props)
                if changed:
                    promoted_utility_css += 1
                return updated_tag

            updated = _full_tag_pat.sub(_promote_utility_cls, updated)
            if promoted_utility_css:
                applied.append(
                    AppliedChange(
                        category="sanitizer",
                        description="Promoted Bootstrap utility classes to inline CSS to preserve visual layout in Canvas",
                        count=promoted_utility_css,
                    )
                )
            # Pass 2 — strip Bootstrap grid / utility / legacy template class tokens.
            class_attr_pattern = re.compile(
                r'(?P<prefix>\sclass\s*=\s*)(?P<quote>["\'])(?P<classes>[^"\']*)(?P=quote)',
                flags=re.IGNORECASE,
            )
            stripped_grid_tokens = 0

            def replace_class_attr(match: re.Match[str]) -> str:
                nonlocal stripped_grid_tokens
                classes_text = match.group("classes").strip()
                if not classes_text:
                    return ""
                original_tokens = [token for token in classes_text.split() if token]
                kept_tokens = [
                    token
                    for token in original_tokens
                    if not _BOOTSTRAP_GRID_CLASS_RE.match(token)
                    and not _BOOTSTRAP_UTILITY_CLASS_RE.match(token)
                    and not _LEGACY_TEMPLATE_CLASS_RE.match(token)
                ]
                removed = len(original_tokens) - len(kept_tokens)
                if removed <= 0:
                    return match.group(0)
                stripped_grid_tokens += removed
                if not kept_tokens:
                    return ""
                return f'{match.group("prefix")}"{" ".join(kept_tokens)}"'

            updated = class_attr_pattern.sub(replace_class_attr, updated)
            if stripped_grid_tokens:
                applied.append(
                    AppliedChange(
                        category="sanitizer",
                        description="Removed Brightspace and Bootstrap layout classes that do not carry Canvas-safe behavior",
                        count=stripped_grid_tokens,
                    )
                )

        link_pattern = re.compile(
            r"<link\b[^>]*\bhref\s*=\s*([\"'])(?P<href>[^\"']+)\1[^>]*>",
            flags=re.IGNORECASE,
        )
        removed_link_tags = 0

        def replace_link_tag(match: re.Match[str]) -> str:
            nonlocal removed_link_tags
            href = match.group("href").strip()
            if _is_brightspace_template_ref(href):
                removed_link_tags += 1
                return ""
            return match.group(0)

        updated = link_pattern.sub(replace_link_tag, updated)
        if removed_link_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template stylesheet references",
                    count=removed_link_tags,
                )
            )

        script_pattern = re.compile(
            r"<script\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>\s*</script>",
            flags=re.IGNORECASE | re.DOTALL,
        )
        removed_script_tags = 0

        def replace_script_tag(match: re.Match[str]) -> str:
            nonlocal removed_script_tags
            src = match.group("src").strip()
            if _is_brightspace_template_ref(src):
                removed_script_tags += 1
                return ""
            return match.group(0)

        updated = script_pattern.sub(replace_script_tag, updated)
        if removed_script_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template script references",
                    count=removed_script_tags,
                )
            )

        img_pattern = re.compile(
            r"<img\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>",
            flags=re.IGNORECASE,
        )
        removed_img_tags = 0
        replaced_img_with_alt = 0

        def replace_img_tag(match: re.Match[str]) -> str:
            nonlocal removed_img_tags
            nonlocal replaced_img_with_alt
            src = match.group("src").strip()
            if not _is_brightspace_template_ref(src):
                return match.group(0)

            removed_img_tags += 1
            tag = match.group(0)
            if not applied_policy.use_alt_text_for_removed_template_images:
                return ""

            alt_match = re.search(
                r"\balt\s*=\s*([\"'])(?P<alt>.*?)\1",
                tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
            alt_text = alt_match.group("alt").strip() if alt_match else ""
            if not alt_text:
                return ""
            if alt_text.lower() in decorative_alt_values:
                return ""

            replaced_img_with_alt += 1
            escaped_alt = html.escape(alt_text, quote=False)
            return f'<span class="migration-template-image-text">{escaped_alt}</span>'

        updated = img_pattern.sub(replace_img_tag, updated)
        if removed_img_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template image references",
                    count=removed_img_tags,
                )
            )
        if replaced_img_with_alt:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Replaced removed template images with existing alt text",
                    count=replaced_img_with_alt,
                )
            )

        decorative_template_assets_pattern = re.compile(
            r"<img\b[^>]*\bsrc\s*=\s*([\"'])[^\"']*templateassets/(?P<name>footer\.png|course-card\.png)(?:[?#][^\"']*)?\1[^>]*>",
            flags=re.IGNORECASE,
        )
        removed_decorative_template_assets = 0

        def replace_decorative_template_asset(match: re.Match[str]) -> str:
            nonlocal removed_decorative_template_assets
            removed_decorative_template_assets += 1
            return ""

        updated = decorative_template_assets_pattern.sub(replace_decorative_template_asset, updated)
        if removed_decorative_template_assets:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed decorative template footer/logo images that do not render correctly in Canvas",
                    count=removed_decorative_template_assets,
                )
            )

        # Brightspace template anchors often point to /shared/Brightspace_HTML_Template/*
        # and trigger Canvas missing-link warnings during import.
        anchor_template_pattern = re.compile(
            r'(<a\b[^>]*\bhref\s*=\s*)([\"\'])(?P<href>[^\"\']+)\2',
            flags=re.IGNORECASE,
        )
        neutralized_template_links = 0

        def replace_template_anchor_href(match: re.Match[str]) -> str:
            nonlocal neutralized_template_links
            href = match.group("href").strip()
            if not _is_brightspace_template_ref(href):
                return match.group(0)

            neutralized_template_links += 1
            escaped_href = html.escape(href, quote=True)
            prefix = match.group(1)
            return (
                f'{prefix}"#" '
                f'data-migration-link-status="needs-review" '
                f'data-migration-link-reason="brightspace-template-link" '
                f'data-migration-original-href="{escaped_href}"'
            )

        updated = anchor_template_pattern.sub(replace_template_anchor_href, updated)
        if neutralized_template_links:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Neutralized Brightspace template anchor links",
                    count=neutralized_template_links,
                )
            )

    h1_pattern = re.compile(r"<h1(?P<attrs>\b[^>]*)>(?P<body>.*?)</h1>", flags=re.IGNORECASE | re.DOTALL)
    h1_demoted = 0

    def demote_h1(match: re.Match[str]) -> str:
        nonlocal h1_demoted
        h1_demoted += 1
        attrs = match.group("attrs") or ""
        body = match.group("body")
        return f"<h2{attrs}>{body}</h2>"

    updated = h1_pattern.sub(demote_h1, updated)
    if h1_demoted:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Demoted in-body H1 headings to H2 to align with Canvas page-title hierarchy",
                count=h1_demoted,
            )
        )

    print_anchor_pattern = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*([\"'])javascript:[^\"']*(?:window\.print|printprintables|print)\([^\"']*\)[^\"']*\1[^>]*>.*?</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    updated, removed_print_links = print_anchor_pattern.subn("", updated)
    if removed_print_links:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed legacy printer-friendly links that should not appear on Canvas pages",
                count=removed_print_links,
            )
        )

    javascript_anchor_pattern = re.compile(
        r'(<a\b[^>]*\bhref\s*=\s*)(["\'])(?P<href>javascript:[^"\']+)\2',
        flags=re.IGNORECASE,
    )
    neutralized_javascript_links = 0

    def replace_javascript_anchor_href(match: re.Match[str]) -> str:
        nonlocal neutralized_javascript_links
        href = match.group("href").strip()
        neutralized_javascript_links += 1
        escaped_href = html.escape(href, quote=True)
        prefix = match.group(1)
        return (
            f'{prefix}"#" '
            f'data-migration-link-status="needs-review" '
            f'data-migration-link-reason="javascript-href" '
            f'data-migration-original-href="{escaped_href}"'
        )

    updated = javascript_anchor_pattern.sub(replace_javascript_anchor_href, updated)
    if neutralized_javascript_links:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Neutralized non-Canvas javascript: links for manual review",
                count=neutralized_javascript_links,
            )
        )

    # Collapse *consecutive runs* of 3 or more empty spacer paragraphs to a
    # single spacer.  Isolated or paired spacers are preserved because they are
    # often intentional breathing room between content sections.  Only large
    # runs (common in D2L Brightspace template pages) are collapsed.
    _empty_p_src = (
        r"(?:<p\b[^>]*>\s*(?:&nbsp;|<br\s*/?>|\s|"
        r"<span\b[^>]*>\s*(?:&nbsp;|<br\s*/?>|\s)*</span>)*</p>)"
    )
    empty_para_run_pattern = re.compile(
        rf"(?:{_empty_p_src}\s*){{3,}}",
        flags=re.IGNORECASE,
    )
    removed_empty_paragraphs = 0

    def _collapse_empty_para_run(m: re.Match[str]) -> str:
        nonlocal removed_empty_paragraphs
        count = len(re.findall(r"<p\b", m.group(0), flags=re.IGNORECASE))
        removed_empty_paragraphs += count - 1
        return "<p>&nbsp;</p>\n"

    updated = empty_para_run_pattern.sub(_collapse_empty_para_run, updated)
    if removed_empty_paragraphs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Collapsed excessive empty-spacer paragraph runs (3+) to a single spacer to reduce Canvas layout drift while preserving intentional spacing",
                count=removed_empty_paragraphs,
            )
        )

    # Remove whitespace filler around images inside headings (common after
    # Brightspace -> Canvas conversion where many &nbsp;/<br> tokens are used for
    # manual spacing in source templates).
    heading_pattern = re.compile(
        r"<h(?P<level>[1-6])(?P<attrs>\b[^>]*)>(?P<body>.*?)</h(?P=level)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    normalized_heading_whitespace = 0

    def normalize_heading_body(match: re.Match[str]) -> str:
        nonlocal normalized_heading_whitespace
        body = match.group("body")
        if "<img" not in body.lower():
            return match.group(0)
        rebuilt = re.sub(r"(?:&nbsp;|\s|<br\s*/?>){3,}", " ", body, flags=re.IGNORECASE)
        rebuilt = re.sub(r"\s{2,}", " ", rebuilt).strip()
        if rebuilt == body:
            return match.group(0)
        normalized_heading_whitespace += 1
        return f'<h{match.group("level")}{match.group("attrs")}>{rebuilt}</h{match.group("level")}>'

    updated = heading_pattern.sub(normalize_heading_body, updated)
    if normalized_heading_whitespace:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Collapsed excessive whitespace fillers around heading images",
                count=normalized_heading_whitespace,
            )
        )

    metadata_tag_pattern = re.compile(r"<[a-z][^>]*>", flags=re.IGNORECASE)
    removed_editor_artifact_attrs = 0

    def strip_editor_artifact_attrs(match: re.Match[str]) -> str:
        nonlocal removed_editor_artifact_attrs
        tag = match.group(0)
        updated_tag, removed = _EDITOR_ARTIFACT_ATTR_RE.subn("", tag)
        if removed:
            removed_editor_artifact_attrs += removed
        return updated_tag

    updated = metadata_tag_pattern.sub(strip_editor_artifact_attrs, updated)
    if removed_editor_artifact_attrs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed pasted-editor metadata attributes and Brightspace authoring artifacts",
                count=removed_editor_artifact_attrs,
            )
        )

    removed_d2l_style_tokens = 0

    def strip_d2l_style_tokens(match: re.Match[str]) -> str:
        nonlocal removed_d2l_style_tokens
        tag = match.group(0)
        updated_tag = tag
        removed = False
        style_match = re.search(
            r'(\bstyle\s*=\s*)(["\'])(?P<value>.*?)(\2)',
            updated_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if style_match is None:
            return updated_tag
        style_value = style_match.group("value")
        if "--d2l-" in style_value.lower():
            updated_tag, removed = _remove_inline_style_keys(updated_tag, {"box-sizing"})
            style_match = re.search(
                r'(\bstyle\s*=\s*)(["\'])(?P<value>.*?)(\2)',
                updated_tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if style_match is None:
                if removed:
                    removed_d2l_style_tokens += 1
                return updated_tag
            style_value = style_match.group("value")
        rebuilt_value, d2l_removed = _D2L_STYLE_TOKEN_RE.subn("", style_value)
        if d2l_removed:
            rebuilt_value = re.sub(r";\s*;", ";", rebuilt_value)
            rebuilt_value = re.sub(r"^\s*;\s*", "", rebuilt_value)
            rebuilt_value = re.sub(r"\s{2,}", " ", rebuilt_value).strip()
            if rebuilt_value and not rebuilt_value.endswith(";"):
                rebuilt_value += ";"
            if rebuilt_value:
                updated_tag = (
                    updated_tag[: style_match.start("value")]
                    + rebuilt_value
                    + updated_tag[style_match.end("value") :]
                )
            else:
                updated_tag = updated_tag[: style_match.start()] + updated_tag[style_match.end() :]
                updated_tag = re.sub(r"\s{2,}", " ", updated_tag)
                updated_tag = updated_tag.replace("< ", "<")
            removed_d2l_style_tokens += d2l_removed
        elif removed:
            removed_d2l_style_tokens += 1
        return updated_tag

    updated = metadata_tag_pattern.sub(strip_d2l_style_tokens, updated)
    if removed_d2l_style_tokens:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed D2L-specific inline style tokens that do not translate to Canvas",
                count=removed_d2l_style_tokens,
            )
        )

    image_spacing_attr_pattern = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
    removed_image_spacing_attrs = 0
    layout_attrs_converted = 0

    def strip_legacy_image_attrs(match: re.Match[str]) -> str:
        nonlocal removed_image_spacing_attrs
        nonlocal layout_attrs_converted
        tag = match.group(0)
        updated_tag = tag

        # Before stripping deprecated HTML4 layout attributes, convert them to
        # equivalent inline CSS so that image float and spacing layout is preserved
        # in Canvas.  align → float/margin, hspace → margin-left/right,
        # vspace → margin-top/bottom.
        css_additions: dict[str, str] = {}

        align_val = (_extract_attr_value(updated_tag, "align") or "").lower().strip()
        if align_val == "left":
            css_additions["float"] = "left"
            css_additions.setdefault("margin", "0 12px 8px 0")
        elif align_val == "right":
            css_additions["float"] = "right"
            css_additions.setdefault("margin", "0 0 8px 12px")
        elif align_val == "center":
            css_additions["display"] = "block"
            css_additions["margin"] = "0 auto"

        hspace_raw = (_extract_attr_value(updated_tag, "hspace") or "").strip()
        if hspace_raw.isdigit() and int(hspace_raw) > 0:
            css_additions.setdefault("margin-left",  f"{hspace_raw}px")
            css_additions.setdefault("margin-right", f"{hspace_raw}px")

        vspace_raw = (_extract_attr_value(updated_tag, "vspace") or "").strip()
        if vspace_raw.isdigit() and int(vspace_raw) > 0:
            css_additions.setdefault("margin-top",    f"{vspace_raw}px")
            css_additions.setdefault("margin-bottom", f"{vspace_raw}px")

        if css_additions:
            updated_tag, css_changed = _merge_inline_style(updated_tag, css_additions)
            if css_changed:
                layout_attrs_converted += 1

        updated_tag, removed = re.subn(
            r"\s(?:align|border|hspace|vspace)\s*=\s*(?:([\"']).*?\1|[^\s>]+)",
            "",
            updated_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if removed:
            removed_image_spacing_attrs += removed
        return updated_tag

    updated = image_spacing_attr_pattern.sub(strip_legacy_image_attrs, updated)
    if layout_attrs_converted:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Converted deprecated image layout attributes (align/hspace/vspace) to inline CSS to preserve text-wrap and float layout",
                count=layout_attrs_converted,
            )
        )
    if removed_image_spacing_attrs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed deprecated image spacing attributes (align/border/hspace/vspace)",
                count=removed_image_spacing_attrs,
            )
        )

    # Ensure all content images scale responsively.  Add max-width: 100% and
    # height: auto to any <img> that does not already carry a max-width style.
    # Template asset icons are skipped here because the overlay pass already
    # sets correct sizing on those.
    img_responsive_pattern = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
    images_made_responsive = 0

    def _make_img_responsive(m: re.Match[str]) -> str:
        nonlocal images_made_responsive
        tag = m.group(0)
        style_lower = (_extract_attr_value(tag, "style") or "").lower()
        if "max-width" in style_lower:
            return tag
        src_lower = (_extract_attr_value(tag, "src") or "").lower()
        if "templateassets/" in src_lower:
            return tag
        updated_tag, changed = _merge_inline_style(tag, {"max-width": "100%", "height": "auto"})
        if changed:
            images_made_responsive += 1
        return updated_tag

    updated = img_responsive_pattern.sub(_make_img_responsive, updated)
    if images_made_responsive:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Added responsive max-width styling to content images to prevent Canvas overflow",
                count=images_made_responsive,
            )
        )

    # Convert fixed pixel-width tables wider than 500 px to a fluid layout so
    # they do not cause horizontal scroll in Canvas.
    wide_table_pattern = re.compile(r"<table\b[^>]*>", flags=re.IGNORECASE)
    tables_made_responsive = 0

    def _make_table_responsive(m: re.Match[str]) -> str:
        nonlocal tables_made_responsive
        tag = m.group(0)
        style = _extract_attr_value(tag, "style") or ""
        width_m = re.search(r"(?:^|;)\s*width\s*:\s*(\d+)px", style, flags=re.IGNORECASE)
        if not width_m:
            return tag
        px_value = int(width_m.group(1))
        if px_value <= 500:
            return tag
        updated_tag, _ = _remove_inline_style_keys(tag, {"width"})
        updated_tag, changed = _merge_inline_style(
            updated_tag, {"width": "100%", "max-width": f"{px_value}px"}
        )
        if changed:
            tables_made_responsive += 1
        return updated_tag

    updated = wide_table_pattern.sub(_make_table_responsive, updated)
    if tables_made_responsive:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Converted fixed-pixel table widths to fluid layout to prevent horizontal overflow in Canvas",
                count=tables_made_responsive,
            )
        )

    empty_container_pattern = re.compile(
        r"<(?P<tag>div|span|footer)\b[^>]*>\s*</(?P=tag)>",
        flags=re.IGNORECASE,
    )
    removed_empty_containers = 0
    while True:
        updated, removed = empty_container_pattern.subn("", updated)
        if removed <= 0:
            break
        removed_empty_containers += removed
    if removed_empty_containers:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed empty wrapper containers left by legacy template markup",
                count=removed_empty_containers,
            )
        )

    if applied_policy.normalize_divider_styling:
        hr_pattern = re.compile(r"<hr\b[^>]*>", flags=re.IGNORECASE)
        normalized_hr_count = 0

        def normalize_hr_tag(match: re.Match[str]) -> str:
            nonlocal normalized_hr_count
            tag = match.group(0)
            style_match = re.search(
                r'(?<=\s)style\s*=\s*(["\'])(?P<style>[^"\']*)\1',
                tag,
                flags=re.IGNORECASE,
            )
            if style_match is None:
                normalized_style = (
                    "border: 0; height: 2px; background-color: #ac1a2f; width: 100%; margin: 16px 0;"
                )
                if tag.endswith("/>"):
                    rebuilt = f'{tag[:-2].rstrip()} style="{normalized_style}" />'
                else:
                    rebuilt = f'{tag[:-1].rstrip()} style="{normalized_style}">'
                if rebuilt != tag:
                    normalized_hr_count += 1
                return rebuilt
            style_text = style_match.group("style")
            color_match = re.search(
                r"(?:background-color|color|border(?:-top)?(?:-color)?)\s*:\s*(?P<color>#[0-9a-f]{3,8}|[a-z]+)",
                style_text,
                flags=re.IGNORECASE,
            )
            color_value = color_match.group("color") if color_match is not None else "#ac1a2f"
            normalized_style = (
                f"border: 0; height: 2px; background-color: {color_value}; width: 100%; margin: 16px 0;"
            )
            rebuilt = (
                tag[: style_match.start("style")]
                + normalized_style
                + tag[style_match.end("style") :]
            )
            if rebuilt != tag:
                normalized_hr_count += 1
            return rebuilt

        updated = hr_pattern.sub(normalize_hr_tag, updated)
        if normalized_hr_count:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Normalized horizontal divider styling for Canvas consistency",
                    count=normalized_hr_count,
                )
            )

    if _is_syllabus_like_page(file_path=file_path, document_title_text=document_title_text, content=updated):
        updated, normalized_syllabus_tables = _normalize_syllabus_tables(updated)
        if normalized_syllabus_tables:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Normalized syllabus tables to match current template caption and header styling",
                    count=normalized_syllabus_tables,
                )
            )

    def normalize_display_text(value: str) -> str:
        lowered = value.lower()
        lowered = re.sub(r"\s+", " ", lowered).strip()
        lowered = lowered.replace("&", "and")
        lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    def tokenized(value: str) -> list[str]:
        return [token for token in value.split(" ") if token and token not in {"the", "a", "an"}]

    def is_duplicate_title_block(block_text: str, title_text: str) -> bool:
        normalized_block = normalize_display_text(block_text)
        normalized_title_text = normalize_display_text(title_text)
        if not normalized_block or not normalized_title_text:
            return False
        if normalized_block == normalized_title_text:
            return True
        block_tokens = tokenized(normalized_block)
        title_tokens = tokenized(normalized_title_text)
        if block_tokens and title_tokens and block_tokens == title_tokens:
            return True
        ratio = SequenceMatcher(a=normalized_block, b=normalized_title_text).ratio()
        return ratio >= 0.92

    normalized_title = normalize_display_text(document_title_text)
    if normalized_title:
        block_pattern = re.compile(
            r"<(?P<tag>h[1-6]|p)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
            flags=re.IGNORECASE | re.DOTALL,
        )
        duplicate_title_block_span: tuple[int, int] | None = None
        inspected_candidates = 0
        for match in block_pattern.finditer(updated):
            block_text = re.sub(r"<[^>]+>", " ", match.group("body"))
            block_text = html.unescape(block_text)
            normalized_block = normalize_display_text(block_text)
            if not normalized_block:
                continue
            if normalized_block == "printerfriendlyversion":
                continue
            if is_duplicate_title_block(block_text, document_title_text):
                duplicate_title_block_span = (match.start(), match.end())
                break
            inspected_candidates += 1
            if inspected_candidates >= 3:
                break

        if duplicate_title_block_span is not None:
            start, end = duplicate_title_block_span
            updated = updated[:start] + updated[end:]
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed duplicate in-body heading/paragraph that repeated the Canvas page title",
                    count=1,
                )
            )

    if applied_policy.neutralize_legacy_d2l_links:
        anchor_href_pattern = re.compile(
            r'(<a\b[^>]*\bhref\s*=\s*)([\"\'])(?P<href>[^\"\']+)\2',
            flags=re.IGNORECASE,
        )
        rewritten_quicklinks = 0
        neutralized_links = 0

        def replace_anchor_href(match: re.Match[str]) -> str:
            nonlocal rewritten_quicklinks
            nonlocal neutralized_links
            href = match.group("href").strip()
            rewritten = _rewrite_quicklink_coursefile_href(href)
            if rewritten:
                rewritten_quicklinks += 1
                prefix = match.group(1)
                return f'{prefix}"{html.escape(rewritten, quote=True)}"'
            if not _is_legacy_d2l_link(_html_unescape_repeated(href)):
                return match.group(0)

            neutralized_links += 1
            escaped_href = html.escape(href, quote=True)
            prefix = match.group(1)
            return (
                f'{prefix}"#" '
                f'data-migration-link-status="needs-review" '
                f'data-migration-original-href="{escaped_href}"'
            )

        updated = anchor_href_pattern.sub(replace_anchor_href, updated)
        if rewritten_quicklinks:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Converted D2L quickLink coursefile links to package-relative file references",
                    count=rewritten_quicklinks,
                )
            )
        if neutralized_links:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Neutralized legacy D2L links requiring manual relink in Canvas",
                    count=neutralized_links,
                )
            )

    return updated, applied


def repair_missing_local_references(
    content: str,
    *,
    file_path: str,
    available_paths: Iterable[str],
    keep_alt_text_for_missing_images: bool = True,
) -> tuple[str, list[AppliedChange]]:
    """
    Repair or neutralize local references that don't map to files in the package.
    """
    available_set = {str(path).strip().replace("\\", "/").lstrip("/") for path in available_paths}
    if not available_set:
        return content, []

    lower_map: dict[str, str] = {}
    by_dir_and_tail: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_basename: dict[str, list[str]] = defaultdict(list)
    for path in available_set:
        lowered = path.lower()
        lower_map.setdefault(lowered, path)
        directory = posixpath.dirname(path).lower()
        basename = posixpath.basename(path)
        by_basename[basename.lower()].append(path)
        if "_" in basename:
            tail = basename.split("_", 1)[1].lower()
            by_dir_and_tail[(directory, tail)].append(path)

    rewired_local_refs = 0
    neutralized_anchor_refs = 0
    removed_missing_images = 0
    replaced_missing_images_with_alt = 0

    def resolve_candidate(raw_url: str) -> tuple[str, str]:
        """Return (status, target_path_or_empty)."""
        parsed = urlparse(raw_url)
        if parsed.scheme or raw_url.startswith("//"):
            return ("keep", "")
        path_text = unquote(parsed.path).strip()
        if not path_text:
            return ("keep", "")
        if raw_url.strip().startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return ("keep", "")

        current_dir = posixpath.dirname(file_path)
        if path_text.startswith("/"):
            normalized = posixpath.normpath(path_text.lstrip("/"))
        else:
            normalized = posixpath.normpath(posixpath.join(current_dir, path_text))
        normalized = normalized.lstrip("./")
        lowered = normalized.lower()

        if normalized in available_set:
            return ("rewrite", normalized)
        if lowered in lower_map:
            return ("rewrite", lower_map[lowered])

        target_dir = posixpath.dirname(normalized).lower()
        target_basename = posixpath.basename(normalized)

        if target_basename:
            exact_name_matches = by_basename.get(target_basename.lower(), [])
            if len(exact_name_matches) == 1:
                return ("rewrite", exact_name_matches[0])

        if "_" in target_basename:
            tail = target_basename.split("_", 1)[1].lower()
            tail_matches = by_dir_and_tail.get((target_dir, tail), [])
            if len(tail_matches) == 1:
                return ("rewrite", tail_matches[0])
            if not tail_matches:
                # Try cross-directory unique tail match as fallback.
                all_tail_matches = [
                    path
                    for (dir_key, tail_key), paths in by_dir_and_tail.items()
                    if tail_key == tail
                    for path in paths
                ]
                if len(all_tail_matches) == 1:
                    return ("rewrite", all_tail_matches[0])

        return ("missing", normalized)

    def _strip_is_course_file_query(query: str) -> str:
        if not query:
            return ""
        pairs = parse_qsl(query, keep_blank_values=True)
        if not pairs:
            return query
        filtered = [(k, v) for (k, v) in pairs if k.lower() != "iscoursefile"]
        if len(filtered) == len(pairs):
            return query
        if not filtered:
            return ""
        return urlencode(filtered, doseq=True)

    def rebuild_url(raw_url: str, target_path: str) -> str:
        parsed = urlparse(raw_url)
        current_dir = posixpath.dirname(file_path).strip().replace("\\", "/").lstrip("./")
        if current_dir:
            rebuilt_path = posixpath.relpath(target_path, start=current_dir)
        else:
            rebuilt_path = target_path
        rebuilt_path = rebuilt_path.replace("\\", "/")
        if rebuilt_path in {"", "."}:
            rebuilt_path = posixpath.basename(target_path) or target_path
        query = _strip_is_course_file_query(parsed.query)
        if query:
            rebuilt_path += f"?{query}"
        if parsed.fragment:
            rebuilt_path += f"#{parsed.fragment}"
        return rebuilt_path

    def replace_attr(tag_html: str, attr_name: str, value: str) -> str:
        pattern = re.compile(
            rf'(\b{attr_name}\s*=\s*)(["\'])([^"\']*)(\2)',
            flags=re.IGNORECASE,
        )
        return pattern.sub(lambda m: f'{m.group(1)}"{html.escape(value, quote=True)}"', tag_html, count=1)

    anchor_pattern = re.compile(r"<a\b[^>]*\bhref\s*=\s*([\"'])(?P<href>[^\"']+)\1[^>]*>", flags=re.IGNORECASE)

    def replace_anchor(match: re.Match[str]) -> str:
        nonlocal rewired_local_refs
        nonlocal neutralized_anchor_refs
        original_tag = match.group(0)
        href = match.group("href").strip()
        status, target = resolve_candidate(href)
        if status == "keep":
            return original_tag
        if status == "rewrite":
            new_href = rebuild_url(href, target)
            if new_href == href:
                return original_tag
            rewired_local_refs += 1
            return replace_attr(original_tag, "href", new_href)

        neutralized_anchor_refs += 1
        tag = replace_attr(original_tag, "href", "#")
        if "data-migration-link-status=" not in tag:
            original_href = html.escape(href, quote=True)
            tag = tag[:-1] + (
                f' data-migration-link-status="needs-review" '
                f'data-migration-original-href="{original_href}">'
            )
        return tag

    updated = anchor_pattern.sub(replace_anchor, content)

    image_pattern = re.compile(r"<img\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>", flags=re.IGNORECASE)

    def replace_image(match: re.Match[str]) -> str:
        nonlocal rewired_local_refs
        nonlocal removed_missing_images
        nonlocal replaced_missing_images_with_alt
        original_tag = match.group(0)
        src = match.group("src").strip()
        status, target = resolve_candidate(src)
        if status == "keep":
            return original_tag
        if status == "rewrite":
            new_src = rebuild_url(src, target)
            if new_src == src:
                return original_tag
            rewired_local_refs += 1
            return replace_attr(original_tag, "src", new_src)

        removed_missing_images += 1
        if not keep_alt_text_for_missing_images:
            return ""
        alt_match = re.search(
            r'\balt\s*=\s*(["\'])(?P<alt>.*?)\1',
            original_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        alt_text = alt_match.group("alt").strip() if alt_match else ""
        if not alt_text or alt_text.lower() in {"banner", "logo", "image", "decorative"}:
            return ""
        replaced_missing_images_with_alt += 1
        return f'<span class="migration-missing-image-text">{html.escape(alt_text, quote=False)}</span>'

    updated = image_pattern.sub(replace_image, updated)

    applied: list[AppliedChange] = []
    if rewired_local_refs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Rewired local file references to existing package assets",
                count=rewired_local_refs,
            )
        )
    if neutralized_anchor_refs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Neutralized unresolved local links for manual relink",
                count=neutralized_anchor_refs,
            )
        )
    if removed_missing_images:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed unresolved local image references",
                count=removed_missing_images,
            )
        )
    if replaced_missing_images_with_alt:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Replaced removed unresolved images with existing alt text",
                count=replaced_missing_images_with_alt,
            )
        )

    return updated, applied


def apply_best_practice_enforcer(
    content: str,
    *,
    file_path: str = "",
    policy: BestPracticeEnforcerPolicy | None = None,
) -> tuple[str, list[AppliedChange]]:
    """
    Apply a safe subset of best-practice enforcement rules.
    """
    applied_policy = policy or BestPracticeEnforcerPolicy()
    if not applied_policy.enabled:
        return content, []

    updated = content
    applied: list[AppliedChange] = []
    normalized_file = file_path.lower()
    lowered = html.unescape(updated).lower()

    if applied_policy.enforce_module_checklist_closer:
        is_intro_or_checklist = (
            "introduction and objectives" in normalized_file
            or "introduction and checklist" in normalized_file
            or "module checklist" in lowered
        )
        required_closer_plain = "contact your instructor with any questions or post in the course q&a."
        required_closer_html = "Contact your instructor with any questions or post in the Course Q&amp;A."
        local_contact_plain = "contact your instructor with any course questions."
        local_contact_html = "Contact your instructor with any course questions."
        local_contact_activity_html = (
            "Contact your instructor with any course questions or use the Activity Feed on the Home Page."
        )
        has_local_contact_guidance = local_contact_plain in lowered
        has_activity_feed_guidance = "activity feed on the home page" in lowered
        checklist_support_present = required_closer_plain in lowered or has_local_contact_guidance
        if is_intro_or_checklist:
            updated = re.sub(
                r'<p\b[^>]*class\s*=\s*(["\'])migration-checklist-closer\1[^>]*>.*?</p>',
                "",
                updated,
                flags=re.IGNORECASE | re.DOTALL,
            )
            lowered = html.unescape(updated).lower()
            has_local_contact_guidance = local_contact_plain in lowered
            has_activity_feed_guidance = "activity feed on the home page" in lowered
            checklist_support_present = required_closer_plain in lowered or has_local_contact_guidance
        if is_intro_or_checklist and not checklist_support_present:
            closer_added = False

            heading_match = re.search(
                r"<h[1-6][^>]*>.*?module checklist.*?</h[1-6]>",
                updated,
                flags=re.IGNORECASE,
                # Headings can include template icons/strong tags after visual normalization.
            )
            if heading_match:
                remainder = updated[heading_match.end() :]
                ul_open_match = re.search(r"<ul\b[^>]*>", remainder, flags=re.IGNORECASE)
                if ul_open_match:
                    after_ul = remainder[ul_open_match.end() :]
                    ul_close_match = re.search(r"</ul>", after_ul, flags=re.IGNORECASE)
                    if ul_close_match:
                        insert_at = heading_match.end() + ul_open_match.end() + ul_close_match.start()
                        updated = updated[:insert_at] + f"\n  <li>{required_closer_html}</li>" + updated[insert_at:]
                        closer_added = True

            if not closer_added:
                fallback_closer_html = local_contact_activity_html if has_activity_feed_guidance else local_contact_html
                fallback_block = (
                    "<p class=\"migration-checklist-closer\">"
                    "<strong>Module Checklist Reminder:</strong> "
                    f"{fallback_closer_html}"
                    "</p>"
                )
                body_close_match = re.search(r"</body>", updated, flags=re.IGNORECASE)
                if body_close_match:
                    updated = updated[: body_close_match.start()] + fallback_block + "\n" + updated[body_close_match.start() :]
                else:
                    updated = updated + "\n" + fallback_block
                closer_added = True

            if closer_added:
                lowered = updated.lower()
                applied.append(
                    AppliedChange(
                        category="best_practice",
                        description="Added required Module Checklist closing reminder",
                        count=1,
                    )
                )

        # Some courses already include local support guidance and end up with a stale
        # template-only "Course Q&A" reminder duplicated at the end of the checklist.
        redundant_support_guidance = (
            has_local_contact_guidance
            and has_activity_feed_guidance
            and required_closer_plain in lowered
        )
        if is_intro_or_checklist and redundant_support_guidance:
            stale_qna_pattern = re.compile(
                r"\s*<li\b[^>]*>(?:(?!</li>).)*?contact\s+your\s+instructor(?:(?!</li>).)*?course\s+q(?:\s|&nbsp;|<[^>]+>|&amp;|&)*a(?:(?!</li>).)*?</li>\s*",
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated, removed_count = stale_qna_pattern.subn("\n", updated)
            if removed_count:
                lowered = html.unescape(updated).lower()
                applied.append(
                    AppliedChange(
                        category="best_practice",
                        description="Removed redundant template Course Q&A checklist reminder when course-specific support guidance was already present",
                        count=removed_count,
                    )
                )

    heading_pattern = re.compile(
        r"<h(?P<level>[1-6])(?P<attrs>\b[^>]*)>(?P<body>.*?)</h(?P=level)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    heading_repairs = 0
    previous_level: int | None = None
    rebuilt_parts: list[str] = []
    cursor = 0

    for match in heading_pattern.finditer(updated):
        original_level = int(match.group("level"))
        normalized_level = original_level
        if previous_level is not None and original_level - previous_level > 1:
            normalized_level = previous_level + 1

        rebuilt_parts.append(updated[cursor : match.start()])
        if normalized_level != original_level:
            heading_repairs += 1
        rebuilt_parts.append(
            f"<h{normalized_level}{match.group('attrs')}>{match.group('body')}</h{normalized_level}>"
        )
        cursor = match.end()
        previous_level = normalized_level

    if rebuilt_parts:
        rebuilt_parts.append(updated[cursor:])
        updated = "".join(rebuilt_parts)

    if heading_repairs:
        applied.append(
            AppliedChange(
                category="best_practice",
                description="Promoted heading levels to remove accessibility-breaking heading jumps",
                count=heading_repairs,
            )
        )

    if applied_policy.ensure_external_links_new_tab:
        anchor_tag_pattern = re.compile(r"<a\b[^>]*>", flags=re.IGNORECASE)
        updated_link_count = 0

        def replace_external_anchor(match: re.Match[str]) -> str:
            nonlocal updated_link_count
            tag = match.group(0)
            href_match = re.search(
                r'\bhref\s*=\s*(["\'])(?P<href>[^"\']+)\1',
                tag,
                flags=re.IGNORECASE,
            )
            if href_match is None:
                return tag

            href_value = href_match.group("href").strip()
            if not re.match(r"^https?://", href_value, flags=re.IGNORECASE):
                return tag

            original = tag
            if re.search(r"\btarget\s*=", tag, flags=re.IGNORECASE) is None:
                tag = tag[:-1] + ' target="_blank">'

            if re.search(r'\btarget\s*=\s*(["\'])_blank\1', tag, flags=re.IGNORECASE):
                rel_match = re.search(
                    r'\brel\s*=\s*(["\'])(?P<rel>[^"\']*)\1',
                    tag,
                    flags=re.IGNORECASE,
                )
                if rel_match is None:
                    tag = tag[:-1] + ' rel="noopener noreferrer">'
                else:
                    rel_tokens = [token for token in rel_match.group("rel").split() if token]
                    rel_lower = {token.lower() for token in rel_tokens}
                    updated_tokens = list(rel_tokens)
                    if "noopener" not in rel_lower:
                        updated_tokens.append("noopener")
                    if "noreferrer" not in rel_lower:
                        updated_tokens.append("noreferrer")
                    updated_rel = " ".join(updated_tokens).strip()
                    tag = (
                        tag[: rel_match.start("rel")]
                        + updated_rel
                        + tag[rel_match.end("rel") :]
                    )

            if tag != original:
                updated_link_count += 1
            return tag

        updated = anchor_tag_pattern.sub(replace_external_anchor, updated)
        if updated_link_count:
            applied.append(
                AppliedChange(
                    category="best_practice",
                    description="Updated external links to open in new tab with safe rel attributes",
                    count=updated_link_count,
                )
            )

    return updated, applied


def detect_manual_review_issues(content: str, triggers: Iterable[ManualTrigger]) -> list[ManualReviewIssue]:
    issues: list[ManualReviewIssue] = []
    for trigger in triggers:
        pattern = re.compile(trigger.pattern, flags=_re_flags(trigger.flags))
        match = pattern.search(content)
        if match:
            snippet = match.group(0)
            evidence = snippet[:120].replace("\n", " ")
            issues.append(ManualReviewIssue(reason=trigger.reason, evidence=evidence))
    return issues


def check_template_heuristics(
    content: str,
    file_path: str = "",
    policy: TemplateCheckPolicy | None = None,
) -> list[ManualReviewIssue]:
    """Detect likely template-compliance issues derived from local reference docs."""
    applied_policy = policy or TemplateCheckPolicy()
    issues: list[ManualReviewIssue] = []
    lowered = html.unescape(content).lower()
    normalized_file = file_path.lower()

    # Instructor note placeholders should be resolved before release.
    if applied_policy.check_instructor_notes and re.search(r"\[\s*instructor note\s*:", content, flags=re.IGNORECASE):
        issues.append(
            ManualReviewIssue(
                reason="Instructor Note placeholder remains in content",
                evidence="[Instructor Note: ...]",
            )
        )

    # Common template placeholders from syllabus/page templates.
    if applied_policy.check_template_placeholders:
        placeholder_patterns = (
            r"\bfill in text here\b",
            r"\[title here\]",
            r"\bxx\b",
        )
        for pattern in placeholder_patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                issues.append(
                    ManualReviewIssue(
                        reason="Template placeholder text remains in content",
                        evidence=match.group(0)[:120],
                    )
                )
                break

    # Legacy quiz wording should be replaced for pilot shells.
    if applied_policy.check_legacy_quiz_wording and re.search(
        r'click\s+the\s+"?take\s+the\s+quiz"?\s+button',
        content,
        flags=re.IGNORECASE,
    ):
        issues.append(
            ManualReviewIssue(
                reason='Legacy quiz instructions detected ("Take the Quiz")',
                evidence='Click the "Take the Quiz" button',
            )
        )

    # IC pages should retain checklist closing reminder based on templates.
    is_intro_checklist_page = (
        "introduction and checklist" in lowered
        or "introduction-and-checklist" in normalized_file
    )
    required_mc_closer = "contact your instructor with any questions or post in the course q&a"
    acceptable_local_closer = "contact your instructor with any course questions."
    if (
        applied_policy.require_mc_closing_bullet
        and is_intro_checklist_page
        and required_mc_closer not in lowered
        and acceptable_local_closer not in lowered
    ):
        issues.append(
            ManualReviewIssue(
                reason="Module Checklist closing reminder appears to be missing",
                evidence="Contact your instructor with any questions or post in the Course Q&A.",
            )
        )

    return issues


def check_accessibility_heuristics(content: str) -> list[ManualReviewIssue]:
    issues: list[ManualReviewIssue] = []

    for match in re.finditer(r"<img\b[^>]*>", content, flags=re.IGNORECASE):
        img_tag = match.group(0)
        alt_match = re.search(r"\balt\s*=\s*([\"'])(.*?)\1", img_tag, flags=re.IGNORECASE | re.DOTALL)
        is_presentational = bool(
            re.search(r'\brole\s*=\s*(["\'])presentation\1', img_tag, flags=re.IGNORECASE)
            or re.search(r'\baria-hidden\s*=\s*(["\'])true\1', img_tag, flags=re.IGNORECASE)
        )
        if alt_match is None:
            issues.append(
                ManualReviewIssue(
                    reason="Image missing alt attribute",
                    evidence=img_tag[:120],
                )
            )
        elif not alt_match.group(2).strip() and not is_presentational:
            issues.append(
                ManualReviewIssue(
                    reason="Image alt attribute is empty",
                    evidence=img_tag[:120],
                )
            )

    heading_levels = [
        int(m.group(1))
        for m in re.finditer(r"<h([1-6])\b", content, flags=re.IGNORECASE)
    ]
    for previous, current in zip(heading_levels, heading_levels[1:]):
        if current - previous > 1:
            issues.append(
                ManualReviewIssue(
                    reason="Heading level jump detected",
                    evidence=f"h{previous} -> h{current}",
                )
            )

    for table_match in re.finditer(r"<table\b.*?</table>", content, flags=re.IGNORECASE | re.DOTALL):
        table_html = table_match.group(0)
        caption_match = re.search(r"<caption\b[^>]*>(?P<body>.*?)</caption>", table_html, flags=re.IGNORECASE | re.DOTALL)
        if caption_match is None or not _plain_text(caption_match.group("body")):
            issues.append(
                ManualReviewIssue(
                    reason="Table missing caption",
                    evidence=table_html[:120].replace("\n", " "),
                )
            )

    for link_match in re.finditer(r"<a\b[^>]*>(.*?)</a>", content, flags=re.IGNORECASE | re.DOTALL):
        visible_text = re.sub(r"<[^>]+>", "", link_match.group(1))
        if visible_text.strip().lower() in {"click here", "here", "learn more", "more"}:
            issues.append(
                ManualReviewIssue(
                    reason="Non-descriptive link text",
                    evidence=visible_text.strip()[:120],
                )
            )

    return issues

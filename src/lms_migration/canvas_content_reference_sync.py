from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .canvas_api import (
    fetch_course_files,
    fetch_course_folders,
    fetch_course_modules,
    fetch_course_page,
    fetch_course_pages,
    normalize_base_url,
    update_course_page_body,
)


_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")
_REF_PREFIX_RE = re.compile(
    r"(?P<prefix>(?:Extra\s+Credit\s+)?(?:Discussion|Assignment|Dropbox|Quiz))\s*\|\s*",
    flags=re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r"<h(?P<level>[2-4])(?P<attrs>[^>]*)>(?P<body>.*?)</h(?P=level)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r'(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(["\'])(?P<value>.*?)\2',
    flags=re.IGNORECASE | re.DOTALL,
)
_IMG_FILE_RE = re.compile(r"/files/(?P<id>\d+)/(?:preview|download)", flags=re.IGNORECASE)
_DISCUSSION_ID_RE = re.compile(r"/discussion_topics/(?P<id>\d+)", flags=re.IGNORECASE)
_ASSIGNMENT_ID_RE = re.compile(r"/assignments/(?P<id>\d+)", flags=re.IGNORECASE)
_PAGE_SLUG_RE = re.compile(r"/pages/(?P<slug>[^/?#\"']+)", flags=re.IGNORECASE)
_TOKEN_RE = re.compile(r"\S+")

_REFERENCE_STOP_PATTERNS = (
    re.compile(r"\s+(?:and\s+read|using|on\s+the|in\s+the|due\b|is\s+based\b)", re.IGNORECASE),
    re.compile(r"\s+\*", re.IGNORECASE),
    re.compile(r"\s+[.:;,(]", re.IGNORECASE),
)
_INLINE_REFERENCE_TAG_RE = re.compile(
    r"<(?P<tag>strong|b|em|span)\b[^>]*>\s*(?P<body>(?:Extra\s+Credit\s+)?(?:Discussion|Assignment|Dropbox|Quiz)\s*\|(?:[^<]|&nbsp;)*?)\s*</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_INLINE_REFERENCE_PREFIX_TAG_RE = re.compile(
    r"<(?P<tag>strong|b|em|span)\b[^>]*>\s*(?P<body>(?:Extra\s+Credit\s+)?(?:Discussion|Assignment|Dropbox|Quiz)\s*\|)\s*</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _normalize_inline_reference_markup(value: str) -> str:
    updated = value
    updated = re.sub(
        r"</(?:strong|b|em|span)>\s*<(?:(?:strong|b|em|span)\b[^>]*)>\s*\|",
        " |",
        updated,
        flags=re.IGNORECASE,
    )
    updated = _INLINE_REFERENCE_PREFIX_TAG_RE.sub(lambda m: m.group("body") + " ", updated)
    updated = _INLINE_REFERENCE_TAG_RE.sub(lambda m: m.group("body"), updated)
    return updated


@dataclass(frozen=True)
class Candidate:
    kind: str
    full_title: str
    short_title: str
    core_label: str
    module_number: int | None = None


@dataclass
class ModuleContext:
    module_number: int
    name: str
    page_urls: set[str] = field(default_factory=set)
    discussions: list[Candidate] = field(default_factory=list)
    assignments: list[Candidate] = field(default_factory=list)
    quizzes: list[Candidate] = field(default_factory=list)
    pages: list[Candidate] = field(default_factory=list)


def _normalize_folder_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/").lower()


def _module_number_from_name(name: str) -> int | None:
    match = re.match(r"^\s*Module\s+(?!T)(\d+)\s*:", name, flags=re.IGNORECASE)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _strip_module_prefix(title: str) -> str:
    return re.sub(r"^\s*Module\s+T?\d+\s*:\s*", "", title.strip(), flags=re.IGNORECASE)


def _extract_core_label(short_title: str) -> str:
    if ":" in short_title:
        _, remainder = short_title.split(":", 1)
        remainder = remainder.strip()
        if remainder:
            return remainder
    return short_title.strip()


def _normalize_text(value: str) -> str:
    cleaned = html.unescape(value or "").replace("\xa0", " ").lower()
    cleaned = cleaned.replace("tedtalk", "ted talk")
    cleaned = cleaned.replace("lgtbq", "lgbtq")
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9+]+", " ", cleaned)
    return " ".join(cleaned.split())


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = {token for token in _normalize_text(left).split() if token}
    right_tokens = {token for token in _normalize_text(right).split() if token}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def _candidate_score(reference_text: str, candidate: Candidate) -> float:
    normalized_reference = _normalize_text(reference_text)
    if not normalized_reference:
        return 0.0

    variants = [candidate.core_label, candidate.short_title]
    best = 0.0
    for variant in variants:
        normalized_variant = _normalize_text(variant)
        if not normalized_variant:
            continue
        seq = SequenceMatcher(None, normalized_reference, normalized_variant).ratio()
        overlap = _token_overlap_score(reference_text, variant)
        score = (seq * 0.7) + (overlap * 0.3)
        if normalized_reference == normalized_variant:
            score += 0.3
        elif normalized_reference in normalized_variant or normalized_variant in normalized_reference:
            score += 0.15
        best = max(best, min(score, 1.5))
    return best


def _candidate_kind_from_module_item(item_type: str, title: str) -> str | None:
    short_title = _strip_module_prefix(title)
    normalized = short_title.lower()
    if item_type == "Discussion":
        return "discussion"
    if item_type == "Assignment":
        if normalized.startswith("quiz:") or "quiz:" in normalized or normalized.startswith(
            "final quiz"
        ):
            return "quiz"
        return "assignment"
    if item_type == "Page":
        return "page"
    return None


def _build_module_contexts(modules: list[dict[str, Any]]) -> tuple[dict[str, ModuleContext], dict[str, list[Candidate]]]:
    page_to_module: dict[str, ModuleContext] = {}
    course_candidates: dict[str, list[Candidate]] = {
        "discussion": [],
        "assignment": [],
        "quiz": [],
        "page": [],
    }

    for module in modules:
        if not isinstance(module, dict):
            continue
        module_name = str(module.get("name") or "").strip()
        module_number = _module_number_from_name(module_name)
        if module_number is None:
            continue
        context = ModuleContext(module_number=module_number, name=module_name)
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            kind = _candidate_kind_from_module_item(item_type, title)
            if kind is None:
                continue
            candidate = Candidate(
                kind=kind,
                full_title=title,
                short_title=_strip_module_prefix(title),
                core_label=_extract_core_label(_strip_module_prefix(title)),
                module_number=module_number,
            )
            attr_name = "quizzes" if kind == "quiz" else f"{kind}s"
            getattr(context, attr_name).append(candidate)
            course_candidates[kind].append(candidate)
            if kind == "page":
                page_url = str(item.get("page_url") or "").strip()
                if page_url:
                    context.page_urls.add(page_url)
                    page_to_module[page_url] = context
    return page_to_module, course_candidates


def _extract_attributes(tag_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(tag_attrs):
        attrs[str(match.group("name")).lower()] = html.unescape(match.group("value"))
    return attrs


def _resolve_anchor_target(attrs: dict[str, str]) -> tuple[str, str] | None:
    values = [attrs.get("href", ""), attrs.get("data-api-endpoint", "")]
    for value in values:
        if not value:
            continue
        discussion_match = _DISCUSSION_ID_RE.search(value)
        if discussion_match is not None:
            return "discussion", discussion_match.group("id")
        assignment_match = _ASSIGNMENT_ID_RE.search(value)
        if assignment_match is not None:
            return "assignment", assignment_match.group("id")
        page_match = _PAGE_SLUG_RE.search(value)
        if page_match is not None:
            return "page", html.unescape(page_match.group("slug"))
    return None


def _rewrite_anchor_labels(
    html_text: str,
    *,
    discussion_title_by_id: dict[str, str],
    assignment_title_by_id: dict[str, str],
    page_title_by_slug: dict[str, str],
) -> tuple[str, int]:
    rewrites = 0

    def replace_anchor(match: re.Match[str]) -> str:
        nonlocal rewrites
        attrs = _extract_attributes(match.group("attrs"))
        target = _resolve_anchor_target(attrs)
        if target is None:
            return match.group(0)
        kind, identifier = target
        replacement = ""
        if kind == "discussion":
            replacement = discussion_title_by_id.get(identifier, "")
        elif kind == "assignment":
            replacement = assignment_title_by_id.get(identifier, "")
        elif kind == "page":
            replacement = page_title_by_slug.get(identifier, "")
        if not replacement:
            return match.group(0)
        current_text = re.sub(r"<[^>]+>", "", match.group("body"))
        if html.unescape(current_text).strip() == replacement.strip():
            return match.group(0)
        rewrites += 1
        return f"<a{match.group('attrs')}>{html.escape(replacement)}</a>"

    return _ANCHOR_RE.sub(replace_anchor, html_text), rewrites


def _candidate_pool_for_kind(
    *,
    kind: str,
    module_context: ModuleContext | None,
    course_candidates: dict[str, list[Candidate]],
) -> list[Candidate]:
    if module_context is not None:
        attr_name = "quizzes" if kind == "quiz" else f"{kind}s"
        module_pool = getattr(module_context, attr_name, [])
        if module_pool:
            return module_pool
    return course_candidates.get(kind, [])


def _prefix_end_positions(text: str) -> list[int]:
    positions: set[int] = set()
    tokens = list(_TOKEN_RE.finditer(text))
    for token in tokens[:12]:
        positions.add(token.end())
    for pattern in _REFERENCE_STOP_PATTERNS:
        match = pattern.search(text)
        if match is not None and match.start() > 0:
            positions.add(match.start())
    if text:
        positions.add(min(len(text), 80))
    return sorted(pos for pos in positions if pos > 0)


def _strip_trailing_punctuation(value: str) -> tuple[str, str]:
    stripped = value.rstrip()
    suffix = ""
    while stripped and stripped[-1] in ".:,;":
        suffix = stripped[-1] + suffix
        stripped = stripped[:-1].rstrip()
    return stripped, suffix


def _pick_reference_candidate(
    *,
    rest_text: str,
    kind: str,
    module_context: ModuleContext | None,
    course_candidates: dict[str, list[Candidate]],
) -> tuple[Candidate | None, int, str]:
    pool = _candidate_pool_for_kind(
        kind=kind,
        module_context=module_context,
        course_candidates=course_candidates,
    )
    if not pool:
        return None, 0, ""

    best_candidate: Candidate | None = None
    best_position = 0
    best_suffix = ""
    best_score = 0.0

    for end_pos in _prefix_end_positions(rest_text):
        raw_prefix = rest_text[:end_pos]
        cleaned_prefix, suffix = _strip_trailing_punctuation(raw_prefix)
        cleaned_prefix = cleaned_prefix.strip()
        if not cleaned_prefix:
            continue
        for candidate in pool:
            score = _candidate_score(cleaned_prefix, candidate)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_position = end_pos
                best_suffix = suffix

    if best_candidate is not None and best_score >= 0.64:
        chosen_end = best_position
        if module_context is not None:
            boundary_end = len(rest_text)
            for pattern in _REFERENCE_STOP_PATTERNS:
                match = pattern.search(rest_text[best_position:])
                if match is not None:
                    boundary_end = best_position + match.start()
                    break
            trailing = rest_text[best_position:boundary_end]
            if trailing and not re.match(
                r"\s*(?:and\b|using\b|on\b|in\b|due\b|is\b|[.:;,(])",
                trailing,
                flags=re.IGNORECASE,
            ):
                chosen_end = boundary_end
        return best_candidate, chosen_end, best_suffix

    if module_context is not None and len(pool) == 1:
        candidate = pool[0]
        positions = _prefix_end_positions(rest_text)
        chosen_end = positions[-1] if positions else 0
        for pattern in _REFERENCE_STOP_PATTERNS:
            match = pattern.search(rest_text)
            if match is not None and match.start() > 0:
                chosen_end = match.start()
                break
        if chosen_end > 0:
            raw_prefix = rest_text[:chosen_end]
            cleaned_prefix, suffix = _strip_trailing_punctuation(raw_prefix)
            if cleaned_prefix.strip():
                return candidate, chosen_end, suffix
    return None, 0, ""


def _generic_reference_lead(rest_text: str) -> bool:
    leading = _normalize_text(rest_text)
    return leading.startswith(
        (
            "this quiz",
            "the quiz",
            "this assignment",
            "the assignment",
            "this discussion",
            "the discussion",
        )
    )


def _rewrite_reference_text_chunk(
    text: str,
    *,
    module_context: ModuleContext | None,
    course_candidates: dict[str, list[Candidate]],
    practice_page_title: str | None,
) -> tuple[str, int]:
    rewrites = 0
    out_parts: list[str] = []
    cursor = 0

    while True:
        match = _REF_PREFIX_RE.search(text, cursor)
        if match is None:
            out_parts.append(text[cursor:])
            break
        out_parts.append(text[cursor:match.start()])
        prefix_text = match.group("prefix")
        lower_prefix = prefix_text.lower()
        if "discussion" in lower_prefix:
            kind = "discussion"
        elif "quiz" in lower_prefix:
            kind = "quiz"
        else:
            kind = "assignment"

        rest = text[match.end() :]
        pool = _candidate_pool_for_kind(
            kind=kind,
            module_context=module_context,
            course_candidates=course_candidates,
        )
        if (
            module_context is not None
            and len(pool) == 1
            and _generic_reference_lead(rest)
        ):
            candidate = pool[0]
            boundary_end = len(rest)
            punctuation_match = re.search(r"\s+[.:;,()]", rest)
            if punctuation_match is not None and punctuation_match.start() > 0:
                boundary_end = punctuation_match.start()
            out_parts.append(candidate.short_title)
            rewrites += 1
            cursor = match.end() + boundary_end
            continue
        candidate, consumed, suffix = _pick_reference_candidate(
            rest_text=rest,
            kind=kind,
            module_context=module_context,
            course_candidates=course_candidates,
        )
        if candidate is None or consumed <= 0:
            out_parts.append(match.group(0))
            cursor = match.end()
            continue
        replacement_text = candidate.short_title + suffix
        if kind == "assignment":
            trailing_assignment_match = re.match(
                r"(?P<spaces>\s+)(?P<label>assignment\b)",
                rest[consumed:],
                flags=re.IGNORECASE,
            )
            if trailing_assignment_match is not None and candidate.short_title.lower().startswith(
                ("assignment:", "extra credit assignment:", "project:")
            ):
                consumed += trailing_assignment_match.end()
        out_parts.append(replacement_text)
        rewrites += 1
        cursor = match.end() + consumed

    updated = "".join(out_parts)
    updated = re.sub(
        r"\bAssignment:\s+([^<\n]+?)\s+Assignment\b",
        r"Assignment: \1",
        updated,
        flags=re.IGNORECASE,
    )
    if practice_page_title:
        updated = re.sub(
            r"\bPractice Games page\b",
            f"{practice_page_title} page",
            updated,
            flags=re.IGNORECASE,
        )
        updated = re.sub(
            r"\bStudy Mates Activities on the Practice Activity for Quiz page\b",
            f"Study Mates Activities on the {practice_page_title} page",
            updated,
            flags=re.IGNORECASE,
        )
    return updated, rewrites


def _rewrite_textual_references(
    html_text: str,
    *,
    module_context: ModuleContext | None,
    course_candidates: dict[str, list[Candidate]],
) -> tuple[str, int]:
    normalized_html = _normalize_inline_reference_markup(html_text)
    pieces = _TAG_SPLIT_RE.split(normalized_html)
    rewrites = 0
    practice_page_title = None
    if module_context is not None:
        for page_candidate in module_context.pages:
            if "practice activity for quiz" in page_candidate.short_title.lower():
                practice_page_title = page_candidate.short_title
                break

    for index, piece in enumerate(pieces):
        if not piece or piece.startswith("<"):
            continue
        updated_piece, piece_rewrites = _rewrite_reference_text_chunk(
            piece,
            module_context=module_context,
            course_candidates=course_candidates,
            practice_page_title=practice_page_title,
        )
        if updated_piece != piece:
            pieces[index] = updated_piece
        rewrites += piece_rewrites

    return "".join(pieces), rewrites


def _normalize_heading_inner_html(value: str) -> tuple[str, int]:
    changes = 0
    updated = value
    normalized = updated.replace("\xa0", " ")
    if normalized != updated:
        updated = normalized
        changes += 1
    normalized = re.sub(r">\s*&nbsp;\s*<", "><", updated, flags=re.IGNORECASE)
    normalized = re.sub(r">\s+<", "><", normalized)
    normalized = re.sub(r"(<img\b[^>]*>)\s+", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*&nbsp;\s*", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    normalized = normalized.strip()
    if normalized != updated:
        updated = normalized
        changes += 1
    return updated, changes


def _normalize_template_icon_headings(
    html_text: str,
    *,
    template_icon_ids: set[str],
) -> tuple[str, int, int]:
    level_fixes = 0
    spacing_fixes = 0

    def replace_heading(match: re.Match[str]) -> str:
        nonlocal level_fixes
        nonlocal spacing_fixes
        level = int(match.group("level"))
        body = match.group("body")
        img_match = _IMG_FILE_RE.search(body)
        if img_match is None or img_match.group("id") not in template_icon_ids:
            return match.group(0)
        normalized_body, body_changes = _normalize_heading_inner_html(body)
        new_level = 2 if level in {3, 4} else level
        if new_level != level:
            level_fixes += 1
        if body_changes:
            spacing_fixes += body_changes
        return f"<h{new_level}{match.group('attrs')}>{normalized_body}</h{new_level}>"

    return _HEADING_RE.sub(replace_heading, html_text), level_fixes, spacing_fixes


def sync_canvas_content_references(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_base = normalize_base_url(base_url)
    modules = fetch_course_modules(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    page_summaries = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    files = fetch_course_files(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    folders = fetch_course_folders(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    folder_paths = {
        str(folder.get("id", "")).strip(): _normalize_folder_path(
            str(folder.get("full_name") or folder.get("name") or "")
        )
        for folder in folders
        if isinstance(folder, dict)
    }
    template_icon_ids = {
        str(file_item.get("id", "")).strip()
        for file_item in files
        if isinstance(file_item, dict)
        and folder_paths.get(str(file_item.get("folder_id", "")).strip(), "")
        == "course files/template-images/icons"
    }

    page_to_module, course_candidates = _build_module_contexts(modules)
    discussion_title_by_id: dict[str, str] = {}
    assignment_title_by_id: dict[str, str] = {}
    page_title_by_slug: dict[str, str] = {}

    for module in modules:
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            title = str(item.get("title") or "").strip()
            content_id = str(item.get("content_id") or "").strip()
            if item_type == "Discussion" and content_id and title:
                discussion_title_by_id[content_id] = title
            if item_type == "Assignment" and content_id and title:
                assignment_title_by_id[content_id] = title
            if item_type == "Page":
                page_url = str(item.get("page_url") or "").strip()
                if page_url and title:
                    page_title_by_slug[page_url] = title

    pages_updated = 0
    total_reference_rewrites = 0
    total_anchor_label_rewrites = 0
    total_heading_level_fixes = 0
    total_heading_spacing_fixes = 0
    page_results: list[dict[str, Any]] = []

    for summary in page_summaries:
        if not isinstance(summary, dict):
            continue
        page_url = str(summary.get("url") or "").strip()
        title = str(summary.get("title") or "").strip()
        if not page_url:
            continue

        page = fetch_course_page(
            base_url=normalized_base,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        body_html = str(page.get("body") or "")
        module_context = page_to_module.get(page_url)

        updated = body_html
        updated, anchor_rewrites = _rewrite_anchor_labels(
            updated,
            discussion_title_by_id=discussion_title_by_id,
            assignment_title_by_id=assignment_title_by_id,
            page_title_by_slug=page_title_by_slug,
        )
        updated, reference_rewrites = _rewrite_textual_references(
            updated,
            module_context=module_context,
            course_candidates=course_candidates,
        )
        updated, heading_level_fixes, heading_spacing_fixes = _normalize_template_icon_headings(
            updated,
            template_icon_ids=template_icon_ids,
        )

        changed = updated != body_html
        if changed and not dry_run:
            update_course_page_body(
                base_url=normalized_base,
                course_id=course_id,
                page_url=page_url,
                body_html=updated,
                token=token,
            )
            pages_updated += 1
        elif changed:
            pages_updated += 1

        if changed or anchor_rewrites or reference_rewrites or heading_level_fixes or heading_spacing_fixes:
            page_results.append(
                {
                    "page_id": str(page.get("page_id") or summary.get("page_id") or ""),
                    "page_url": page_url,
                    "title": title,
                    "module": module_context.name if module_context else "",
                    "anchor_label_rewrites": anchor_rewrites,
                    "text_reference_rewrites": reference_rewrites,
                    "icon_heading_level_fixes": heading_level_fixes,
                    "icon_heading_spacing_fixes": heading_spacing_fixes,
                    "updated": changed,
                }
            )

        total_anchor_label_rewrites += anchor_rewrites
        total_reference_rewrites += reference_rewrites
        total_heading_level_fixes += heading_level_fixes
        total_heading_spacing_fixes += heading_spacing_fixes

    report = {
        "base_url": normalized_base,
        "course_id": course_id,
        "dry_run": dry_run,
        "summary": {
            "pages_scanned": len(page_summaries),
            "pages_updated": pages_updated,
            "anchor_label_rewrites": total_anchor_label_rewrites,
            "text_reference_rewrites": total_reference_rewrites,
            "icon_heading_level_fixes": total_heading_level_fixes,
            "icon_heading_spacing_fixes": total_heading_spacing_fixes,
        },
        "page_results": page_results,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite Canvas page references so D2L-style checklist/page text aligns "
            "with the current Canvas page, assignment, quiz, and discussion names."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = sync_canvas_content_references(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json=args.output_json,
        dry_run=args.dry_run,
    )
    print(json.dumps(report["summary"], indent=2))
    print(args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

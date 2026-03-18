from __future__ import annotations

import html
import re


_MATHML_BLOCK_RE = re.compile(r"<math\b[^>]*>.*?</math>", flags=re.IGNORECASE | re.DOTALL)
_WIRIS_ANNOTATION_RE = re.compile(
    r"<annotation\b[^>]*\bencoding\s*=\s*([\"'])wiris\1[^>]*>.*?</annotation>",
    flags=re.IGNORECASE | re.DOTALL,
)
_GENERIC_ANNOTATION_RE = re.compile(
    r"<annotation(?:-xml)?\b[^>]*>.*?</annotation(?:-xml)?>",
    flags=re.IGNORECASE | re.DOTALL,
)
_MATH_CONTENT_TAG_RE = re.compile(
    r"<(?:mi|mn|mo|mtext|ms|mglyph|mfrac|msqrt|mroot|msub|msup|msubsup|mover|munder|munderover|"
    r"mfenced|menclose|mmultiscripts|mtable|mtr|mtd)\b",
    flags=re.IGNORECASE,
)
_EQUATION_IMAGE_RE = re.compile(
    r"<img\b[^>]*\bclass\s*=\s*([\"'])[^\"']*\bequation_image\b[^\"']*\1[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_DISPLAY_MATHML_RE = re.compile(
    r"<math\b[^>]*(?:\bdisplay\s*=\s*([\"'])block\1|\bmode\s*=\s*([\"'])display\2)[^>]*>.*?</math>",
    flags=re.IGNORECASE | re.DOTALL,
)
_STANDALONE_EQUATION_IMAGE_BLOCK_RE = re.compile(
    r"<(?P<tag>p|div)\b(?P<attrs>[^>]*)>\s*(?P<body>(?:<span\b[^>]*>\s*)?<img\b[^>]*>(?:\s*</span>)?)\s*</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_RAW_TEX_PATTERNS = (
    re.compile(r"(?<!\\)\$\$(?P<body>.+?)(?<!\\)\$\$", flags=re.DOTALL),
    re.compile(r"\\\((?P<body>.+?)\\\)", flags=re.DOTALL),
    re.compile(r"\\\[(?P<body>.+?)\\\]", flags=re.DOTALL),
)


def normalize_math_handling(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"canvas", "canvas-equation", "canvas-equation-compatible"}:
        return "canvas-equation-compatible"
    if normalized in {"audit", "audit-only"}:
        return "audit-only"
    return "preserve-semantic"


def count_mathml(content: str) -> int:
    return len(_MATHML_BLOCK_RE.findall(content))


def count_wiris_annotations(content: str) -> int:
    return len(_WIRIS_ANNOTATION_RE.findall(content))


def find_equation_image_tags(content: str) -> list[str]:
    return [match.group(0) for match in _EQUATION_IMAGE_RE.finditer(content)]


def count_equation_images(content: str) -> int:
    return len(find_equation_image_tags(content))


def count_raw_tex_delimiters(content: str) -> int:
    return sum(len(pattern.findall(content)) for pattern in _RAW_TEX_PATTERNS)


def count_display_math_blocks(content: str) -> int:
    standalone_equation_images = 0
    for match in _STANDALONE_EQUATION_IMAGE_BLOCK_RE.finditer(content):
        attrs = match.group("attrs") or ""
        if "migration-display-equation" in attrs.lower():
            continue
        if _EQUATION_IMAGE_RE.search(match.group("body") or "") is None:
            continue
        standalone_equation_images += 1
    return len(_DISPLAY_MATHML_RE.findall(content)) + standalone_equation_images


def count_absolute_equation_image_urls(content: str) -> int:
    count = 0
    for tag in find_equation_image_tags(content):
        src = _extract_attr_value(tag, "src")
        if src and src.lower().startswith(("http://", "https://")) and "/equation_images/" in src.lower():
            count += 1
    return count


def count_equation_images_missing_alt(content: str) -> int:
    count = 0
    for tag in find_equation_image_tags(content):
        alt_value = _extract_attr_value(tag, "alt")
        if alt_value is None or not alt_value.strip():
            count += 1
    return count


def count_equation_images_missing_source(content: str) -> int:
    count = 0
    for tag in find_equation_image_tags(content):
        has_equation_source = bool(_extract_attr_value(tag, "data-equation-content"))
        has_title = bool((_extract_attr_value(tag, "title") or "").strip())
        if not has_equation_source and not has_title:
            count += 1
    return count


def math_modes_present(content: str) -> tuple[str, ...]:
    modes: list[str] = []
    if count_mathml(content):
        modes.append("mathml")
    if count_equation_images(content):
        modes.append("equation_image")
    if count_raw_tex_delimiters(content):
        modes.append("raw_tex")
    return tuple(modes)


def is_trivial_mathml_block(block_html: str) -> bool:
    without_annotations = _GENERIC_ANNOTATION_RE.sub("", block_html)
    if _MATH_CONTENT_TAG_RE.search(without_annotations):
        return False
    text = _plain_text(without_annotations)
    return not text


def count_empty_mathml_stubs(content: str) -> int:
    return sum(1 for match in _MATHML_BLOCK_RE.finditer(content) if is_trivial_mathml_block(match.group(0)))


def strip_empty_mathml_stubs(content: str) -> tuple[str, int]:
    removed = 0

    def replace_block(match: re.Match[str]) -> str:
        nonlocal removed
        block_html = match.group(0)
        if not is_trivial_mathml_block(block_html):
            return block_html
        removed += 1
        return ""

    updated = _MATHML_BLOCK_RE.sub(replace_block, content)
    return updated, removed


def _extract_attr_value(tag_html: str, attr_name: str) -> str | None:
    match = re.search(
        rf"\b{attr_name}\s*=\s*([\"'])(?P<value>.*?)\1",
        tag_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    return html.unescape(match.group("value"))


def _plain_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

"""style_inference.py — Infer Canvas template mappings from a Brightspace export.

Analyzes a D2L export zip to extract:
  - Brand color palette and font stacks (from inline ``style=""`` attributes,
    which are the only CSS data present in a D2L export — linked CSS files are
    served from the Brightspace server and are not included in the export).
  - Data-driven icon alias suggestions: for each D2L template icon basename
    found in the course, the nearest heading text and alt text are aggregated
    across all HTML files and matched against semantic patterns to suggest the
    most appropriate Canvas template icon.
  - Inferred alias JSON (same structure as ``template_asset_aliases.json``)
    ready to pass directly as ``--template-alias-map-json``.

Three output files are produced per run::

    <stem>.style-inference.json   — full machine-readable report
    <stem>.style-inference.md     — human-readable summary
    <stem>.inferred-aliases.json  — drop-in replacement / starting point for
                                    template_asset_aliases.json

CLI::

    lms-style-inference path/to/d2l-export.zip \\
        [--template-package path/to/template.imscc] \\
        [--output-dir output/course-id]
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class StyleInferenceError(RuntimeError):
    """Raised when style inference cannot proceed."""


# ─── Semantic icon patterns ────────────────────────────────────────────────────
#
# Each entry: (frozenset of keyword fragments, canonical icon type).
# Keywords are matched against the lowercased combined text of heading labels
# and alt text surrounding each icon reference.
# ORDER MATTERS — checked top-to-bottom; first match above threshold wins.

_SEMANTIC_ICON_PATTERNS: list[tuple[frozenset[str], str]] = [
    (
        frozenset(
            {
                "practice",
                "try this",
                "apply",
                "hands-on",
                "do this",
                "exercise",
                "activity",
            }
        ),
        "practice",
    ),
    (
        frozenset(
            {
                "objective",
                "learning objective",
                "goal",
                "outcome",
                "competency",
                "what you will learn",
                "by the end",
                "learning target",
            }
        ),
        "objectives",
    ),
    (
        frozenset(
            {
                "resource",
                "reading",
                "reference",
                "further reading",
                "explore resources",
                "materials",
                "explore this",
            }
        ),
        "resources",
    ),
    (
        frozenset(
            {
                "quiz",
                "self-check",
                "knowledge check",
                "check your knowledge",
                "exam",
                "test yourself",
                "assessment",
            }
        ),
        "quiz",
    ),
    (
        frozenset(
            {
                "important",
                "note",
                "tip",
                "remember",
                "key point",
                "heads up",
                "warning",
                "caution",
                "critical step",
            }
        ),
        "note",
    ),
    (
        frozenset(
            {
                "checklist",
                "to-do",
                "to do",
                "action item",
                "complete by",
                "due",
                "assignment checklist",
            }
        ),
        "checklist",
    ),
    (
        frozenset(
            {"video", "watch", "lecture", "view", "media", "presentation", "screencast"}
        ),
        "video",
    ),
    (
        frozenset(
            {
                "discussion",
                "forum",
                "post",
                "reply",
                "respond",
                "discuss",
                "share your response",
            }
        ),
        "discussion",
    ),
    (
        frozenset(
            {
                "assignment",
                "submit",
                "turn in",
                "upload",
                "homework",
                "project",
                "paper due",
            }
        ),
        "assignment",
    ),
    (
        frozenset(
            {
                "overview",
                "introduction",
                "intro",
                "welcome",
                "module overview",
                "course overview",
                "agenda",
                "faculty",
                "instructor",
                "about this",
                "course info",
            }
        ),
        "overview",
    ),
    (
        frozenset(
            {
                "syllabus",
                "policy",
                "policies",
                "guidelines",
                "expectations",
                "schedule",
                "course schedule",
                "grading",
                "require",
                "grade breakdown",
            }
        ),
        "syllabus",
    ),
    (
        frozenset(
            {
                "announcement",
                "news",
                "update",
                "notice",
                "alert",
                "reminder",
                "coming up",
            }
        ),
        "announcement",
    ),
]

# Canvas icon candidates per semantic type (ordered by confidence, best first).
# These are basenames from the Sinclair eLearn template package.
_CANVAS_ICON_CANDIDATES: dict[str, list[str]] = {
    "practice": ["circle-arrow.png", "practice.png"],
    "objectives": ["bullseye.png", "checklist.png"],
    "resources": ["folder.png", "bookmark.png"],
    "quiz": ["quiz.png", "pencil.png"],
    "note": ["exclamation.png", "flag.png", "info.png"],
    "checklist": ["checklist.png", "checkmark.png"],
    "video": ["video.png", "media.png"],
    "discussion": ["discussion.png", "speech-bubble.png"],
    "assignment": ["paper.png", "assignment.png"],
    "overview": ["info.png", "globe.png"],
    "syllabus": ["paper.png", "folder.png"],
    "announcement": ["announcement.png", "exclamation.png"],
}

# Icon basenames that are structural/decorative and should not be aliased.
_SKIP_ICON_BASENAMES: frozenset[str] = frozenset(
    {
        "logo.png",
        "logo-white.png",
        "footer.png",
        "banner.png",
        "banner.jpg",
        "course-card.png",
        "course-card.jpg",
        "header.png",
        "header.jpg",
        "background.png",
        "background.jpg",
        "rule_brown_gradient.png",
    }
)

# Prefix/suffix patterns in the basename itself that indicate decorative elements.
_SKIP_BASENAME_PATTERNS: tuple[str, ...] = (
    "rule_",  # e.g. rule_brown_gradient.png — horizontal rule graphics
    "_rule",
    "divider",
    "separator",
    "banner_",  # numbered banners (banner_01.jpg etc.) handled by alias map
)

# Alt text values that carry no semantic meaning.
_DECORATIVE_ALTS: frozenset[str] = frozenset(
    {
        "",
        "logo",
        "image",
        "icon",
        "decoration",
        "decorative",
        "spacer",
        "graphic",
        "banner",
        "header",
        "footer",
        " ",
    }
)

# Colors that are standard defaults — filtered when detecting brand palette.
_DEFAULT_COLORS: frozenset[str] = frozenset(
    {
        "000000",
        "111111",
        "222222",
        "333333",
        "444444",
        "555555",
        "666666",
        "777777",
        "888888",
        "999999",
        "aaaaaa",
        "bbbbbb",
        "cccccc",
        "cdcdcd",
        "dddddd",
        "e5e5e5",
        "ebebeb",
        "eeeeee",
        "f0f0f0",
        "f5f5f5",
        "f9f9f9",
        "fafafa",
        "fcfcfc",
        "ffffff",
    }
)

# ─── Regexes ──────────────────────────────────────────────────────────────────

# Matches inline style attributes (greedy-safe with quoted values).
_STYLE_ATTR_RE = re.compile(
    r'<([a-zA-Z][a-zA-Z0-9]*)[^>]+style=["\']([^"\']+)["\']', re.IGNORECASE
)

# Matches a CSS property: value; pair inside a style string.
_CSS_PROP_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+?)(?:;|$)", re.IGNORECASE)

# Six-digit and three-digit hex colors.
_HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")

# rgb/rgba colors.
_RGB_COLOR_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)

# CSS font-family value — the full value after font-family:
_FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;"\']+)', re.IGNORECASE)

# D2L template icon references (both URL casings, any depth after the marker).
_D2L_ICON_REF_RE = re.compile(
    r'<img[^>]+src=["\'][^"\']*?'
    r"(?:brightspace_html_template|Brightspace_HTML_Template)"
    r'[^"\']*?/([^/"\']+\.(?:png|jpg|jpeg|gif|svg))["\'][^>]*>',
    re.IGNORECASE,
)

# Alt attribute within an img tag.
_ALT_ATTR_RE = re.compile(r'\balt=["\']([^"\']*)["\']', re.IGNORECASE)

# Heading tags (h1–h6).
_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.DOTALL | re.IGNORECASE)

# Linked CSS href paths.
_CSS_LINK_RE = re.compile(r'<link[^>]+href=["\']([^"\']+\.css)["\']', re.IGNORECASE)


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class ThemeColors:
    """Brand palette inferred from inline styles across all HTML files."""

    primary: str | None = None
    """Most-used non-default color — likely the brand primary."""

    accent: str | None = None
    """Second most-used non-default color."""

    heading_colors: list[str] = field(default_factory=list)
    """Colors found on heading (h1–h4) elements."""

    all_colors: dict[str, int] = field(default_factory=dict)
    """All non-default hex colors with occurrence counts."""

    css_link_paths: list[str] = field(default_factory=list)
    """Unique external CSS file paths referenced (server-side, unreadable)."""


@dataclass
class FontStacks:
    """Font families inferred from inline styles."""

    primary: str | None = None
    """Most-used font-family value."""

    all_families: list[str] = field(default_factory=list)
    """All distinct font-family values ordered by frequency."""


@dataclass
class IconAliasEntry:
    """Alias suggestion for one D2L icon basename."""

    source_basename: str
    """Lowercase D2L icon filename (e.g. ``explore.png``)."""

    occurrences: int
    """Number of HTML files in which this icon was found."""

    inferred_type: str
    """Semantic category (e.g. ``resources``) or ``generic``."""

    confidence: float
    """0.0–1.0 confidence in the inferred type."""

    canvas_candidates: list[str]
    """Suggested Canvas template icon basenames, best-first."""

    supporting_texts: list[str]
    """Sample heading/alt texts that drove this classification."""


@dataclass
class StyleInferenceResult:
    """Aggregated output of a single style inference run."""

    source_zip: str
    html_files_analyzed: int
    theme_colors: ThemeColors
    font_stacks: FontStacks
    icon_aliases: list[IconAliasEntry]
    unresolved_icons: list[str]
    """D2L icons found but scored below confidence threshold (conf < 0.3)."""


# ─── Extraction helpers ───────────────────────────────────────────────────────


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags and decode basic entities from *text*."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    return text.strip()


def _normalise_hex(color_str: str) -> str | None:
    """Return a lowercase 6-digit hex string or None for invalid input."""
    h = color_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
        return h.lower()
    return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"{r:02x}{g:02x}{b:02x}"


def _is_near_white_or_black(hex6: str) -> bool:
    try:
        r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    except ValueError:
        return False
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return luminance < 30 or luminance > 225  # near-black or near-white


# ─── Core analysis functions ──────────────────────────────────────────────────


def _read_html_files_from_zip(zip_path: Path) -> list[tuple[str, str]]:
    """Return ``(filename, content)`` pairs for all HTML files in the zip."""
    results: list[tuple[str, str]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.lower().endswith((".html", ".htm")):
                try:
                    content = zf.read(name).decode("utf-8", errors="replace")
                    results.append((name, content))
                except Exception:
                    pass
    return results


def _extract_theme_colors(
    html_files: list[tuple[str, str]],
) -> ThemeColors:
    """Collect brand colors from inline ``style=`` attributes across all pages."""
    color_counts: Counter[str] = Counter()
    heading_colors: list[str] = []
    css_links: list[str] = []

    for _name, content in html_files:
        # Collect linked CSS paths (for reporting only).
        for href in _CSS_LINK_RE.findall(content):
            if href not in css_links:
                css_links.append(href)

        # Walk all elements with inline style attributes.
        for tag, style_val in _STYLE_ATTR_RE.findall(content):
            tag_lower = tag.lower()
            # Extract hex colors from color / background-color properties.
            for m in _HEX_COLOR_RE.finditer(style_val):
                hex6 = _normalise_hex(m.group(1))
                if (
                    hex6
                    and hex6 not in _DEFAULT_COLORS
                    and not _is_near_white_or_black(hex6)
                ):
                    color_counts[hex6] += 1
                    if (
                        tag_lower in ("h1", "h2", "h3", "h4")
                        and hex6 not in heading_colors
                    ):
                        heading_colors.append(hex6)

            for m in _RGB_COLOR_RE.finditer(style_val):
                r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hex6 = _rgb_to_hex(r, g, b)
                if hex6 not in _DEFAULT_COLORS and not _is_near_white_or_black(hex6):
                    color_counts[hex6] += 1

    sorted_colors = color_counts.most_common()
    primary = "#" + sorted_colors[0][0] if sorted_colors else None
    accent = "#" + sorted_colors[1][0] if len(sorted_colors) > 1 else None
    heading_colors_hex = ["#" + h for h in heading_colors[:5]]

    return ThemeColors(
        primary=primary,
        accent=accent,
        heading_colors=heading_colors_hex,
        all_colors={"#" + k: v for k, v in sorted_colors},
        css_link_paths=sorted(set(css_links)),
    )


def _extract_font_stacks(html_files: list[tuple[str, str]]) -> FontStacks:
    """Collect font-family declarations from inline ``style=`` attributes."""
    family_counts: Counter[str] = Counter()

    for _name, content in html_files:
        for m in _FONT_FAMILY_RE.finditer(content):
            raw = m.group(1).strip().rstrip(";").strip()
            # Normalise: lowercase, strip surrounding quotes.
            normalised = re.sub(r"\s+", " ", raw.strip("'\"").lower())
            # Skip values that are HTML entities or clearly not font names.
            if normalised and len(normalised) < 120 and "&" not in normalised:
                family_counts[normalised] += 1

    ordered = [fam for fam, _ in family_counts.most_common()]
    primary = ordered[0] if ordered else None
    return FontStacks(primary=primary, all_families=ordered[:10])


def _score_icon_type(
    heading_texts: list[str],
    alt_texts: list[str],
    basename: str = "",
) -> tuple[str, float, list[str]]:
    """Score *heading_texts* + *alt_texts* against semantic icon patterns.

    Falls back to scoring the *basename* itself when heading/alt texts provide
    insufficient signal (e.g. ``gradinginformation.png`` → grading → syllabus).

    Returns ``(inferred_type, confidence, supporting_texts)`` where
    *confidence* is 0.0–1.0 and *supporting_texts* are the text fragments
    that drove the classification.
    """
    # Include a humanised form of the basename as a synthetic text signal so
    # icons like "gradinginformation.png" are self-classifying.
    basename_words = re.sub(
        r"[_\-]",
        " ",
        basename.replace(".png", "")
        .replace(".jpg", "")
        .replace(".gif", "")
        .replace(".svg", ""),
    )
    combined_lower = " ".join(heading_texts + alt_texts + [basename_words]).lower()
    if not combined_lower.strip():
        return "generic", 0.0, []

    scores: Counter[str] = Counter()
    matched_texts: dict[str, list[str]] = defaultdict(list)

    for keywords, icon_type in _SEMANTIC_ICON_PATTERNS:
        for kw in keywords:
            if kw in combined_lower:
                scores[icon_type] += 1
                # collect which source text matched (prefer real heading/alt text)
                for txt in (
                    heading_texts
                    + alt_texts
                    + ([basename_words] if basename_words else [])
                ):
                    if kw in txt.lower() and txt not in matched_texts[icon_type]:
                        matched_texts[icon_type].append(txt)

    if not scores:
        return "generic", 0.0, []

    best_type, best_score = scores.most_common(1)[0]
    total_score = sum(scores.values())
    confidence = round(min(best_score / max(total_score, 1) * 1.5, 1.0), 2)
    return best_type, confidence, matched_texts[best_type][:3]


def _scan_icon_contexts(
    html_files: list[tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    """Find every D2L template icon ref and collect its surrounding context.

    Returns a dict keyed by lowercase icon basename. Each value::

        {
            "occurrences": int,
            "alt_texts": list[str],
            "heading_texts": list[str],
        }
    """
    contexts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"occurrences": 0, "alt_texts": [], "heading_texts": []}
    )

    for _name, content in html_files:
        for m in _D2L_ICON_REF_RE.finditer(content):
            raw_basename = m.group(1)
            basename = raw_basename.lower()

            if basename in _SKIP_ICON_BASENAMES:
                continue
            if any(pat in basename for pat in _SKIP_BASENAME_PATTERNS):
                continue

            img_tag = m.group(0)
            img_pos = m.start()

            ctx = contexts[basename]
            ctx["occurrences"] += 1

            # Alt text.
            alt_match = _ALT_ATTR_RE.search(img_tag)
            alt = alt_match.group(1).strip() if alt_match else ""
            cleaned_alt = _strip_html_tags(alt)
            if cleaned_alt.lower() not in _DECORATIVE_ALTS:
                ctx["alt_texts"].append(cleaned_alt)

            # Nearest heading BEFORE the image (within 600 chars).
            pre_window = content[max(0, img_pos - 600) : img_pos]
            heading_matches = list(_HEADING_RE.finditer(pre_window))
            if heading_matches:
                raw_h = heading_matches[-1].group(1)
                h_text = _strip_html_tags(raw_h)
                if h_text:
                    ctx["heading_texts"].append(h_text)

            # Nearest heading AFTER the image (within 400 chars).
            post_window = content[img_pos : img_pos + 400]
            post_headings = list(_HEADING_RE.finditer(post_window))
            if post_headings:
                raw_h = post_headings[0].group(1)
                h_text = _strip_html_tags(raw_h)
                if h_text:
                    ctx["heading_texts"].append(h_text)

    return dict(contexts)


def _build_icon_aliases(
    icon_contexts: dict[str, dict[str, Any]],
    *,
    confidence_threshold: float = 0.3,
) -> tuple[list[IconAliasEntry], list[str]]:
    """Turn raw icon contexts into scored alias entries.

    Returns ``(aliases, unresolved)`` where *unresolved* contains basenames
    that scored below *confidence_threshold*.
    """
    aliases: list[IconAliasEntry] = []
    unresolved: list[str] = []

    for basename, ctx in sorted(icon_contexts.items()):
        itype, confidence, supporting = _score_icon_type(
            ctx["heading_texts"], ctx["alt_texts"], basename=basename
        )
        candidates = _CANVAS_ICON_CANDIDATES.get(itype, ["info.png"])

        entry = IconAliasEntry(
            source_basename=basename,
            occurrences=ctx["occurrences"],
            inferred_type=itype,
            confidence=confidence,
            canvas_candidates=candidates,
            supporting_texts=supporting,
        )

        if confidence >= confidence_threshold:
            aliases.append(entry)
        else:
            unresolved.append(basename)

    return aliases, unresolved


# ─── Orchestrator ─────────────────────────────────────────────────────────────


def infer_styles(
    zip_path: Path,
    *,
    confidence_threshold: float = 0.3,
) -> StyleInferenceResult:
    """Analyse *zip_path* and return a :class:`StyleInferenceResult`.

    Args:
        zip_path: Path to the D2L export ``.zip`` / ``.imscc``.
        confidence_threshold: Minimum confidence (0–1) for an icon alias to be
            included in the resolved list.  Anything below goes to
            ``unresolved_icons``.

    Returns:
        :class:`StyleInferenceResult` with theme data and icon alias
        suggestions.

    Raises:
        :exc:`StyleInferenceError` if the file is not a valid zip.
    """
    if not zip_path.exists():
        raise StyleInferenceError(f"File not found: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise StyleInferenceError(f"Not a valid zip file: {zip_path}")

    html_files = _read_html_files_from_zip(zip_path)

    theme_colors = _extract_theme_colors(html_files)
    font_stacks = _extract_font_stacks(html_files)
    icon_contexts = _scan_icon_contexts(html_files)
    aliases, unresolved = _build_icon_aliases(
        icon_contexts, confidence_threshold=confidence_threshold
    )

    return StyleInferenceResult(
        source_zip=str(zip_path),
        html_files_analyzed=len(html_files),
        theme_colors=theme_colors,
        font_stacks=font_stacks,
        icon_aliases=aliases,
        unresolved_icons=unresolved,
    )


# ─── Alias JSON builder ───────────────────────────────────────────────────────


def build_alias_dict(result: StyleInferenceResult) -> dict[str, Any]:
    """Return a ``{"aliases": {...}}`` dict compatible with ``template_asset_aliases.json``.

    Only aliases with ``confidence >= 0.3`` are included (already filtered by
    :func:`infer_styles`).
    """
    aliases: dict[str, list[str]] = {}
    for entry in result.icon_aliases:
        aliases[entry.source_basename] = entry.canvas_candidates
    return {"aliases": aliases}


# ─── Report writers ───────────────────────────────────────────────────────────


def write_inference_reports(
    result: StyleInferenceResult,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path, Path]:
    """Write JSON, Markdown, and alias-JSON reports to *output_dir*.

    Args:
        result: Inference result from :func:`infer_styles`.
        output_dir: Directory to write reports into (created if absent).
        stem: Filename stem (e.g. ``"d2l-export"``).

    Returns:
        ``(json_path, md_path, alias_json_path)``
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{stem}.style-inference.json"
    md_path = output_dir / f"{stem}.style-inference.md"
    alias_path = output_dir / f"{stem}.inferred-aliases.json"

    # ── JSON report ──────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "source_zip": result.source_zip,
        "html_files_analyzed": result.html_files_analyzed,
        "theme_colors": {
            "primary": result.theme_colors.primary,
            "accent": result.theme_colors.accent,
            "heading_colors": result.theme_colors.heading_colors,
            "all_colors": result.theme_colors.all_colors,
            "css_link_paths": result.theme_colors.css_link_paths,
        },
        "font_stacks": {
            "primary": result.font_stacks.primary,
            "all_families": result.font_stacks.all_families,
        },
        "icon_aliases": [
            {
                "source_basename": e.source_basename,
                "occurrences": e.occurrences,
                "inferred_type": e.inferred_type,
                "confidence": e.confidence,
                "canvas_candidates": e.canvas_candidates,
                "supporting_texts": e.supporting_texts,
            }
            for e in result.icon_aliases
        ],
        "unresolved_icons": result.unresolved_icons,
    }
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── Alias JSON ───────────────────────────────────────────────────────────
    alias_dict = build_alias_dict(result)
    alias_path.write_text(
        json.dumps(alias_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── Markdown report ──────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append(f"# Style Inference Report")
    lines.append(f"")
    lines.append(f"**Source:** `{result.source_zip}`  ")
    lines.append(f"**HTML files analyzed:** {result.html_files_analyzed}")
    lines.append(f"")

    # Colors
    lines.append("## Brand Color Palette")
    lines.append("")
    tc = result.theme_colors
    if tc.primary:
        lines.append(f"| Role | Hex |")
        lines.append(f"|------|-----|")
        lines.append(f"| Primary | `{tc.primary}` |")
        if tc.accent:
            lines.append(f"| Accent | `{tc.accent}` |")
        for hc in tc.heading_colors:
            lines.append(f"| Heading | `{hc}` |")
        lines.append("")
        if len(tc.all_colors) > 2:
            lines.append(f"**All detected brand colors** ({len(tc.all_colors)} total):")
            lines.append("")
            for color_hex, count in list(tc.all_colors.items())[:10]:
                lines.append(f"- `{color_hex}` — {count} occurrence(s)")
            lines.append("")
    else:
        lines.append("_No brand colors detected in inline styles._")
        lines.append("")

    if tc.css_link_paths:
        lines.append(
            "**External CSS files referenced** (server-side; not included in export):"
        )
        lines.append("")
        for p in tc.css_link_paths[:8]:
            lines.append(f"- `{p}`")
        lines.append("")

    # Fonts
    lines.append("## Font Stacks")
    lines.append("")
    fs = result.font_stacks
    if fs.primary:
        lines.append(f"**Primary font:** `{fs.primary}`")
        lines.append("")
        if len(fs.all_families) > 1:
            lines.append("**All detected families:**")
            lines.append("")
            for fam in fs.all_families:
                lines.append(f"- `{fam}`")
            lines.append("")
    else:
        lines.append("_No font-family declarations detected in inline styles._")
        lines.append("")

    # Icon aliases
    lines.append("## Icon Alias Suggestions")
    lines.append("")
    if result.icon_aliases:
        lines.append("| D2L Icon | Type | Confidence | Canvas Candidates | Context |")
        lines.append("|----------|------|------------|-------------------|---------|")
        for e in sorted(result.icon_aliases, key=lambda x: -x.confidence):
            cands = ", ".join(f"`{c}`" for c in e.canvas_candidates)
            ctx_snippet = "; ".join(e.supporting_texts)[:80]
            pct = f"{e.confidence:.0%}"
            lines.append(
                f"| `{e.source_basename}` | {e.inferred_type} | {pct} | {cands} | {ctx_snippet} |"
            )
        lines.append("")
        lines.append(
            f"**{len(result.icon_aliases)}** alias(es) inferred. "
            f"Alias JSON written to `{alias_path.name}`."
        )
        lines.append(
            "Pass it to the migration pipeline with "
            "`--template-alias-map-json <path>`."
        )
        lines.append("")
    else:
        lines.append("_No icon references detected in this export._")
        lines.append("")

    if result.unresolved_icons:
        lines.append("### Unresolved Icons (confidence < 30%)")
        lines.append("")
        lines.append(
            "These icons were found but could not be confidently classified. "
            "Add them manually to the alias JSON."
        )
        lines.append("")
        for bn in result.unresolved_icons:
            lines.append(f"- `{bn}`")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path, md_path, alias_path


# ─── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-style-inference",
        description=(
            "Infer Canvas template mappings from a Brightspace D2L export zip.\n\n"
            "Analyzes inline CSS in HTML files to extract brand colors, font\n"
            "stacks, and data-driven icon alias suggestions. Produces three\n"
            "output files:\n"
            "  <stem>.style-inference.json   — full machine-readable report\n"
            "  <stem>.style-inference.md     — human-readable summary\n"
            "  <stem>.inferred-aliases.json  — drop-in --template-alias-map-json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Path to the D2L export .zip or .imscc file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for output files. Defaults to a subfolder named after "
            "the zip stem in the current directory."
        ),
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.3,
        metavar="FLOAT",
        help=(
            "Minimum confidence (0–1) for an icon alias to be included in the "
            "resolved output. Aliases below this threshold are listed as "
            "unresolved. Default: 0.3."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    zip_path: Path = args.zip_path
    output_dir: Path = args.output_dir or Path(zip_path.stem)
    confidence: float = args.confidence

    if not zip_path.exists():
        parser.error(f"File not found: {zip_path}")

    print(f"Analysing {zip_path.name} …")
    try:
        result = infer_styles(zip_path, confidence_threshold=confidence)
    except StyleInferenceError as exc:
        parser.error(str(exc))
        return  # unreachable — satisfies type checker

    stem = zip_path.stem

    json_p, md_p, alias_p = write_inference_reports(result, output_dir, stem)

    print(f"  HTML files analysed : {result.html_files_analyzed}")
    print(f"  Brand primary color : {result.theme_colors.primary or '(none)'}")
    print(f"  Primary font        : {result.font_stacks.primary or '(none)'}")
    print(
        f"  Icon aliases        : {len(result.icon_aliases)} resolved, "
        f"{len(result.unresolved_icons)} unresolved"
    )
    print(f"")
    print(f"  Reports written to {output_dir}/")
    print(f"    {json_p.name}")
    print(f"    {md_p.name}")
    print(f"    {alias_p.name}  ← use as --template-alias-map-json")

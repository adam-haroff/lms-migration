"""css_parser.py — Inline CSS parsing and layout-intent classification.

Provides reusable utilities for:
  - Parsing CSS inline style strings to structured dicts
  - Classifying layout intent (float, positioning, column, flex/grid, overflow)
  - Finding elements with layout patterns that Canvas may strip or break

Used by html_tools.py (targeted fixes and manual-review flagging) and by the
visual audit pipeline.  No external dependencies — stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

# Pixel-width threshold above which a non-image element is flagged as
# potentially causing horizontal overflow on Canvas responsive pages.
FIXED_WIDTH_THRESHOLD_PX: int = 500


# ─── CSS string ↔ dict ────────────────────────────────────────────────────────


def parse_inline_style(style_str: str) -> dict[str, str]:
    """Parse a CSS inline style string into a normalised ``{property: value}`` dict.

    * Property keys are lowercased and stripped.
    * Values are stripped but their case is preserved.
    * Empty / malformed declarations are silently ignored.
    * When a property is repeated, the last declaration wins.

    Args:
        style_str: A CSS inline style value, e.g. ``"float: left; margin: 0 auto;"``.

    Returns:
        A dict mapping normalised CSS property names to their values.
    """
    result: dict[str, str] = {}
    for chunk in style_str.split(";"):
        piece = chunk.strip()
        if not piece or ":" not in piece:
            continue
        key, _, value = piece.partition(":")
        result[key.strip().lower()] = value.strip()
    return result


def serialize_inline_style(props: dict[str, str]) -> str:
    """Serialise a ``{property: value}`` dict back to a CSS inline style string.

    Returns an empty string for an empty dict.  Declarations are separated by
    ``"; "`` and the string ends with ``";"`` when there is at least one
    declaration.

    Args:
        props: Dict mapping CSS property names to their values.

    Returns:
        A CSS inline style string, or ``""`` if *props* is empty.
    """
    if not props:
        return ""
    return "; ".join(f"{k}: {v}" for k, v in props.items()) + ";"


# ─── Layout-intent classification ─────────────────────────────────────────────


@dataclass(frozen=True)
class LayoutIntent:
    """Summarises the layout-relevant CSS effects present on a single element.

    Each flag corresponds to a CSS pattern that has meaningful visual impact
    and may be lost or broken when content passes through Canvas's sanitiser.
    """

    has_float: bool = False
    """Element uses ``float: left`` or ``float: right``."""

    has_absolute_position: bool = False
    """Element uses ``position: absolute`` or ``position: fixed``.
    Canvas strips positioned layouts, making content invisible."""

    has_fixed_width: bool = False
    """Element has a pixel-width above *FIXED_WIDTH_THRESHOLD_PX*.
    Can cause horizontal overflow on Canvas responsive pages."""

    fixed_width_px: int | None = None
    """Actual pixel value when *has_fixed_width* is ``True``, otherwise ``None``."""

    has_overflow_control: bool = False
    """Element uses ``overflow: hidden``, ``scroll``, or ``auto``.
    May clip content that Canvas renders at different dimensions."""

    has_flex_or_grid: bool = False
    """Element uses ``display: flex`` or ``display: grid``.
    Canvas support is uncertain; layout may collapse."""

    has_multicolumn: bool = False
    """Element uses ``column-count`` or the ``columns`` shorthand.
    Multi-column layouts render as a single column in Canvas."""

    has_z_index: bool = False
    """Element uses ``z-index``.  Meaningless in Canvas's flat rendering."""

    def is_breaking(self) -> bool:
        """Return ``True`` if any flag that *actively breaks* layout is set.

        Actively-breaking patterns are those that make content invisible or
        collapse structure entirely in Canvas: absolute/fixed positioning,
        flex/grid containers, and multi-column layouts.
        """
        return (
            self.has_absolute_position or self.has_flex_or_grid or self.has_multicolumn
        )

    def is_notable(self) -> bool:
        """Return ``True`` if any layout flag is set (including minor ones).

        Used by the document scanner to decide whether an element is worth
        reporting.
        """
        return any(
            getattr(self, f.name) for f in fields(self) if f.name != "fixed_width_px"
        )


# CSS value sets for classification
_FLOAT_VALUES = frozenset({"left", "right"})
_ABSOLUTE_POSITION_VALUES = frozenset({"absolute", "fixed"})
_OVERFLOW_CONTROL_VALUES = frozenset({"hidden", "scroll", "auto"})
_FLEX_GRID_VALUES = frozenset({"flex", "inline-flex", "grid", "inline-grid"})

_PX_RE = re.compile(r"^(\d+(?:\.\d+)?)px$", re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(
    r'\bstyle\s*=\s*(["\'])(?P<value>.*?)\1',
    flags=re.IGNORECASE | re.DOTALL,
)


def classify_layout_intent(tag_html: str) -> LayoutIntent:
    """Classify the layout intent expressed by a tag's inline ``style`` attribute.

    Args:
        tag_html: The full opening tag string, e.g. ``'<div style="float: left">'``.

    Returns:
        A :class:`LayoutIntent` dataclass capturing which layout patterns are
        present.  If the tag has no ``style`` attribute, all flags are ``False``.
    """
    match = _STYLE_ATTR_RE.search(tag_html)
    if match is None:
        return LayoutIntent()

    props = parse_inline_style(match.group("value"))

    # float
    has_float = props.get("float", "").lower() in _FLOAT_VALUES

    # position: absolute / fixed
    has_absolute_position = (
        props.get("position", "").lower() in _ABSOLUTE_POSITION_VALUES
    )

    # width: Npx above threshold
    has_fixed_width = False
    fixed_width_px: int | None = None
    px_match = _PX_RE.match(props.get("width", "").strip())
    if px_match:
        px_val = int(float(px_match.group(1)))
        if px_val > FIXED_WIDTH_THRESHOLD_PX:
            has_fixed_width = True
            fixed_width_px = px_val

    # overflow: hidden / scroll / auto  (covers overflow, overflow-x, overflow-y)
    has_overflow_control = any(
        props.get(key, "").lower() in _OVERFLOW_CONTROL_VALUES
        for key in ("overflow", "overflow-x", "overflow-y")
    )

    # display: flex / grid
    has_flex_or_grid = props.get("display", "").lower() in _FLEX_GRID_VALUES

    # column-count or columns shorthand
    has_multicolumn = "column-count" in props or "columns" in props

    # z-index
    has_z_index = "z-index" in props

    return LayoutIntent(
        has_float=has_float,
        has_absolute_position=has_absolute_position,
        has_fixed_width=has_fixed_width,
        fixed_width_px=fixed_width_px,
        has_overflow_control=has_overflow_control,
        has_flex_or_grid=has_flex_or_grid,
        has_multicolumn=has_multicolumn,
        has_z_index=has_z_index,
    )


# ─── Document-level scanning ──────────────────────────────────────────────────


@dataclass
class LayoutIssue:
    """A single layout-breaking CSS pattern found in an HTML document."""

    tag_html: str
    """The full opening tag string where the issue was detected."""

    offset: int
    """Character offset of *tag_html* within the source document."""

    intent: LayoutIntent
    """The full layout classification for the element."""

    severity: str
    """``"warning"`` for patterns that actively break layout; ``"info"`` otherwise."""

    description: str
    """Human-readable summary of the detected issue(s) on this element."""


# Matches opening tags (not closing, not void elements already handled in
# html_tools.py) that carry a style attribute.  img/br/hr/input are excluded
# because their inline style handling is already done in html_tools.py.
_STYLED_BLOCK_TAG_RE = re.compile(
    r"<(?!/)(?P<tag>"
    r"div|span|section|article|aside|main|header|footer|nav"
    r"|table|td|th|tr|caption"
    r"|p|pre|blockquote|figure|figcaption"
    r"|ul|ol|li|dl|dt|dd"
    r"|h[1-6]|a|button|details|summary"
    r")\b[^>]*\bstyle\s*=[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)

# Any element the pipeline itself authors carries a "migration-" class prefix.
# These are never original Brightspace content and must not be re-flagged.
_PIPELINE_CLASS_RE = re.compile(
    r'\bclass\s*=\s*(["\'])[^"\']*(migration-)[^"\']*(\1)',
    flags=re.IGNORECASE,
)


def find_layout_breaking_elements(html_content: str) -> list[LayoutIssue]:
    """Scan an HTML document for elements with layout-breaking inline CSS.

    Only *notable* issues are returned — elements whose inline styles actively
    break or significantly alter layout in Canvas (i.e. :meth:`LayoutIntent.is_notable`
    returns ``True``).

    Elements already handled elsewhere in the pipeline (``<img>`` float/align
    conversion, Bootstrap class promotion) are excluded to prevent double-reporting.
    Elements generated by the pipeline itself (class contains ``migration-``) are
    also excluded — their overflow/layout CSS is intentional and correct.

    Args:
        html_content: Full HTML document string.

    Returns:
        List of :class:`LayoutIssue` objects in document order.
    """
    issues: list[LayoutIssue] = []

    for m in _STYLED_BLOCK_TAG_RE.finditer(html_content):
        tag_html = m.group(0)
        # Skip elements the pipeline authored — their styles are intentional.
        if _PIPELINE_CLASS_RE.search(tag_html):
            continue
        intent = classify_layout_intent(tag_html)
        if not intent.is_notable():
            continue

        descriptions: list[str] = []
        severity = "info"

        if intent.has_absolute_position:
            descriptions.append(
                "position: absolute/fixed — content may become invisible in Canvas"
            )
            severity = "warning"

        if intent.has_flex_or_grid:
            descriptions.append("display: flex/grid — layout may collapse in Canvas")
            severity = "warning"

        if intent.has_multicolumn:
            descriptions.append(
                "column-count/columns — multi-column layout not supported in Canvas"
            )
            severity = "warning"

        if intent.has_float:
            descriptions.append(
                "float layout — preserved via inline CSS, verify visual result in Canvas"
            )

        if intent.has_fixed_width:
            descriptions.append(
                f"fixed width: {intent.fixed_width_px}px"
                f" (>{FIXED_WIDTH_THRESHOLD_PX}px) — may cause horizontal overflow"
            )

        if intent.has_overflow_control:
            descriptions.append(
                "overflow control — may clip content at Canvas page dimensions"
            )

        if intent.has_z_index:
            descriptions.append("z-index — ignored in Canvas")

        issues.append(
            LayoutIssue(
                tag_html=tag_html,
                offset=m.start(),
                intent=intent,
                severity=severity,
                description="; ".join(descriptions),
            )
        )

    return issues


# ─── Layout CSS degradation ───────────────────────────────────────────────────

# Properties that become orphaned (and meaningless) once `position` is removed.
_ABSOLUTE_POSITION_PROPS = frozenset(
    {
        "position",
        "top",
        "right",
        "bottom",
        "left",
        "z-index",
    }
)

# Flex/grid-specific properties to strip when `display` is downgraded to
# `block`.  The `display` key itself is kept (set to "block"), so it is
# intentionally absent here.
_FLEX_GRID_PROPS = frozenset(
    {
        "flex-direction",
        "flex-wrap",
        "flex-flow",
        "justify-content",
        "align-items",
        "align-content",
        "align-self",
        "gap",
        "row-gap",
        "column-gap",
        "grid",
        "grid-template",
        "grid-template-columns",
        "grid-template-rows",
        "grid-template-areas",
        "grid-auto-flow",
        "grid-auto-columns",
        "grid-auto-rows",
        "grid-column",
        "grid-row",
        "grid-area",
        "flex",
        "flex-grow",
        "flex-shrink",
        "flex-basis",
        "order",
        "place-items",
        "place-content",
        "place-self",
    }
)

# Multi-column layout properties — Canvas renders a single-column view, so
# these produce invisible overflow rather than visible columns.
_MULTICOLUMN_PROPS = frozenset(
    {
        "column-count",
        "columns",
        "column-gap",
        "column-rule",
        "column-rule-color",
        "column-rule-style",
        "column-rule-width",
        "column-width",
        "column-fill",
        "column-span",
    }
)


def degrade_breaking_layout_css(
    html_content: str,
) -> tuple[str, int, int, int]:
    """Degrade inline CSS layout patterns that actively break layout in Canvas.

    Scans styled block elements (excluding pipeline-authored ``migration-*``
    elements) and replaces three classes of breaking CSS with safe equivalents:

    * **position: absolute/fixed** — removes the ``position`` declaration and
      all associated offset properties (``top``, ``right``, ``bottom``,
      ``left``, ``z-index``).  The element becomes in-flow so its content
      remains visible.

    * **display: flex/grid** — degrades to ``display: block`` and removes
      flex/grid-specific properties (``flex-direction``, ``justify-content``,
      ``gap``, grid template properties, etc.) that have no meaning in a
      block context.

    * **column-count / columns** — removes multi-column layout properties.
      Canvas renders a single-column view; multi-column CSS produces invisible
      overflow rather than visible column splits.

    Pipeline-authored elements (class containing ``migration-``) are skipped
    because their layout CSS is intentional.

    Args:
        html_content: Full HTML document string.

    Returns:
        A tuple of ``(new_html, absolute_fixed_count, flex_grid_count,
        multicolumn_count)`` where each count is the number of elements
        whose inline style was modified for that pattern.
    """
    absolute_fixed_count = 0
    flex_grid_count = 0
    multicolumn_count = 0

    def _degrade_tag(m: re.Match[str]) -> str:
        nonlocal absolute_fixed_count, flex_grid_count, multicolumn_count
        tag_html = m.group(0)

        # Never touch elements the pipeline itself authored.
        if _PIPELINE_CLASS_RE.search(tag_html):
            return tag_html

        style_m = _STYLE_ATTR_RE.search(tag_html)
        if style_m is None:
            return tag_html

        props = parse_inline_style(style_m.group("value"))
        changed = False

        # ── position: absolute / fixed ───────────────────────────────────────
        if props.get("position", "").lower() in _ABSOLUTE_POSITION_VALUES:
            for key in list(props.keys()):
                if key in _ABSOLUTE_POSITION_PROPS:
                    del props[key]
                    changed = True
            absolute_fixed_count += 1

        # ── display: flex / grid ─────────────────────────────────────────────
        if props.get("display", "").lower() in _FLEX_GRID_VALUES:
            props["display"] = "block"
            for key in list(props.keys()):
                if key in _FLEX_GRID_PROPS:
                    del props[key]
            flex_grid_count += 1
            changed = True

        # ── column-count / columns ───────────────────────────────────────────
        if "column-count" in props or "columns" in props:
            for key in list(props.keys()):
                if key in _MULTICOLUMN_PROPS:
                    del props[key]
                    changed = True
            multicolumn_count += 1

        if not changed:
            return tag_html

        if not props:
            # Remove the entire style="..." attribute.
            new_tag = tag_html[: style_m.start()] + tag_html[style_m.end() :]
            new_tag = re.sub(r"\s{2,}", " ", new_tag)
            new_tag = new_tag.replace("< ", "<")
        else:
            new_style = serialize_inline_style(props)
            new_tag = (
                tag_html[: style_m.start("value")]
                + new_style
                + tag_html[style_m.end("value") :]
            )
        return new_tag

    new_content = _STYLED_BLOCK_TAG_RE.sub(_degrade_tag, html_content)
    return new_content, absolute_fixed_count, flex_grid_count, multicolumn_count


# ─── Clearfix wrapping for floated blocks ─────────────────────────────────────

# Tags whose float behaviour is self-contained and should NOT receive a
# clearfix wrapper.  <img> floats are handled by the image pipeline.
# Table cells float in a completely different stacking context.
_FLOAT_SKIP_TAGS = frozenset({"img", "td", "th", "tr", "table", "caption"})

# Matches an opening tag for a wrappable block element that carries a float.
_FLOAT_OPEN_TAG_RE = re.compile(
    r"<(?P<tag>div|section|article|aside|figure)\b(?P<attrs>[^>]*)>",
    flags=re.IGNORECASE | re.DOTALL,
)

# Matches an opening tag for ANY element that has ``migration-`` in its class.
# Used to pre-compute skip zones so we never wrap children of pipeline-authored
# containers.
_MIGRATION_CONTAINER_OPEN_RE = re.compile(
    r"<(?P<tag>\w+)\b[^>]*class\s*=\s*(?P<q>[\"'])[^\"']*migration-[^\"']*(?P=q)[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)

# Clearfix container injected around floated blocks.  `overflow: hidden` is the
# simplest cross-browser clearfix that does not rely on pseudo-elements (which
# Canvas's rich-text renderer does not support).
_CLEARFIX_OPEN = '<div class="migration-clearfix" style="overflow: hidden;">'
_CLEARFIX_CLOSE = "</div>"


def _has_float(attrs: str) -> bool:
    """Return True if *attrs* (the attribute portion of an opening tag) declares
    ``float: left`` or ``float: right`` in its inline style."""
    style_m = _STYLE_ATTR_RE.search(f"<x {attrs}>")
    if style_m is None:
        return False
    props = parse_inline_style(style_m.group("value"))
    return props.get("float", "").lower() in _FLOAT_VALUES


def _find_closing_tag(html: str, start: int, tag: str) -> int:
    """Return the index immediately after the closing ``</tag>`` that matches
    the opening tag at *start*.

    Handles nested tags of the same type by counting open/close depth.  If no
    matching close tag is found, returns ``-1``.

    Args:
        html: Full HTML document string.
        start: Index of the ``<`` of the opening tag to match.
        tag: Tag name (case-insensitive, e.g. ``"div"``).

    Returns:
        Index just past the ``>`` of the matching closing tag, or ``-1``.
    """
    open_pat = re.compile(rf"<{tag}\b", re.IGNORECASE)
    close_pat = re.compile(rf"</{tag}\s*>", re.IGNORECASE)

    depth = 0
    pos = start
    while pos < len(html):
        open_m = open_pat.search(html, pos)
        close_m = close_pat.search(html, pos)

        if close_m is None:
            return -1
        if open_m is not None and open_m.start() < close_m.start():
            depth += 1
            pos = open_m.end()
        else:
            depth -= 1
            if depth == 0:
                return close_m.end()
            pos = close_m.end()
    return -1


def _compute_migration_skip_zones(html_content: str) -> list[tuple[int, int]]:
    """Return a list of ``(start, end)`` ranges covering every pipeline-authored
    element (class contains ``migration-``) in *html_content*.

    Floating elements whose start position falls inside one of these ranges will
    be skipped by :func:`wrap_floated_blocks` to prevent double-wrapping.
    """
    zones: list[tuple[int, int]] = []
    for m in _MIGRATION_CONTAINER_OPEN_RE.finditer(html_content):
        tag = m.group("tag").lower()
        close_end = _find_closing_tag(html_content, m.start(), tag)
        if close_end != -1:
            zones.append((m.start(), close_end))
    return zones


def wrap_floated_blocks(html_content: str) -> tuple[str, int]:
    """Wrap block-level floated elements in a clearfix container.

    A plain ``float: left/right`` on a ``<div>``, ``<section>``, etc. causes
    the *parent* element's height to collapse in Canvas's renderer — subsequent
    sibling content wraps around or behind the floated element.  Wrapping the
    floated block in ``<div style="overflow: hidden">`` creates a new block
    formatting context that contains the float, preventing the collapse.

    **Rules:**

    * Only ``<div>``, ``<section>``, ``<article>``, ``<aside>``, and
      ``<figure>`` elements are candidates.
    * The element must declare ``float: left`` or ``float: right`` in its
      inline ``style`` attribute.
    * Pipeline-authored elements (class containing ``migration-``) are skipped.
    * Elements already inside a ``migration-`` container are skipped to prevent
      double-wrapping (e.g. a second pass over already-converted output).
    * ``<img>`` and table elements are excluded — their float handling is done
      elsewhere in the pipeline.

    Args:
        html_content: Full HTML document string (post-degradation).

    Returns:
        ``(new_html, wrapped_count)`` where *wrapped_count* is the number of
        elements that received a clearfix wrapper.
    """
    # Pre-compute the full extents of all pipeline-authored containers so we
    # can skip floated children that are already inside them.
    skip_zones: list[tuple[int, int]] = _compute_migration_skip_zones(html_content)

    wrapped_count = 0
    result_parts: list[str] = []
    pos = 0

    for m in _FLOAT_OPEN_TAG_RE.finditer(html_content):
        tag = m.group("tag").lower()
        attrs = m.group("attrs")

        # Only act on elements that float.
        if not _has_float(attrs):
            continue

        # Skip pipeline-authored elements (class contains migration-).
        if _PIPELINE_CLASS_RE.search(m.group(0)):
            continue

        # Skip elements that are already inside a migration-authored container
        # or a previously-wrapped clearfix region.
        if any(start <= m.start() < end for start, end in skip_zones):
            continue

        close_end = _find_closing_tag(html_content, m.start(), tag)
        if close_end == -1:
            continue

        # Emit everything up to this element unchanged.
        result_parts.append(html_content[pos : m.start()])
        # Emit the clearfix wrapper around the entire element.
        result_parts.append(_CLEARFIX_OPEN)
        result_parts.append(html_content[m.start() : close_end])
        result_parts.append(_CLEARFIX_CLOSE)
        # Mark this wrapped region so nested floated descendants are not
        # wrapped a second time on subsequent iterations (finditer continues
        # over the original content, so nested matches are still visited).
        skip_zones.append((m.start(), close_end))
        pos = close_end
        wrapped_count += 1

    result_parts.append(html_content[pos:])
    return "".join(result_parts), wrapped_count

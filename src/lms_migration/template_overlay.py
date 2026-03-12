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


def _load_template_assets_by_basename(template_package: Path) -> tuple[dict[str, list[str]], dict[str, int]]:
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

    collisions = {key: len(values) for key, values in by_basename.items() if len(values) > 1}
    return dict(by_basename), collisions


def _load_alias_map(alias_map_json_path: Path | None) -> tuple[dict[str, tuple[str, ...]], str]:
    if alias_map_json_path is None:
        return {}, ""
    if not alias_map_json_path.exists():
        raise ValueError(f"Template alias map JSON does not exist: {alias_map_json_path}")

    payload = json.loads(alias_map_json_path.read_text(encoding="utf-8"))
    raw_mapping = payload.get("aliases") if isinstance(payload, dict) and isinstance(payload.get("aliases"), dict) else payload
    if not isinstance(raw_mapping, dict):
        raise ValueError("Template alias map JSON must be an object or include an object at key 'aliases'.")

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


def _load_icon_label_map(template_package: Path) -> dict[str, str]:
    icon_labels: dict[str, str] = {}
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
                for match in re.finditer(
                    r'<img\b[^>]*src\s*=\s*(["\'])[^"\']*/icons/(?P<basename>[^/"\']+\.(?:png|jpg|jpeg|svg|gif|webp))\1[^>]*>\s*(?P<label>[^<]+)',
                    html_text,
                    flags=re.IGNORECASE,
                ):
                    basename = _normalize_basename(match.group("basename"))
                    canonical = _canonical_icon_label(match.group("label"))
                    if basename and canonical and basename not in icon_labels:
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


@dataclass
class TemplateOverlayContext:
    template_package: Path
    alias_map_source: str
    alias_map: dict[str, tuple[str, ...]]
    assets_by_basename: dict[str, list[str]]
    file_name_collisions: dict[str, int]
    icon_label_by_basename: dict[str, str]
    apply_visual_standards: bool


def build_template_overlay_context(config: TemplateOverlayConfig) -> TemplateOverlayContext:
    assets_by_basename, collisions = _load_template_assets_by_basename(config.template_package)
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
    )


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
        if not _is_brightspace_template_url(original_url):
            return match.group(0)

        source_basename = _extract_template_basename(original_url)
        if not source_basename:
            unresolved += 1
            unresolved_refs.append(original_url)
            unresolved_basenames.append("")
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
                has_large_width = bool(
                    re.search(r'\bwidth\s*=\s*(["\'])\s*(?:[2-9]\d{2,}|\d{4,})\s*\1', updated_tag, flags=re.IGNORECASE)
                    or re.search(r'width\s*:\s*(?:[2-9]\d{2,}|\d{4,})px', updated_tag, flags=re.IGNORECASE)
                )
                if not has_large_width:
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
                        if key in {"width", "height", "max-width"}:
                            continue
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        style_tokens.append(cleaned)

                style_tokens.extend(["max-width: 100%", "height: auto"])
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
                    updated_tag = append_attribute(updated_tag, f'style="{rebuilt_style}"')

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
                        if lowered.startswith(("max-width:", "width:", "height:", "display:")):
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
                    updated_tag = append_attribute(updated_tag, f'style="{rebuilt_style}"')

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
                if key == "height" and lowered.startswith("height:auto") and not has_float_style:
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
            if canonical_icon_label:
                if alt_match is not None:
                    if alt_text != canonical_icon_label:
                        updated_tag = (
                            updated_tag[: alt_match.start("alt")]
                            + canonical_icon_label
                            + updated_tag[alt_match.end("alt") :]
                        )
                        icon_alt_updates += 1
                else:
                    updated_tag = append_attribute(updated_tag, f'alt="{canonical_icon_label}"')
                    icon_alt_updates += 1
            elif not alt_text:
                title_match = re.search(
                    r'(?<=\s)title\s*=\s*(["\'])(?P<title>[^"\']*)\1',
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                title_text = title_match.group("title").strip() if title_match is not None else ""
                fallback_alt = (
                    title_text
                    if title_text
                    else posixpath.splitext(src_basename)[0].replace("-", " ").replace("_", " ").strip().title()
                )
                if fallback_alt:
                    if alt_match is not None:
                        updated_tag = (
                            updated_tag[: alt_match.start("alt")]
                            + fallback_alt
                            + updated_tag[alt_match.end("alt") :]
                        )
                    else:
                        updated_tag = append_attribute(updated_tag, f'alt="{fallback_alt}"')
                    icon_alt_updates += 1

            if canonical_icon_label:
                title_match = re.search(
                    r'(?<=\s)title\s*=\s*(["\'])(?P<title>[^"\']*)\1',
                    updated_tag,
                    flags=re.IGNORECASE,
                )
                title_text = title_match.group("title").strip() if title_match is not None else ""
                if title_match is not None:
                    if title_text != canonical_icon_label:
                        updated_tag = (
                            updated_tag[: title_match.start("title")]
                            + canonical_icon_label
                            + updated_tag[title_match.end("title") :]
                        )
                        icon_title_updates += 1
                else:
                    updated_tag = append_attribute(updated_tag, f'title="{canonical_icon_label}"')
                    icon_title_updates += 1

            return updated_tag

        updated = _IMG_TAG_PATTERN.sub(normalize_template_icon_tag, updated)

        def with_template_heading_color(attrs: str) -> str:
            working_attrs = attrs or ""
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

        def normalize_icon_only_heading(match: re.Match[str]) -> str:
            nonlocal icon_label_heading_updates
            full_heading = match.group(0)
            img_tag = match.group("img")
            src_match = re.search(
                r'\bsrc\s*=\s*(["\'])(?P<src>[^"\']+)\1',
                img_tag,
                flags=re.IGNORECASE,
            )
            if src_match is None:
                return full_heading
            src_value = src_match.group("src").strip()
            parsed = urlparse(src_value)
            src_basename = _normalize_basename(unquote(parsed.path))
            if not src_basename:
                return full_heading
            label = context.icon_label_by_basename.get(src_basename, "")
            if not label:
                return full_heading
            heading_attrs = with_template_heading_color(match.group("attrs"))
            replacement = f'<h{match.group("level")}{heading_attrs}>{img_tag} {html.escape(label)}</h{match.group("level")}>'
            if replacement != full_heading:
                icon_label_heading_updates += 1
            return replacement

        updated = re.sub(
            r"<h(?P<level>[1-6])(?P<attrs>[^>]*)>\s*(?P<img><img\b[^>]*>)\s*</h(?P=level)>",
            normalize_icon_only_heading,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def merge_icon_block_with_heading(match: re.Match[str]) -> str:
            nonlocal icon_block_heading_merges
            img_tag = match.group("img")
            heading_body = match.group("body").strip()
            if not heading_body:
                return match.group(0)
            heading_attrs = with_template_heading_color(match.group("hattrs"))
            replacement = (
                f'<h{match.group("level")}{heading_attrs}>{img_tag} {heading_body}</h{match.group("level")}>'
            )
            icon_block_heading_merges += 1
            return replacement

        updated = re.sub(
            r"<(?P<wrapper>p|div)\b[^>]*>\s*(?P<img><img\b[^>]*src\s*=\s*[\"'][^\"']*templateassets/[^\"']+[\"'][^>]*>)\s*</(?P=wrapper)>\s*"
            r"<h(?P<level>[1-6])(?P<hattrs>[^>]*)>(?P<body>.*?)</h(?P=level)>",
            merge_icon_block_with_heading,
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )

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
                description="Normalized mapped template banner image sizing for Canvas rendering",
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
    if responsive_image_updates:
        applied_changes.append(
            AppliedChange(
                category="template_overlay",
                description="Normalized large fixed-width images to responsive max-width styling for Canvas",
                count=responsive_image_updates,
            )
        )

    manual_issues: list[ManualReviewIssue] = []
    if unresolved:
        evidence = unresolved_refs[0][:120] if unresolved_refs else "unresolved template references"
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
        "unresolved_basenames": sorted({name for name in unresolved_basenames if name})[:50],
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

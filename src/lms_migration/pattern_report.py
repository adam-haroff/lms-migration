from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from .reference_docs import parse_best_practice_policy
from .template_standards import extract_template_standards, resolve_default_template_package
from .training_corpus import collect_course_artifacts


_HTML_EXTENSIONS = {".html", ".htm"}
_TITLE_RE = re.compile(r"<title\b[^>]*>(?P<body>.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TEMPLATE_ASSET_RE = re.compile(
    r"(?:TemplateAssets|templateassets|template-images/(?:icons|sample-images))/"
    r"(?P<basename>[^\"'#?/\s>]+)",
    flags=re.IGNORECASE,
)
_STANDARD_IMAGE_ASSET_RE = re.compile(
    r"(?:standardImages|standardimages)/(?P<basename>[^\"'#?/\s>]+)",
    flags=re.IGNORECASE,
)
_SHARED_TEMPLATE_RE = re.compile(r"shared/brightspace_html_template/", flags=re.IGNORECASE)
_BOOTSTRAP_ACCORDION_RE = re.compile(
    r"class\s*=\s*[\"'][^\"']*(?:card-header|collapse)[^\"']*[\"']",
    flags=re.IGNORECASE,
)
_DETAILS_RE = re.compile(r"<details\b", flags=re.IGNORECASE)
_FLOAT_IMAGE_RE = re.compile(
    r"<img\b[^>]*(?:\balign\s*=\s*[\"'](?:left|right)[\"']|float\s*:\s*(?:left|right))[^>]*>",
    flags=re.IGNORECASE,
)
_SAFE_BLOCK_IMAGE_RE = re.compile(
    r"<img\b[^>]*style\s*=\s*[\"'][^\"']*display\s*:\s*block[^\"']*(?:margin\s*:\s*[^\"']*auto|margin-left\s*:\s*auto|margin-right\s*:\s*auto)[^\"']*[\"'][^>]*>",
    flags=re.IGNORECASE,
)
_HEADING_WITH_TEMPLATE_ICON_RE = re.compile(
    r"<h[1-6]\b[^>]*>.*?(?:TemplateAssets|templateassets|template-images/icons)/[^<]+.*?[A-Za-z].*?</h[1-6]>",
    flags=re.IGNORECASE | re.DOTALL,
)
_STANDARD_DIVIDER_RE = re.compile(
    r"<hr\b[^>]*style\s*=\s*[\"'][^\"']*height\s*:\s*2px[^\"']*background-color\s*:\s*#ac1a2f[^\"']*[\"'][^>]*>",
    flags=re.IGNORECASE,
)
_HR_RE = re.compile(r"<hr\b[^>]*>", flags=re.IGNORECASE)
_TOPIC_RE = re.compile(r"^topic\s+\d+", flags=re.IGNORECASE)
_MODULE_RE = re.compile(r"^module\s+\d+", flags=re.IGNORECASE)
_SECTION_TITLES = ("Overview", "Learning Activities", "Review")
_PROTECTED_SHELL_TITLES = ("Start Here", "Instructor Module (Do Not Publish)")
_HEADING_BLOCK_RE = re.compile(r"<h(?P<level>[1-6])\b[^>]*>(?P<body>.*?)</h(?P=level)>", flags=re.IGNORECASE | re.DOTALL)
_TEMPLATE_ICON_RE = re.compile(
    r"(?:TemplateAssets|templateassets|template-images/icons)/(?P<basename>[^\"'#?/\s>]+)",
    flags=re.IGNORECASE,
)
_STANDARD_ICON_BLOCK_RE = re.compile(
    r"<img\b[^>]*?(?:standardImages|standardimages)/(?P<basename>[^\"'#?/\s>]+)[^>]*>\s*(?:</?(?:span|strong|em|b|p|div)\b[^>]*>\s*){0,6}<h[1-6]\b[^>]*>(?P<title>.*?)</h[1-6]>",
    flags=re.IGNORECASE | re.DOTALL,
)
_PIPE_TITLE_RE = re.compile(r".+\s\|\s.+")
_COLON_TITLE_RE = re.compile(r"^[A-Za-z0-9][^|]{0,120}:\s+\S")
_GENERIC_ICON_LABEL_KEYS = {
    "view",
    "read",
    "quiz",
    "practice",
    "additional resources",
    "information",
    "important",
    "reminder",
    "introduction",
    "module objectives",
    "module checklist",
}

_CONSENSUS_TRANSFORMS = (
    {
        "key": "topic_to_module",
        "label": 'Top-level "Topic N" names become "Module N" names',
        "applicable": lambda before, after: before.get("top_level_topic_titles", 0) > 0,
        "matched": lambda before, after: after.get("top_level_module_titles", 0) > 0,
        "sample": lambda row: (
            f"{row['course_code']}: {row['before']['top_level_topic_titles']} topic titles -> "
            f"{row['after']['top_level_module_titles']} module titles"
        ),
    },
    {
        "key": "intro_objectives_to_checklist",
        "label": '"Introduction and Objectives" pages become "Introduction and Checklist"',
        "applicable": lambda before, after: before.get("intro_objectives_titles", 0) > 0,
        "matched": lambda before, after: after.get("intro_checklist_titles", 0) > 0,
        "sample": lambda row: (
            f"{row['course_code']}: {row['before']['intro_objectives_titles']} intro/objectives -> "
            f"{row['after']['intro_checklist_titles']} intro/checklist"
        ),
    },
    {
        "key": "shared_template_to_local_assets",
        "label": "Brightspace shared template references become local TemplateAssets references",
        "applicable": lambda before, after: before.get("shared_template_refs", 0) > 0,
        "matched": lambda before, after: after.get("shared_template_refs", 0) == 0
        and after.get("template_asset_refs", 0) > 0,
        "sample": lambda row: (
            f"{row['course_code']}: shared refs {row['before']['shared_template_refs']} -> "
            f"local TemplateAssets {row['after']['template_asset_refs']}"
        ),
    },
    {
        "key": "accordion_modernization",
        "label": "Legacy accordions are replaced by accessible details blocks or flattened sections",
        "applicable": lambda before, after: before.get("bootstrap_accordion_blocks", 0) > 0,
        "matched": lambda before, after: after.get("bootstrap_accordion_blocks", 0) == 0,
        "sample": lambda row: (
            f"{row['course_code']}: accordion blocks {row['before']['bootstrap_accordion_blocks']} -> "
            f"{row['after']['bootstrap_accordion_blocks']} (details {row['after']['details_blocks']})"
        ),
    },
    {
        "key": "image_cleanup",
        "label": "Float-heavy content images are reduced in favor of safer block-aligned images",
        "applicable": lambda before, after: before.get("floated_images", 0) > 0,
        "matched": lambda before, after: after.get("floated_images", 0) == 0
        or after.get("floated_images", 0) < before.get("floated_images", 0),
        "sample": lambda row: (
            f"{row['course_code']}: floated images {row['before']['floated_images']} -> "
            f"{row['after']['floated_images']} (safe block images {row['after']['safe_block_images']})"
        ),
    },
    {
        "key": "divider_normalization",
        "label": "Divider styling is normalized to the standard Canvas-safe red rule",
        "applicable": lambda before, after: after.get("divider_count", 0) > 0,
        "matched": lambda before, after: after.get("nonstandard_dividers", 0) == 0,
        "sample": lambda row: (
            f"{row['course_code']}: dividers {row['after']['divider_count']} with "
            f"{row['after']['standard_dividers']} standard"
        ),
    },
)


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(value: str) -> str:
    lowered = _strip_html(value).lower().replace("&", "and")
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _load_html_files(zip_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _HTML_EXTENSIONS:
                continue
            files[name] = zf.read(name).decode("utf-8", errors="ignore")
    return files


def _extract_title(value: str, fallback: str) -> str:
    match = _TITLE_RE.search(value)
    if match is None:
        return fallback
    return _strip_html(match.group("body")) or fallback


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _extract_item_title(item: ET.Element) -> str:
    for child in list(item):
        if _local_name(child.tag) == "title":
            return (child.text or "").strip()
    return ""


def _load_manifest_structure(zip_path: Path) -> dict[str, list[str]]:
    with ZipFile(zip_path, "r") as zf:
        if "imsmanifest.xml" not in zf.namelist():
            return {"top_level_titles": [], "section_titles": [], "all_titles": []}
        root = ET.fromstring(zf.read("imsmanifest.xml"))

    top_level_titles: list[str] = []
    section_titles: list[str] = []
    all_titles: list[str] = []
    children_by_top_level: dict[str, list[str]] = {}

    for organization in [node for node in root.iter() if _local_name(node.tag) == "organization"]:
        top_level_items = [child for child in list(organization) if _local_name(child.tag) == "item"]
        if (
            len(top_level_items) == 1
            and not _extract_item_title(top_level_items[0])
            and any(_local_name(child.tag) == "item" for child in list(top_level_items[0]))
        ):
            top_level_items = [child for child in list(top_level_items[0]) if _local_name(child.tag) == "item"]
        for item in top_level_items:
            title = _extract_item_title(item)
            if title:
                top_level_titles.append(title)
                all_titles.append(title)
                children_by_top_level.setdefault(title, [])
            for child_item in [child for child in list(item) if _local_name(child.tag) == "item"]:
                child_title = _extract_item_title(child_item)
                if child_title:
                    section_titles.append(child_title)
                    all_titles.append(child_title)
                    if title:
                        children_by_top_level.setdefault(title, []).append(child_title)
                for nested in child_item.iter():
                    if nested is child_item or _local_name(nested.tag) != "item":
                        continue
                    nested_title = _extract_item_title(nested)
                    if nested_title:
                        all_titles.append(nested_title)
    return {
        "top_level_titles": top_level_titles,
        "section_titles": section_titles,
        "all_titles": all_titles,
        "children_by_top_level": children_by_top_level,
    }


def _count_template_assets(html_text: str) -> Counter[str]:
    return Counter(match.group("basename").lower() for match in _TEMPLATE_ASSET_RE.finditer(html_text))


def _package_features(zip_path: Path) -> dict:
    html_files = _load_html_files(zip_path)
    manifest = _load_manifest_structure(zip_path)
    html_titles = [
        _extract_title(content, Path(path).stem.replace("_", " ").strip())
        for path, content in html_files.items()
    ]
    all_html = "\n".join(html_files.values())
    template_assets = _count_template_assets(all_html)
    top_titles = manifest.get("top_level_titles", [])
    section_titles = manifest.get("section_titles", [])
    all_titles = manifest.get("all_titles", []) + html_titles
    normalized_titles = Counter(_normalize_key(title) for title in all_titles if _normalize_key(title))
    pipe_titles = [title for title in all_titles if _PIPE_TITLE_RE.search(title)]
    colon_titles = [title for title in all_titles if _COLON_TITLE_RE.search(title)]

    divider_count = len(_HR_RE.findall(all_html))
    standard_dividers = len(_STANDARD_DIVIDER_RE.findall(all_html))

    return {
        "zip_path": str(zip_path),
        "html_file_count": len(html_files),
        "template_asset_refs": sum(template_assets.values()),
        "template_asset_basenames": dict(template_assets.most_common(20)),
        "shared_template_refs": len(_SHARED_TEMPLATE_RE.findall(all_html)),
        "bootstrap_accordion_blocks": len(_BOOTSTRAP_ACCORDION_RE.findall(all_html)),
        "details_blocks": len(_DETAILS_RE.findall(all_html)),
        "floated_images": len(_FLOAT_IMAGE_RE.findall(all_html)),
        "safe_block_images": len(_SAFE_BLOCK_IMAGE_RE.findall(all_html)),
        "template_icon_headings": len(_HEADING_WITH_TEMPLATE_ICON_RE.findall(all_html)),
        "divider_count": divider_count,
        "standard_dividers": standard_dividers,
        "nonstandard_dividers": max(divider_count - standard_dividers, 0),
        "top_level_topic_titles": sum(1 for title in top_titles if _TOPIC_RE.match(title)),
        "top_level_module_titles": sum(1 for title in top_titles if _MODULE_RE.match(title)),
        "intro_objectives_titles": normalized_titles.get("introduction and objectives", 0),
        "intro_checklist_titles": normalized_titles.get("introduction and checklist", 0),
        "learning_activities_titles": normalized_titles.get("learning activities", 0),
        "pipe_delimited_titles": len(pipe_titles),
        "colon_delimited_titles": len(colon_titles),
        "pipe_title_examples": pipe_titles[:12],
        "colon_title_examples": colon_titles[:12],
        "top_level_titles": top_titles[:20],
        "section_titles": section_titles[:30],
        "children_by_top_level": manifest.get("children_by_top_level", {}),
        "section_title_counts": {
            label: sum(1 for title in section_titles if _normalize_key(title) == _normalize_key(label))
            for label in _SECTION_TITLES
        },
        "sample_titles": html_titles[:12],
    }


def _collect_training_pairs(
    training_courses_root: Path,
    *,
    examples_courses_root: Path | None = None,
) -> list[dict]:
    rows: list[dict] = []
    roots = [training_courses_root]
    if examples_courses_root is not None:
        roots.append(examples_courses_root)
    for artifacts in collect_course_artifacts(roots):
        before_zip = artifacts.get("before_zip")
        after_zip = artifacts.get("after_zip")
        if before_zip is None or after_zip is None or not before_zip.exists() or not after_zip.exists():
            continue
        rows.append(
            {
                "course_code": artifacts["course_code"],
                "training_source_root": str(artifacts.get("root", "")),
                "snapshot_course_id": artifacts.get("snapshot_identity", {}).get("course_id"),
                "snapshot_course_name": artifacts.get("snapshot_identity", {}).get("course_name", ""),
                "snapshot_course_code": artifacts.get("snapshot_identity", {}).get("course_code", ""),
                "before_zip": str(before_zip),
                "after_zip": str(after_zip),
                "before": _package_features(before_zip),
                "after": _package_features(after_zip),
            }
        )
    return rows


def _template_features(template_package: Path | None) -> dict:
    if template_package is None or not template_package.exists():
        return {"page_titles": [], "manifest_titles": [], "asset_basenames": {}}
    features = _package_features(template_package)
    return {
        "page_titles": features.get("sample_titles", []),
        "manifest_titles": features.get("section_titles", []) + features.get("top_level_titles", []),
        "asset_basenames": features.get("template_asset_basenames", {}),
    }


def _template_elements_kept(training_rows: list[dict], template_package: Path | None) -> dict:
    template = _template_features(template_package)
    template_titles = {
        title
        for title in template.get("manifest_titles", [])
        if title and len(title) <= 80
    }
    title_presence: Counter[str] = Counter()
    asset_presence: Counter[str] = Counter()

    for row in training_rows:
        after_titles = {
            _normalize_key(title)
            for title in (row.get("after", {}).get("top_level_titles", []) + row.get("after", {}).get("section_titles", []))
            if _normalize_key(title)
        }
        for template_title in template_titles:
            normalized = _normalize_key(template_title)
            if normalized and normalized in after_titles:
                title_presence[template_title] += 1

        after_assets = set((row.get("after", {}).get("template_asset_basenames", {}) or {}).keys())
        for basename in after_assets:
            if basename in template.get("asset_basenames", {}):
                asset_presence[basename] += 1

    return {
        "page_or_module_titles": [
            {"title": title, "course_count": count}
            for title, count in title_presence.most_common()
            if count >= 2
        ][:15],
        "template_assets": [
            {"basename": basename, "course_count": count}
            for basename, count in asset_presence.most_common()
            if count >= 2
        ][:20],
    }


def _protected_shell_playbook(training_rows: list[dict], template_package: Path | None) -> list[dict]:
    if template_package is None or not template_package.exists():
        return []

    template_manifest = _load_manifest_structure(template_package)
    template_children_by_top = template_manifest.get("children_by_top_level", {})
    rows: list[dict] = []

    for shell_title in _PROTECTED_SHELL_TITLES:
        template_children = template_children_by_top.get(shell_title, [])
        template_child_keys = {_normalize_key(title): title for title in template_children if _normalize_key(title)}
        presence_count = 0
        intact_child_counts: Counter[str] = Counter()
        custom_child_counts: Counter[str] = Counter()

        for row in training_rows:
            after_children_map = row.get("after", {}).get("children_by_top_level", {}) or {}
            after_children = after_children_map.get(shell_title, [])
            if not after_children:
                continue
            presence_count += 1
            for child_title in after_children:
                normalized = _normalize_key(child_title)
                if not normalized:
                    continue
                if normalized in template_child_keys:
                    intact_child_counts[template_child_keys[normalized]] += 1
                else:
                    custom_child_counts[child_title] += 1

        rows.append(
            {
                "title": shell_title,
                "training_course_presence": presence_count,
                "template_child_count": len(template_children),
                "recommended_protected_shell": presence_count >= max(2, len(training_rows) // 2),
                "common_intact_children": [
                    {"title": child_title, "course_count": count}
                    for child_title, count in intact_child_counts.most_common()
                    if count >= 2
                ],
                "common_custom_children": [
                    {"title": child_title, "course_count": count}
                    for child_title, count in custom_child_counts.most_common(12)
                    if count >= 2
                ],
            }
        )
    return rows


def _before_icon_signatures(html_files: dict[str, str]) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for content in html_files.values():
        for match in _STANDARD_ICON_BLOCK_RE.finditer(content):
            basename = Path(match.group("basename")).name.lower()
            if basename.startswith("rule_") or basename == "rule_brown_gradient.png":
                continue
            title = _strip_html(match.group("title") or "")
            normalized = _normalize_key(title)
            if not basename or not normalized or normalized in signatures:
                continue
            if normalized in {"horizontal rule", "logo", "banner"}:
                continue
            signatures[normalized] = basename
    return signatures


def _after_icon_signatures(html_files: dict[str, str]) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for content in html_files.values():
        headings = list(_HEADING_BLOCK_RE.finditer(content))
        for index, match in enumerate(headings):
            body = match.group("body") or ""
            icon_match = _TEMPLATE_ICON_RE.search(body)
            if icon_match is None:
                continue
            basename = Path(icon_match.group("basename")).name.lower()
            current_text = _strip_html(body)
            signature_text = current_text
            if _normalize_key(current_text) in _GENERIC_ICON_LABEL_KEYS and index + 1 < len(headings):
                next_text = _strip_html(headings[index + 1].group("body") or "")
                if next_text:
                    signature_text = next_text
            normalized = _normalize_key(signature_text)
            if not basename or not normalized or normalized in signatures:
                continue
            if normalized in {"horizontal rule", "logo", "banner"}:
                continue
            signatures[normalized] = basename
    return signatures


def _icon_mapping_playbook(training_rows: list[dict]) -> list[dict]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    sample_signatures: dict[tuple[str, str], str] = {}

    for row in training_rows:
        before_zip = Path(str(row.get("before_zip", "")))
        after_zip = Path(str(row.get("after_zip", "")))
        if not before_zip.exists() or not after_zip.exists():
            continue
        before_signatures = _before_icon_signatures(_load_html_files(before_zip))
        after_signatures = _after_icon_signatures(_load_html_files(after_zip))
        shared_keys = set(before_signatures) & set(after_signatures)
        for key in shared_keys:
            pair = (before_signatures[key], after_signatures[key])
            pair_counts[pair] += 1
            sample_signatures.setdefault(pair, key)

    rows: list[dict] = []
    for (before_icon, after_icon), count in pair_counts.most_common():
        if count < 2:
            continue
        rows.append(
            {
                "before_icon": before_icon,
                "after_icon": after_icon,
                "course_count": count,
                "sample_heading": sample_signatures.get((before_icon, after_icon), ""),
            }
        )
    return rows


def _consensus_transforms(training_rows: list[dict], current_row: dict | None) -> list[dict]:
    rows: list[dict] = []
    for definition in _CONSENSUS_TRANSFORMS:
        applicable = [
            row
            for row in training_rows
            if definition["applicable"](row.get("before", {}), row.get("after", {}))
        ]
        matched = [
            row
            for row in applicable
            if definition["matched"](row.get("before", {}), row.get("after", {}))
        ]
        if not applicable:
            continue

        current_state = "not_applicable"
        if current_row is not None and definition["applicable"](current_row.get("before", {}), current_row.get("after", {})):
            current_state = (
                "matched"
                if definition["matched"](current_row.get("before", {}), current_row.get("after", {}))
                else "missing"
            )

        rows.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "applicable_courses": len(applicable),
                "matching_courses": len(matched),
                "match_rate": round(len(matched) / max(len(applicable), 1), 3),
                "sample_matches": [definition["sample"](row) for row in matched[:5]],
                "current_course_state": current_state,
            }
        )
    return rows


def _is_consensus_transform(row: dict) -> bool:
    applicable_courses = int(row.get("applicable_courses", 0) or 0)
    matching_courses = int(row.get("matching_courses", 0) or 0)
    match_rate = float(row.get("match_rate", 0) or 0)
    return applicable_courses >= 2 and matching_courses >= 2 and match_rate >= 0.5


def _current_alignment(current_row: dict | None, kept: dict, transforms: list[dict]) -> dict:
    if current_row is None:
        return {}

    after_titles = {
        _normalize_key(title)
        for title in (current_row.get("after", {}).get("top_level_titles", []) + current_row.get("after", {}).get("section_titles", []))
        if _normalize_key(title)
    }
    after_assets = set((current_row.get("after", {}).get("template_asset_basenames", {}) or {}).keys())

    kept_titles = [
        row["title"]
        for row in kept.get("page_or_module_titles", [])
        if _normalize_key(row.get("title", "")) in after_titles
    ]
    kept_assets = [
        row["basename"]
        for row in kept.get("template_assets", [])
        if row.get("basename", "") in after_assets
    ]

    return {
        "matching_consensus_transforms": sum(
            1 for row in transforms if row.get("current_course_state") == "matched"
        ),
        "missing_consensus_transforms": [
            row.get("label", "")
            for row in transforms
            if row.get("current_course_state") == "missing"
        ],
        "kept_template_titles": kept_titles[:10],
        "kept_template_assets": kept_assets[:10],
    }


def _title_delimiter_playbook(
    training_rows: list[dict],
    current_row: dict | None,
    best_practices_docx: Path | None,
) -> dict:
    policy = parse_best_practice_policy(best_practices_docx)
    after_pipe_courses = sum(
        1 for row in training_rows if int(row.get("after", {}).get("pipe_delimited_titles", 0) or 0) > 0
    )
    after_colon_courses = sum(
        1 for row in training_rows if int(row.get("after", {}).get("colon_delimited_titles", 0) or 0) > 0
    )
    before_pipe_courses = sum(
        1 for row in training_rows if int(row.get("before", {}).get("pipe_delimited_titles", 0) or 0) > 0
    )

    current_before = current_row.get("before", {}) if isinstance(current_row, dict) else {}
    current_after = current_row.get("after", {}) if isinstance(current_row, dict) else {}
    return {
        "best_practice_policy": policy,
        "training_before_pipe_courses": before_pipe_courses,
        "training_after_pipe_courses": after_pipe_courses,
        "training_after_colon_courses": after_colon_courses,
        "historical_corpus_is_mixed": bool(after_pipe_courses and after_colon_courses),
        "current_before_pipe_titles": int(current_before.get("pipe_delimited_titles", 0) or 0),
        "current_after_pipe_titles": int(current_after.get("pipe_delimited_titles", 0) or 0),
        "current_after_colon_titles": int(current_after.get("colon_delimited_titles", 0) or 0),
        "current_after_pipe_examples": list(current_after.get("pipe_title_examples", []) or [])[:8],
        "current_after_colon_examples": list(current_after.get("colon_title_examples", []) or [])[:8],
    }


def _default_output_json(current_converted_zip: Path) -> Path:
    stem = current_converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return current_converted_zip.with_name(f"{stem}.pattern-report.json")


def _default_output_markdown(output_json: Path) -> Path:
    return output_json.with_suffix(".md")


def build_pattern_report(
    *,
    current_course_code: str,
    current_source_zip: Path | None,
    current_converted_zip: Path | None,
    training_courses_root: Path,
    examples_courses_root: Path | None = Path("resources/examples"),
    template_package: Path | None = None,
    best_practices_docx: Path | None = None,
    output_json_path: Path | None = None,
    output_markdown_path: Path | None = None,
) -> tuple[Path, Path]:
    template_standards = extract_template_standards(template_package)
    training_rows = _collect_training_pairs(
        training_courses_root,
        examples_courses_root=examples_courses_root,
    )
    source_counter = Counter(Path(str(row.get("training_source_root", ""))).name for row in training_rows)

    current_row: dict | None = None
    if current_source_zip is not None and current_converted_zip is not None:
        current_row = {
            "course_code": current_course_code,
            "before_zip": str(current_source_zip),
            "after_zip": str(current_converted_zip),
            "before": _package_features(current_source_zip),
            "after": _package_features(current_converted_zip),
        }

    kept = _template_elements_kept(training_rows, template_package)
    protected_shells = _protected_shell_playbook(training_rows, template_package)
    icon_mappings = _icon_mapping_playbook(training_rows)
    observed_transforms = _consensus_transforms(training_rows, current_row)
    transforms = [row for row in observed_transforms if _is_consensus_transform(row)]
    current_alignment = _current_alignment(current_row, kept, transforms)
    title_delimiter_policy = _title_delimiter_playbook(training_rows, current_row, best_practices_docx)

    report = {
        "inputs": {
            "current_course_code": current_course_code,
            "current_source_zip": str(current_source_zip) if current_source_zip is not None else "",
            "current_converted_zip": str(current_converted_zip) if current_converted_zip is not None else "",
            "training_courses_root": str(training_courses_root),
            "examples_courses_root": str(examples_courses_root) if examples_courses_root is not None else "",
            "template_package": str(template_package) if template_package is not None else "",
            "best_practices_docx": str(best_practices_docx) if best_practices_docx is not None else "",
        },
        "summary": {
            "training_course_pairs": len(training_rows),
            "training_course_pairs_by_source": dict(sorted(source_counter.items())),
            "consensus_transforms": len(transforms),
            "kept_template_titles": len(kept.get("page_or_module_titles", [])),
            "kept_template_assets": len(kept.get("template_assets", [])),
            "protected_shell_modules": sum(1 for row in protected_shells if row.get("recommended_protected_shell")),
            "icon_mapping_rules": len(icon_mappings),
            "current_matching_transforms": current_alignment.get("matching_consensus_transforms", 0),
            "current_missing_transforms": len(current_alignment.get("missing_consensus_transforms", [])),
            "template_warnings": len(template_standards.get("warnings", [])),
        },
        "title_delimiter_policy": title_delimiter_policy,
        "template_standards": template_standards,
        "kept_template_elements": kept,
        "protected_shell_playbook": protected_shells,
        "icon_mapping_playbook": icon_mappings,
        "observed_transforms": observed_transforms,
        "consistent_transforms": transforms,
        "current_alignment": current_alignment,
        "training_courses": training_rows,
    }

    if current_converted_zip is not None:
        output_json = output_json_path or _default_output_json(current_converted_zip)
    else:
        output_json = output_json_path or (training_courses_root.parent / "pattern-report.json")
    output_markdown = output_markdown_path or _default_output_markdown(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Pattern Report",
        "",
        "## Summary",
        "",
        f"- Training course pairs: {report['summary']['training_course_pairs']}",
        f"- Training course pairs by source: {report['summary']['training_course_pairs_by_source']}",
        f"- Consensus transforms: {report['summary']['consensus_transforms']}",
        f"- Template titles kept across corpus: {report['summary']['kept_template_titles']}",
        f"- Template assets kept across corpus: {report['summary']['kept_template_assets']}",
        f"- Protected shell modules: {report['summary']['protected_shell_modules']}",
        f"- Icon mapping rules: {report['summary']['icon_mapping_rules']}",
        f"- Template warnings: {report['summary']['template_warnings']}",
    ]
    if current_row is not None:
        lines.extend(
            [
                f"- Current course matching transforms: {report['summary']['current_matching_transforms']}",
                f"- Current course missing transforms: {report['summary']['current_missing_transforms']}",
            ]
        )

    lines.extend(["", "## Consistent Transforms", ""])
    for row in transforms:
        lines.append(
            f"- {row['label']}: {row['matching_courses']}/{row['applicable_courses']} "
            f"({row['match_rate']:.0%}) | current={row['current_course_state']}"
        )
    lines.extend(["", "## Template Elements Kept", ""])
    for row in kept.get("page_or_module_titles", [])[:10]:
        lines.append(f"- Title kept: {row['title']} ({row['course_count']} courses)")
    for row in kept.get("template_assets", [])[:10]:
        lines.append(f"- Asset kept: {row['basename']} ({row['course_count']} courses)")
    lines.extend(["", "## Protected Template Shells", ""])
    for row in protected_shells:
        status = "protect" if row.get("recommended_protected_shell") else "review"
        lines.append(
            f"- {row['title']}: {status} | present in {row['training_course_presence']} courses"
        )
        for child in row.get("common_intact_children", [])[:6]:
            lines.append(f"- {row['title']} intact child: {child['title']} ({child['course_count']})")
        for child in row.get("common_custom_children", [])[:4]:
            lines.append(f"- {row['title']} common custom child: {child['title']} ({child['course_count']})")
    template_shell = template_standards.get("shell", {})
    template_visual = template_standards.get("visual", {})
    template_content = template_standards.get("content", {})
    lines.extend(["", "## Current Template Standards", ""])
    lines.append(f"- Template package: {template_standards.get('template_package', '') or 'none'}")
    if template_visual:
        lines.append(
            f"- Heading icon width: {template_visual.get('heading_icon_width_px', 'unknown')}px"
        )
        lines.append(
            f"- Decorative icon alt expected: {template_visual.get('decorative_icon_alt_expected', False)}"
        )
        lines.append(
            f"- Sinclair red hex: {template_visual.get('sinclair_red_hex', '') or 'unknown'}"
        )
        lines.append(
            f"- Primary page-heading rule: {template_visual.get('primary_page_heading_rule', '') or 'unknown'}"
        )
        lines.append(
            f"- Thick red divider rule: {template_visual.get('thick_red_divider_rule', '') or 'unknown'}"
        )
        lines.append(
            f"- Internal separator rule: {template_visual.get('internal_separator_rule', '') or 'unknown'}"
        )
        lines.append(
            f"- Home page section rule: {template_visual.get('home_page_section_rule', '') or 'unknown'}"
        )
        lines.append(
            f"- Home page header background: {template_visual.get('home_page_header_background', '') or 'unknown'}"
        )
        lines.append(
            f"- Icon and title treated as separate elements: {template_visual.get('icon_text_can_be_copied_separately', False)}"
        )
    if template_shell:
        for item in template_shell.get("start_here_items", [])[:8]:
            lines.append(f"- Start Here shell item: {item}")
        for item in template_shell.get("instructor_module_items", [])[:8]:
            lines.append(f"- Instructor Module shell item: {item}")
        for item in template_shell.get("lesson_module_items", [])[:10]:
            lines.append(f"- Lesson module shell item: {item}")
        for item in template_shell.get("course_conclusion_items", [])[:8]:
            lines.append(f"- Course Conclusion shell item: {item}")
        lines.append(
            f"- Course Credentials present in current template shell: {template_shell.get('course_credentials_in_shell', False)}"
        )
    if template_content:
        lines.append(
            "- Learning Activities video guidance present: "
            f"{template_content.get('view_requires_video_title_transcript_timestamp_citation', False)}"
        )
        lines.append(
            f"- Paste without formatting guidance present: {template_content.get('paste_without_formatting_guidance_present', False)}"
        )
        lines.append(
            f"- Accordion guidance present: {template_content.get('accordion_guidance_present', False)}"
        )
        lines.append(
            f"- Start Here notes that Syllabus Quiz replaces Course Overview Survey: {template_content.get('start_here_replaces_course_overview_survey', False)}"
        )
        lines.append(
            f"- Home page AI notice present: {template_content.get('home_page_ai_notice_present', False)}"
        )
        lines.append(
            f"- Home page AI notice conditional: {template_content.get('home_page_ai_notice_conditional', False)}"
        )
        lines.append(
            f"- Home page links expected: {template_content.get('home_page_links', [])}"
        )
        lines.append(
            f"- Syllabus table of contents present: {template_content.get('syllabus_has_table_of_contents', False)}"
        )
        lines.append(
            f"- Syllabus return-to-TOC links present: {template_content.get('syllabus_has_return_to_toc_links', False)}"
        )
        lines.append(
            f"- Syllabus AI disclosure section present: {template_content.get('syllabus_has_ai_disclosure_section', False)}"
        )
        lines.append(
            f"- Syllabus variants present: {template_content.get('syllabus_variants_present', [])}"
        )
        lines.append(
            f"- About the Instructor belonging language present: {template_content.get('about_instructor_belonging_language_present', False)}"
        )
        lines.append(
            f"- Policies and Support page present: {template_content.get('policies_support_page_present', False)}"
        )
    for warning in template_standards.get("warnings", []):
        lines.append(f"- Template warning: {warning}")
    lines.extend(["", "## Icon Mapping Playbook", ""])
    for row in icon_mappings[:12]:
        lines.append(
            f"- {row['before_icon']} -> {row['after_icon']} ({row['course_count']} courses)"
            + (f" | sample: {row['sample_heading']}" if row.get("sample_heading") else "")
        )
    lines.extend(["", "## Title Delimiter Policy", ""])
    policy = title_delimiter_policy.get("best_practice_policy", {})
    lines.append(f"- Best-practice doc pipes deprecated: {policy.get('pipes_deprecated', False)}")
    lines.append(
        f"- Best-practice doc accessible accordion allowed: {policy.get('accessible_accordion_allowed', False)}"
    )
    if policy.get("title_policy_excerpt"):
        lines.append(f"- Title policy excerpt: {policy['title_policy_excerpt']}")
    if policy.get("accordion_policy_excerpt"):
        lines.append(f"- Accordion policy excerpt: {policy['accordion_policy_excerpt']}")
    lines.append(
        f"- Training courses with pipe-delimited Canvas titles after migration: "
        f"{title_delimiter_policy.get('training_after_pipe_courses', 0)}/{len(training_rows)}"
    )
    lines.append(
        f"- Training courses with colon-delimited Canvas titles after migration: "
        f"{title_delimiter_policy.get('training_after_colon_courses', 0)}/{len(training_rows)}"
    )
    lines.append(
        f"- Historical corpus is mixed on pipe vs colon: {title_delimiter_policy.get('historical_corpus_is_mixed', False)}"
    )
    if current_row is not None:
        lines.append(
            f"- Current course pipe titles before -> after: "
            f"{title_delimiter_policy.get('current_before_pipe_titles', 0)} -> "
            f"{title_delimiter_policy.get('current_after_pipe_titles', 0)}"
        )
        lines.append(
            f"- Current course colon titles after conversion: "
            f"{title_delimiter_policy.get('current_after_colon_titles', 0)}"
        )
        for sample in title_delimiter_policy.get("current_after_pipe_examples", [])[:5]:
            lines.append(f"- Current remaining pipe title: {sample}")
    if current_alignment.get("missing_consensus_transforms"):
        lines.extend(["", "## Current Gaps", ""])
        for item in current_alignment["missing_consensus_transforms"]:
            lines.append(f"- {item}")

    output_markdown.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_json, output_markdown


def main() -> None:
    default_template_package = resolve_default_template_package(Path.cwd())
    parser = argparse.ArgumentParser(
        prog="lms-pattern-report",
        description="Analyze deterministic before/after patterns across the local training corpus.",
    )
    parser.add_argument("--current-course-code", default="current-course", help="Course code for the current package")
    parser.add_argument("--current-source-zip", type=Path, default=None, help="Optional current source D2L zip")
    parser.add_argument("--current-converted-zip", type=Path, default=None, help="Optional current converted zip")
    parser.add_argument(
        "--training-courses-root",
        type=Path,
        default=Path("resources/training-corpus-v2/courses"),
        help="Training corpus root with before/after course folders",
    )
    parser.add_argument(
        "--examples-courses-root",
        type=Path,
        default=Path("resources/examples"),
        help="Optional second corpus root for example courses with Canvas gold exports.",
    )
    parser.add_argument(
        "--template-package",
        type=Path,
        default=default_template_package,
        help="Optional template package used for kept-element comparisons",
    )
    parser.add_argument(
        "--best-practices-docx",
        type=Path,
        default=Path("resources/helpers/Canvas Blueprints - Best Practices-20260316.docx"),
        help="Optional best-practices docx used to compare historical title/accordion trends against current policy.",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Optional pattern report JSON path")
    parser.add_argument("--output-markdown", type=Path, default=None, help="Optional pattern report Markdown path")
    args = parser.parse_args()

    output_json, output_markdown = build_pattern_report(
        current_course_code=args.current_course_code,
        current_source_zip=args.current_source_zip,
        current_converted_zip=args.current_converted_zip,
        training_courses_root=args.training_courses_root,
        examples_courses_root=args.examples_courses_root,
        template_package=args.template_package,
        best_practices_docx=args.best_practices_docx,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(f"Pattern report JSON: {output_json}")
    print(f"Pattern report Markdown: {output_markdown}")


if __name__ == "__main__":
    main()

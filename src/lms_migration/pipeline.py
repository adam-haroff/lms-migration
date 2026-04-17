from __future__ import annotations

import csv
import html
import json
import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from .html_tools import (
    AppliedChange,
    BestPracticeEnforcerPolicy,
    CanvasSanitizerPolicy,
    ManualReviewIssue,
    TemplateCheckPolicy,
    apply_best_practice_enforcer,
    apply_banner_rule,
    apply_canvas_sanitizer,
    apply_link_rewrites,
    apply_replacements,
    check_accessibility_heuristics,
    check_template_heuristics,
    detect_layout_breaking_issues,
    detect_lti_embed_issues,
    detect_iframe_issues,
    detect_d2l_media_library_embeds,
    detect_email_submission_issues,
    detect_manual_review_issues,
    inject_accent_divider,
    neutralize_legacy_d2l_hrefs_in_markup,
    repair_missing_local_references,
)
from .fix_checklist import _map_manual_review_group
from .policy_profiles import PolicyProfile, get_policy_profile
from .quiz_audit import (
    _RISK_TYPES as _QUIZ_RISK_TYPES,
    _parse_quiz_xml as _parse_quiz_xml_file,
    audit_quizzes as _audit_quizzes,
    write_json_report as _write_quiz_json_report,
    write_markdown_report as _write_quiz_markdown_report,
)
from .rules import load_rules
from .template_merger import run_template_merge
from .template_overlay import (
    TemplateOverlayConfig,
    apply_template_overlay,
    build_template_overlay_context,
    build_template_overlay_report,
    ensure_canonical_closing_divider,
    materialize_template_assets,
)


_XML_NAMESPACES_TO_REGISTER = {
    "": "http://www.imsglobal.org/xsd/imscp_v1p1",
    "imsmd": "http://www.imsglobal.org/xsd/imsmd_rootv1p2p1",
    "d2l_2p0": "http://desire2learn.com/xsd/d2lcp_v2p0",
    "lom": "http://ltsc.ieee.org/xsd/LOM",
    "dc": "http://purl.org/dc/elements/1.1/",
}
for _prefix, _uri in _XML_NAMESPACES_TO_REGISTER.items():
    ET.register_namespace(_prefix, _uri)


@dataclass
class FileResult:
    path: str
    changed: bool
    applied_changes: list[AppliedChange]
    manual_issues: list[ManualReviewIssue]
    a11y_issues: list[ManualReviewIssue]


@dataclass
class MigrationOutput:
    output_zip: Path
    report_json: Path
    report_markdown: Path
    manual_review_csv: Path
    preflight_checklist: Path
    policy_profile_id: str
    template_overlay_report_json: Path | None = None
    quiz_audit_json: Path | None = None
    quiz_audit_md: Path | None = None
    kickoff_summary_json: Path | None = None
    kickoff_summary_md: Path | None = None


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _zip_directory(source_dir: Path, output_zip: Path) -> None:
    with ZipFile(output_zip, "w", compression=ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(source_dir)
                zf.write(file_path, arcname)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _normalize_compare_text(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"\s+", " ", lowered).strip()
    lowered = lowered.replace("&", "and")
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _remove_leading_duplicate_title_block(
    fragment: str, title_text: str
) -> tuple[str, int]:
    normalized_title = _normalize_compare_text(title_text)
    if not normalized_title:
        return fragment, 0

    def _tokenize(value: str) -> list[str]:
        return [
            token
            for token in value.split(" ")
            if token and token not in {"the", "a", "an"}
        ]

    def _is_duplicate(block_text: str, expected_title: str) -> bool:
        normalized_block = _normalize_compare_text(block_text)
        normalized_expected = _normalize_compare_text(expected_title)
        if not normalized_block or not normalized_expected:
            return False
        if normalized_block == normalized_expected:
            return True
        if _tokenize(normalized_block) == _tokenize(normalized_expected):
            return True
        ratio = SequenceMatcher(a=normalized_block, b=normalized_expected).ratio()
        return ratio >= 0.92

    block_pattern = re.compile(
        r"<(?P<tag>h[1-6]|p)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    inspected_candidates = 0
    for match in block_pattern.finditer(fragment):
        body_text = re.sub(r"<[^>]+>", " ", match.group("body"))
        unescaped_body = html.unescape(body_text)
        normalized_body = _normalize_compare_text(unescaped_body)
        if not normalized_body:
            continue
        if normalized_body == "printerfriendlyversion":
            continue
        if _is_duplicate(unescaped_body, title_text):
            updated = fragment[: match.start()] + fragment[match.end() :]
            return updated, 1
        inspected_candidates += 1
        if inspected_candidates >= 3:
            break
    return fragment, 0


_TOPIC_MODULE_TITLE_RE = re.compile(
    r"^\s*Topic\s*0*(?P<number>\d+)\s*(?:\||-|:)\s*(?P<label>.+?)\s*$",
    flags=re.IGNORECASE,
)


def _extract_item_title(item: ET.Element) -> tuple[ET.Element | None, str]:
    for child in list(item):
        if _local_name(child.tag) == "title":
            return child, (child.text or "").strip()
    return None, ""


def _resource_href_map(manifest_root: ET.Element) -> dict[str, str]:
    hrefs: dict[str, str] = {}
    for element in manifest_root.iter():
        if _local_name(element.tag) != "resource":
            continue
        identifier = (element.attrib.get("identifier") or "").strip()
        href = (element.attrib.get("href") or "").strip()
        if identifier and href:
            hrefs[identifier] = href
    return hrefs


def _attr_local(element: ET.Element, name: str) -> str:
    direct = (element.attrib.get(name) or "").strip()
    if direct:
        return direct
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value.strip()
    return ""


def _build_manifest_item_inventory(zf: ZipFile) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    manifest_files = [
        n for n in zf.namelist() if re.match(r"imsmanifest\.xml$", n.rsplit("/", 1)[-1])
    ]
    for fname in manifest_files:
        try:
            raw = zf.read(fname).decode("utf-8", errors="replace")
        except Exception:
            continue
        raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        href_map = _resource_href_map(root)
        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue
            _title_el, title = _extract_item_title(item)
            identifierref = (item.attrib.get("identifierref") or "").strip()
            items.append(
                {
                    "title": title,
                    "identifierref": identifierref,
                    "href": href_map.get(identifierref, ""),
                    "resource_code": _attr_local(item, "resource_code"),
                    "condition_set": (item.attrib.get("condition_set") or "").strip(),
                    "isvisible": (item.attrib.get("isvisible") or "").strip(),
                    "resource_type_key": (item.attrib.get("resource_type_key") or "").strip(),
                    "description": _attr_local(item, "description"),
                }
            )
    return items


def _append_html_fragment(existing_html: str, fragment: str) -> str:
    separator = '\n<hr style="border: 0; height: 2px; background-color: #ac1a2f; width: 100%; margin: 16px 0;">\n'
    payload = f"{separator}{fragment.strip()}\n"
    if re.search(r"</body>", existing_html, flags=re.IGNORECASE):
        return re.sub(
            r"</body>", payload + "</body>", existing_html, count=1, flags=re.IGNORECASE
        )
    return existing_html.rstrip() + "\n" + payload


def _normalize_fragment_text(value: str) -> str:
    as_text = re.sub(r"<[^>]+>", " ", value)
    return _normalize_compare_text(html.unescape(as_text))


def _normalize_module_checklist_wording(fragment: str) -> str:
    updated = fragment
    updated = re.sub(
        r"(?i)\btopic\s*0*(\d+)\s*(\||-|:)\s*",
        lambda m: f"Module {int(m.group(1))}: ",
        updated,
    )
    updated = re.sub(r"(?i)\bthis topic\b", "this module", updated)
    updated = re.sub(r"(?i)\bthe topic\b", "the module", updated)
    updated = re.sub(
        r"(?i)after completing the learning activities for this module,\s*you will be able to\s*:",
        "By the end of this module, you will be able to:",
        updated,
    )
    updated = re.sub(
        r"(?i)to meet the learning objectives for this module,\s*you will complete(?: these| the following)? activities\s*:",
        "In order to successfully complete this module, please do the following:",
        updated,
    )
    updated = re.sub(
        r"(?i)\bintroduction and objectives\b",
        "Introduction and Checklist",
        updated,
    )
    return updated


def _is_intro_objectives_page(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lower()
    return bool(
        re.search(r"topic\s*\d+/introduction and objectives\.html$", normalized)
    )


_MODULE_NUMBERED_TITLE_RE = re.compile(
    r"^\s*Module\s*0*(?P<number>\d+)\s*:\s*(?P<label>.+?)\s*$", flags=re.IGNORECASE
)
_START_HERE_TITLE = "Start Here"
_INSTRUCTOR_MODULE_TITLE = "Instructor Module (Do Not Publish)"
_START_HERE_CARRYOVER_TITLE = "D2L Start Here Carryover (Manual Placement)"
_INSTRUCTOR_CARRYOVER_TITLE = "D2L Instructor Carryover (Manual Placement)"
_TITLE_DELIMITER_PREFIX_HINTS = (
    "assignment",
    "discussion",
    "lesson",
    "quiz",
    "exam",
    "survey",
    "syllabus",
    "course overview",
    "course requirements",
    "grading information",
    "guidelines",
    "preparing your course",
    "review",
    "announcement",
    "instructor guide",
    "start here",
)


def _module_number_from_title(value: str) -> int | None:
    match = _MODULE_NUMBERED_TITLE_RE.match(value or "")
    if match is None:
        return None
    try:
        return int(match.group("number"))
    except ValueError:
        return None


def _build_unique_item_identifier(existing_identifiers: set[str], seed: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", seed.lower()).strip("_")
    if not normalized:
        normalized = "auto_item"
    candidate = f"auto_{normalized}"
    index = 1
    while candidate in existing_identifiers:
        index += 1
        candidate = f"auto_{normalized}_{index}"
    existing_identifiers.add(candidate)
    return candidate


def _new_subheader_item(
    parent_item: ET.Element,
    *,
    title_text: str,
    existing_identifiers: set[str],
    module_number: int,
) -> ET.Element:
    namespace_prefix = ""
    if parent_item.tag.startswith("{"):
        namespace_prefix = parent_item.tag.split("}", 1)[0] + "}"
    item_tag = f"{namespace_prefix}item" if namespace_prefix else "item"
    title_tag = f"{namespace_prefix}title" if namespace_prefix else "title"
    item = ET.Element(item_tag)
    item.set(
        "identifier",
        _build_unique_item_identifier(
            existing_identifiers,
            f"module_{module_number}_{title_text}_header",
        ),
    )
    title_element = ET.SubElement(item, title_tag)
    title_element.text = title_text
    return item


def _classify_numbered_module_child(title_text: str) -> str:
    normalized = _normalize_compare_text(title_text)
    if not normalized:
        return "activity"
    if normalized == "overview":
        return "subheader_overview"
    if normalized == "activities":
        return "subheader_activities"
    if normalized == "review":
        return "subheader_review"
    if (
        "introduction and objectives" in normalized
        or "introduction and checklist" in normalized
    ):
        return "overview_intro"
    if normalized.startswith("introduction"):
        return "overview_intro"
    if "learning activities" in normalized:
        return "overview_learning"
    if "lesson" in normalized:
        return "overview_lesson"
    if normalized == "module review" or normalized.endswith(" review"):
        return "review_page"
    if any(
        token in normalized
        for token in ("discussion", "assignment", "quiz", "test", "survey", "exam")
    ):
        return "activity"
    return "activity"


def _strip_title_prefix(value: str, pattern: str) -> str:
    stripped = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
    return stripped.strip("-:| ")


def _normalize_canvas_title_delimiters(value: str) -> tuple[str, bool]:
    normalized_value = re.sub(r"\s+", " ", value or "").strip()
    if "|" not in normalized_value:
        return normalized_value, False

    parts = [part.strip(" -:|") for part in normalized_value.split("|")]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return normalized_value, False

    prefix = parts[0]
    label_number_match = re.match(
        r"^(?P<label>[A-Za-z ]+):\s*(?P<number>\d+)\s*$", prefix
    )
    if label_number_match is not None:
        prefix = f"{label_number_match.group('label').strip()} {label_number_match.group('number').strip()}".strip()
    normalized_prefix = _normalize_compare_text(prefix)
    if not any(
        normalized_prefix.startswith(hint) for hint in _TITLE_DELIMITER_PREFIX_HINTS
    ):
        return normalized_value, False

    suffix = " ".join(parts[1:]).strip()
    if not suffix:
        rebuilt = prefix
    else:
        rebuilt = f"{prefix}: {suffix}"
    return rebuilt, rebuilt != normalized_value


def _normalize_child_item_title_delimiters(parent_item: ET.Element) -> int:
    renamed = 0
    for child in parent_item.iter():
        if child is parent_item or _local_name(child.tag) != "item":
            continue
        child_title_element, child_title = _extract_item_title(child)
        if child_title_element is None or not child_title:
            continue
        normalized_title, changed = _normalize_canvas_title_delimiters(child_title)
        if not changed:
            continue
        child_title_element.text = normalized_title
        renamed += 1
    return renamed


def _normalize_manifest_item_title_delimiters(root: ET.Element) -> int:
    renamed = 0
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        title_element, title_text = _extract_item_title(item)
        if title_element is None or not title_text:
            continue
        normalized_title, changed = _normalize_canvas_title_delimiters(title_text)
        if not changed:
            continue
        title_element.text = normalized_title
        renamed += 1
    return renamed


def _template_module_child_title(
    *, module_number: int, original_title: str, kind: str
) -> str:
    if kind == "overview_intro":
        return f"Module {module_number}: Introduction and Checklist"
    if kind == "overview_learning":
        return f"Module {module_number}: Learning Activities"
    if kind == "overview_lesson":
        tail = _strip_title_prefix(
            original_title, r"^\s*lesson(?:\s*page)?\s*(?:\||:|-)?\s*"
        )
        if not tail:
            tail = "[Title]"
        return f"Module {module_number}: Lesson {tail}"
    if kind == "review_page":
        return f"Module {module_number}: Review"

    normalized = _normalize_compare_text(original_title)
    if "discussion" in normalized:
        tail = _strip_title_prefix(original_title, r"^\s*discussion\s*(?:\||:|-)?\s*")
        if not tail:
            tail = "[Title Here]"
        return f"Module {module_number}: Discussion {tail}"
    if "assignment" in normalized:
        tail = _strip_title_prefix(original_title, r"^\s*assignment\s*(?:\||:|-)?\s*")
        if not tail:
            tail = "[Title Here]"
        return f"Module {module_number}: Assignment {tail}"
    if any(token in normalized for token in ("quiz", "test", "survey", "exam")):
        tail = _strip_title_prefix(
            original_title, r"^\s*(?:quiz|test|survey|exam)\s*(?:\||:|-)?\s*"
        )
        if not tail:
            tail = "[Title Here]"
        return f"Quiz: {tail}"
    return original_title


def _apply_template_module_structure_to_organization(
    organization: ET.Element,
    *,
    existing_identifiers: set[str],
    preserve_template_shell_modules: bool = False,
) -> tuple[int, int, int, int, int]:
    top_level_renames = 0
    child_title_renames = 0
    delimiter_title_renames = 0
    reordered_modules = 0
    inserted_subheaders = 0

    for top_item in [
        child for child in list(organization) if _local_name(child.tag) == "item"
    ]:
        top_title_element, top_title = _extract_item_title(top_item)
        if top_title_element is None:
            continue

        top_normalized = _normalize_compare_text(top_title)
        desired_top_title = ""
        if (
            top_normalized.startswith("faculty resources")
            or "hidden from students" in top_normalized
            or top_normalized == _normalize_compare_text(_INSTRUCTOR_MODULE_TITLE)
        ):
            desired_top_title = (
                _INSTRUCTOR_CARRYOVER_TITLE
                if preserve_template_shell_modules
                else _INSTRUCTOR_MODULE_TITLE
            )
        elif top_normalized.startswith(
            "course overview"
        ) or top_normalized == _normalize_compare_text(_START_HERE_TITLE):
            desired_top_title = (
                _START_HERE_CARRYOVER_TITLE
                if preserve_template_shell_modules
                else _START_HERE_TITLE
            )
        if desired_top_title and top_title != desired_top_title:
            top_title_element.text = desired_top_title
            top_title = desired_top_title
            top_level_renames += 1

        delimiter_title_renames += _normalize_child_item_title_delimiters(top_item)
        module_number = _module_number_from_title(top_title)
        if module_number is None:
            top_child_items = [
                child for child in list(top_item) if _local_name(child.tag) == "item"
            ]
            top_child_titles = [
                _extract_item_title(child)[1] for child in top_child_items
            ]
            normalized_top_title = _normalize_compare_text(top_title)

            if preserve_template_shell_modules and normalized_top_title in {
                _normalize_compare_text(_START_HERE_CARRYOVER_TITLE),
                _normalize_compare_text(_INSTRUCTOR_CARRYOVER_TITLE),
            }:
                continue

            if normalized_top_title == _normalize_compare_text(_START_HERE_TITLE):
                has_support_subheader = any(
                    _normalize_compare_text(child_title)
                    == _normalize_compare_text("Canvas Support Resources")
                    for child_title in top_child_titles
                )
                if not has_support_subheader and top_child_items:
                    target_index: int | None = None
                    for index, child_title in enumerate(top_child_titles):
                        normalized_child = _normalize_compare_text(child_title)
                        if "resource" in normalized_child and (
                            "student" in normalized_child
                            or "support" in normalized_child
                            or "canvas" in normalized_child
                        ):
                            target_index = index
                            break
                    if target_index is not None and target_index > 0:
                        anchor = top_child_items[target_index]
                        insert_position = list(top_item).index(anchor)
                        top_item.insert(
                            insert_position,
                            _new_subheader_item(
                                top_item,
                                title_text="Canvas Support Resources",
                                existing_identifiers=existing_identifiers,
                                module_number=0,
                            ),
                        )
                        reordered_modules += 1
                        inserted_subheaders += 1

            if normalized_top_title == _normalize_compare_text(
                _INSTRUCTOR_MODULE_TITLE
            ):
                has_about_subheader = any(
                    _normalize_compare_text(child_title)
                    == _normalize_compare_text("About This Template")
                    for child_title in top_child_titles
                )
                if not has_about_subheader and top_child_items:
                    first_anchor = top_child_items[0]
                    insert_position = list(top_item).index(first_anchor)
                    top_item.insert(
                        insert_position,
                        _new_subheader_item(
                            top_item,
                            title_text="About This Template",
                            existing_identifiers=existing_identifiers,
                            module_number=0,
                        ),
                    )
                    reordered_modules += 1
                    inserted_subheaders += 1
            continue

        child_items = [
            child for child in list(top_item) if _local_name(child.tag) == "item"
        ]
        if not child_items:
            continue

        old_signature = [
            ((child.attrib.get("identifierref") or ""), _extract_item_title(child)[1])
            for child in child_items
        ]

        overview_items: list[ET.Element] = []
        activity_items: list[ET.Element] = []
        review_items: list[ET.Element] = []

        for child in child_items:
            child_title_element, child_title = _extract_item_title(child)
            kind = _classify_numbered_module_child(child_title)
            if kind.startswith("subheader_"):
                continue

            if child_title_element is not None:
                desired_child_title = _template_module_child_title(
                    module_number=module_number,
                    original_title=child_title,
                    kind=kind,
                )
                if desired_child_title and desired_child_title != child_title:
                    child_title_element.text = desired_child_title
                    child_title = desired_child_title
                    child_title_renames += 1
                    kind = _classify_numbered_module_child(child_title)

            if kind in {"overview_intro", "overview_learning", "overview_lesson"}:
                overview_items.append(child)
            elif kind == "review_page":
                review_items.append(child)
            else:
                activity_items.append(child)

        rebuilt_children: list[ET.Element] = []
        module_headers = 0
        if overview_items:
            rebuilt_children.append(
                _new_subheader_item(
                    top_item,
                    title_text="Overview",
                    existing_identifiers=existing_identifiers,
                    module_number=module_number,
                )
            )
            rebuilt_children.extend(overview_items)
            module_headers += 1
        if activity_items:
            rebuilt_children.append(
                _new_subheader_item(
                    top_item,
                    title_text="Activities",
                    existing_identifiers=existing_identifiers,
                    module_number=module_number,
                )
            )
            rebuilt_children.extend(activity_items)
            module_headers += 1
        if review_items:
            rebuilt_children.append(
                _new_subheader_item(
                    top_item,
                    title_text="Review",
                    existing_identifiers=existing_identifiers,
                    module_number=module_number,
                )
            )
            rebuilt_children.extend(review_items)
            module_headers += 1

        if not rebuilt_children:
            continue

        new_signature = [
            ((child.attrib.get("identifierref") or ""), _extract_item_title(child)[1])
            for child in rebuilt_children
        ]
        if old_signature != new_signature:
            for child in child_items:
                top_item.remove(child)
            for child in rebuilt_children:
                top_item.append(child)
            reordered_modules += 1
            inserted_subheaders += module_headers

    return (
        top_level_renames,
        child_title_renames,
        delimiter_title_renames,
        reordered_modules,
        inserted_subheaders,
    )


def _to_serializable_issue(issue: ManualReviewIssue) -> dict[str, str]:
    return {
        "reason": issue.reason,
        "evidence": issue.evidence,
        "category": issue.category,
    }


def _build_issue_summary(file_results: list[FileResult]) -> dict:
    manual_files = 0
    a11y_files = 0
    manual_reason_counts: Counter[str] = Counter()
    a11y_reason_counts: Counter[str] = Counter()
    file_issue_rows: list[dict] = []

    for result in file_results:
        manual_count = len(result.manual_issues)
        a11y_count = len(result.a11y_issues)
        if manual_count:
            manual_files += 1
            manual_reason_counts.update(
                issue.reason for issue in result.manual_issues if issue.reason
            )
        if a11y_count:
            a11y_files += 1
            a11y_reason_counts.update(
                issue.reason for issue in result.a11y_issues if issue.reason
            )
        if manual_count or a11y_count:
            file_issue_rows.append(
                {
                    "path": result.path,
                    "manual_review_issues": manual_count,
                    "accessibility_issues": a11y_count,
                    "total_issues": manual_count + a11y_count,
                }
            )

    file_issue_rows.sort(
        key=lambda row: (
            -int(row["total_issues"]),
            -int(row["manual_review_issues"]),
            str(row["path"]),
        )
    )

    return {
        "files_with_manual_review_issues": manual_files,
        "files_with_accessibility_issues": a11y_files,
        "top_manual_review_reasons": [
            {"reason": reason, "count": count}
            for reason, count in manual_reason_counts.most_common(15)
        ],
        "top_accessibility_reasons": [
            {"reason": reason, "count": count}
            for reason, count in a11y_reason_counts.most_common(15)
        ],
        "top_issue_files": file_issue_rows[:20],
    }


def _upsert_file_result(
    file_results: list[FileResult],
    replacement: FileResult,
    *,
    merge_applied_changes: bool = False,
) -> None:
    for index in range(len(file_results) - 1, -1, -1):
        if file_results[index].path != replacement.path:
            continue
        if merge_applied_changes:
            existing = file_results[index]
            replacement = FileResult(
                path=replacement.path,
                changed=existing.changed or replacement.changed,
                applied_changes=existing.applied_changes + replacement.applied_changes,
                manual_issues=replacement.manual_issues,
                a11y_issues=replacement.a11y_issues,
            )
        file_results[index] = replacement
        return

    file_results.append(replacement)


def _build_report(
    input_zip: Path,
    output_zip: Path,
    rules_path: Path,
    policy_profile: PolicyProfile,
    manifest_found: bool,
    file_results: list[FileResult],
    best_practice_enforcer_enabled: bool = False,
    math_handling: str = "preserve-semantic",
    reference_alignment: dict | None = None,
    template_overlay: dict | None = None,
    file_layout: dict | None = None,
) -> dict:
    total_files = len(file_results)
    changed_files = sum(1 for result in file_results if result.changed)

    change_count = sum(
        change.count for result in file_results for change in result.applied_changes
    )
    manual_issue_count = sum(len(result.manual_issues) for result in file_results)
    a11y_issue_count = sum(len(result.a11y_issues) for result in file_results)
    issue_summary = _build_issue_summary(file_results)

    report = {
        "input_zip": str(input_zip),
        "output_zip": str(output_zip),
        "rules": str(rules_path),
        "policy_profile": {
            "id": policy_profile.profile_id,
            "description": policy_profile.description,
        },
        "best_practice_enforcer_enabled": bool(best_practice_enforcer_enabled),
        "math_handling": math_handling,
        "manifest_found": manifest_found,
        "summary": {
            "html_files_scanned": total_files,
            "html_files_changed": changed_files,
            "total_automated_changes": change_count,
            "manual_review_issues": manual_issue_count,
            "accessibility_issues": a11y_issue_count,
        },
        "issue_summary": issue_summary,
        "files": [
            {
                "path": result.path,
                "changed": result.changed,
                "applied_changes": [
                    {
                        "category": change.category,
                        "description": change.description,
                        "count": change.count,
                    }
                    for change in result.applied_changes
                ],
                "manual_review_issues": [
                    _to_serializable_issue(issue) for issue in result.manual_issues
                ],
                "accessibility_issues": [
                    _to_serializable_issue(issue) for issue in result.a11y_issues
                ],
            }
            for result in file_results
        ],
    }

    if reference_alignment is not None:
        report["reference_alignment"] = reference_alignment
    if template_overlay is not None:
        report["template_overlay"] = template_overlay
    if file_layout is not None:
        report["file_layout"] = file_layout

    return report


def _load_reference_alignment(reference_audit_json: Path | None) -> dict | None:
    if reference_audit_json is None:
        return None
    if not reference_audit_json.exists():
        return None

    try:
        raw = json.loads(reference_audit_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    instruction = raw.get("instruction_comparison", {})
    best_practices = raw.get("best_practices_coverage", {})
    template = raw.get("template_analysis", {})

    critical_gaps = instruction.get("critical_gaps", [])
    if not isinstance(critical_gaps, list):
        critical_gaps = []

    coverage_rows = best_practices.get("coverage_rows", [])
    if not isinstance(coverage_rows, list):
        coverage_rows = []

    action_needed = [
        row
        for row in coverage_rows
        if isinstance(row, dict) and bool(row.get("action_needed"))
    ]

    placeholders = template.get("placeholder_patterns_detected", [])
    if not isinstance(placeholders, list):
        placeholders = []

    return {
        "source_file": str(reference_audit_json),
        "critical_gap_count": len(critical_gaps),
        "critical_gap_ids": [
            str(gap.get("id", "")).strip()
            for gap in critical_gaps
            if isinstance(gap, dict)
        ],
        "best_practice_action_needed_count": len(action_needed),
        "best_practice_action_needed_ids": [
            str(row.get("id", "")).strip()
            for row in action_needed
            if isinstance(row, dict)
        ],
        "template_placeholder_patterns_detected": [str(item) for item in placeholders],
        "module_checklist_required_closer_present": bool(
            template.get("module_checklist_required_closer_present", True)
        ),
    }


def _write_markdown_report(report: dict, output_path: Path) -> None:
    summary = report["summary"]
    file_layout = report.get("file_layout")
    lines = [
        "# LMS Migration Pilot Report",
        "",
        f"Input zip: `{report['input_zip']}`",
        f"Output zip: `{report['output_zip']}`",
        f"Rules: `{report['rules']}`",
        f"Policy profile: `{report['policy_profile']['id']}`",
        f"Math handling: `{report.get('math_handling', 'preserve-semantic')}`",
        f"Best-practice enforcer enabled: `{report.get('best_practice_enforcer_enabled', False)}`",
        f"IMS manifest found: `{report['manifest_found']}`",
        "",
        "## Summary",
        "",
        f"- HTML files scanned: {summary['html_files_scanned']}",
        f"- HTML files changed: {summary['html_files_changed']}",
        f"- Automated changes applied: {summary['total_automated_changes']}",
        f"- Manual review issues: {summary['manual_review_issues']}",
        f"- Accessibility issues: {summary['accessibility_issues']}",
        "",
    ]
    if isinstance(file_layout, dict):
        lines.extend(
            [
                "## File Layout",
                "",
                f"- Course-content root: `{file_layout.get('course_content_root', '') or 'course-content'}`",
                f"- Loose support files considered: {file_layout.get('loose_files_considered', 0)}",
                f"- Loose support files relocated: {file_layout.get('files_relocated', 0)}",
                f"- Collisions skipped: {file_layout.get('collisions_skipped', 0)}",
                f"- Manifest files changed: {file_layout.get('manifest_files_changed', 0)}",
                f"- Manifest hrefs rewritten: {file_layout.get('manifest_hrefs_rewritten', 0)}",
                "",
            ]
        )

    issue_summary = report.get("issue_summary", {})
    top_manual_reasons = issue_summary.get("top_manual_review_reasons", [])
    top_a11y_reasons = issue_summary.get("top_accessibility_reasons", [])
    top_issue_files = issue_summary.get("top_issue_files", [])
    if issue_summary:
        lines.extend(
            [
                "## Unresolved / Manual Items",
                "",
                f"- Files with manual review issues: {issue_summary.get('files_with_manual_review_issues', 0)}",
                f"- Files with accessibility issues: {issue_summary.get('files_with_accessibility_issues', 0)}",
            ]
        )
        if top_manual_reasons:
            lines.append("- Top manual review reasons:")
            for row in top_manual_reasons[:8]:
                lines.append(f"  - ({row.get('count', 0)}) {row.get('reason', '')}")
        if top_a11y_reasons:
            lines.append("- Top accessibility reasons:")
            for row in top_a11y_reasons[:8]:
                lines.append(f"  - ({row.get('count', 0)}) {row.get('reason', '')}")
        if top_issue_files:
            lines.append("- Most-affected files:")
            for row in top_issue_files[:8]:
                lines.append(
                    f"  - `{row.get('path', '')}` (manual: {row.get('manual_review_issues', 0)}, "
                    f"a11y: {row.get('accessibility_issues', 0)})"
                )
        lines.append("")

    template_overlay = report.get("template_overlay")
    if isinstance(template_overlay, dict):
        overlay_summary = template_overlay.get("summary", {})
        overlay_inputs = template_overlay.get("inputs", {})
        materialization = template_overlay.get("materialization", {})
        lines.extend(
            [
                "## Template Overlay",
                "",
                f"- Enabled: `{template_overlay.get('enabled', False)}`",
                f"- Template package: `{overlay_inputs.get('template_package', '')}`",
                f"- Alias map JSON: `{overlay_inputs.get('alias_map_json', '') or 'none'}`",
                f"- Overlay report JSON: `{template_overlay.get('report_json', '') or 'n/a'}`",
                f"- Materialized template assets dir: `{materialization.get('asset_dir', '') or 'n/a'}`",
                f"- Materialized assets copied: {materialization.get('assets_copied', 0)}",
                f"- Mapped (direct): {overlay_summary.get('mapped_direct', 0)}",
                f"- Mapped (alias): {overlay_summary.get('mapped_alias', 0)}",
                f"- Unresolved template refs: {overlay_summary.get('unresolved_total', 0)}",
                f"- Ignored unresolved framework refs: {overlay_summary.get('ignored_unresolved_total', 0)}",
                "",
            ]
        )

    lines.extend(
        [
            "## Files With Issues",
            "",
        ]
    )

    issue_file_count = 0
    for file_entry in report["files"]:
        has_issues = (
            file_entry["manual_review_issues"] or file_entry["accessibility_issues"]
        )
        if not has_issues:
            continue
        issue_file_count += 1
        lines.append(f"- `{file_entry['path']}`")

    if issue_file_count == 0:
        lines.append("- None")

    reference_alignment = report.get("reference_alignment")
    if isinstance(reference_alignment, dict):
        lines.extend(
            [
                "",
                "## Reference Alignment",
                "",
                f"- Source: `{reference_alignment.get('source_file', '')}`",
                f"- Critical instruction gaps: {reference_alignment.get('critical_gap_count', 0)}",
                f"- Best-practice topics needing new rule/report coverage: {reference_alignment.get('best_practice_action_needed_count', 0)}",
                f"- Template placeholders detected in template docs: {len(reference_alignment.get('template_placeholder_patterns_detected', []))}",
                "- Module Checklist required closer present in template docs: "
                f"{reference_alignment.get('module_checklist_required_closer_present', True)}",
            ]
        )
        critical_ids = [
            item for item in reference_alignment.get("critical_gap_ids", []) if item
        ]
        if critical_ids:
            lines.append(f"- Critical gap IDs: {', '.join(critical_ids)}")
        coverage_ids = [
            item
            for item in reference_alignment.get("best_practice_action_needed_ids", [])
            if item
        ]
        if coverage_ids:
            lines.append(f"- Coverage action IDs: {', '.join(coverage_ids)}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# D2L XML audit helpers — graded discussions and availability windows
# ---------------------------------------------------------------------------

_D2L_NS_URI = "http://desire2learn.com/xsd/d2lcp_v2p0"
_D2L_TAG = "{" + _D2L_NS_URI + "}"


def _d2l_text_el(element: ET.Element, local: str) -> str:
    child = element.find(f"{_D2L_TAG}{local}")
    return (child.text or "").strip() if child is not None else ""


def _audit_graded_discussions(zip_path: Path) -> list[dict]:
    """Return one row per graded discussion topic found in D2L discussion XML files."""
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            disc_files = [
                n
                for n in names
                if re.match(r"discussion_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in disc_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                # Strip XML declaration
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for forum in root.iter("forum"):
                    forum_title_el = forum.find("content/title")
                    if forum_title_el is None:
                        forum_title_el = forum.find("title")
                    forum_title = (
                        (forum_title_el.text or "").strip()
                        if forum_title_el is not None
                        else forum.get("id", "unknown")
                    )
                    for topic in forum.iter("topic"):
                        # Graded topics have a <grade> child element (common), or
                        # a <properties><grade_item_id> element (some D2L versions
                        # store the link there instead of a <grade> wrapper).
                        grade_el = topic.find("grade")
                        props_grade_id_el = topic.find("properties/grade_item_id")
                        if grade_el is None and props_grade_id_el is None:
                            # Also check for score-related attributes
                            if not (
                                topic.get("gradeid")
                                or topic.get("grade_item")
                                or topic.find("grade_item") is not None
                            ):
                                continue
                        topic_title_el = topic.find("content/title")
                        if topic_title_el is None:
                            topic_title_el = topic.find("title")
                        topic_title = (
                            (topic_title_el.text or "").strip()
                            if topic_title_el is not None
                            else topic.get("id", "unknown topic")
                        )
                        rows.append(
                            {
                                "file": fname,
                                "type": "d2l_xml_audit",
                                "reason": "Graded discussion detected — enable scoring in Canvas Discussions",
                                "evidence": f"Forum: {forum_title} | Topic: {topic_title}",
                            }
                        )
    except Exception:
        pass
    return rows


def _audit_availability_windows(zip_path: Path) -> list[dict]:
    """Return one row per gradebook item that has an availability window set."""
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            grades_files = [
                n for n in names if re.match(r"grades_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in grades_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                # Items block contains <item> elements
                items_match = re.search(r"<items>(.*?)</items>", raw, re.DOTALL)
                if items_match is None:
                    continue
                items_text = items_match.group(1)
                for item_m in re.finditer(
                    r"<item\b[^>]*>.*?</item>", items_text, re.DOTALL
                ):
                    item_xml = item_m.group(0)
                    date_start = re.search(r"<date_start>(.*?)</date_start>", item_xml)
                    date_end = re.search(r"<date_end>(.*?)</date_end>", item_xml)
                    ds = date_start.group(1).strip() if date_start else ""
                    de = date_end.group(1).strip() if date_end else ""
                    if not (ds or de):
                        continue
                    name_m = re.search(r"<name>(.*?)</name>", item_xml)
                    name = name_m.group(1).strip() if name_m else "unknown item"
                    window_parts = []
                    if ds:
                        window_parts.append(f"start: {ds}")
                    if de:
                        window_parts.append(f"end: {de}")
                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": "Availability window detected in gradebook item — re-enter dates in Canvas",
                            "evidence": f"{name} | {', '.join(window_parts)}",
                        }
                    )
    except Exception:
        pass
    return rows


def _audit_gradebook_groups(zip_path: Path) -> list[dict]:
    """Return one row per grade category with drop rules or bonus items.

    Emits P1-worthy rows for categories that require specific Canvas configuration
    (drop rules and extra-credit items) and P2-worthy rows for weighted categories
    so the ID knows what weights to enter in Canvas assignment groups.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            grades_files = [
                n for n in names if re.match(r"grades_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in grades_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue

                # ── Categories ─────────────────────────────────────────────
                for cat_m in re.finditer(
                    r"<category\b[^>]*>.*?</category>", raw, re.DOTALL
                ):
                    cat_xml = cat_m.group(0)
                    name_m = re.search(r"<name>(.*?)</name>", cat_xml)
                    weight_m = re.search(r"<weight>(.*?)</weight>", cat_xml)
                    low_m = re.search(
                        r"<low_non_bonus_drop>(.*?)</low_non_bonus_drop>", cat_xml
                    )
                    high_m = re.search(
                        r"<high_non_bonus_drop>(.*?)</high_non_bonus_drop>", cat_xml
                    )

                    name = name_m.group(1).strip() if name_m else "unknown category"
                    weight = weight_m.group(1).strip() if weight_m else "0"
                    low_drop = low_m.group(1).strip() if low_m else "0"
                    high_drop = high_m.group(1).strip() if high_m else "0"

                    try:
                        low_int = int(low_drop)
                        high_int = int(high_drop)
                    except ValueError:
                        low_int = high_int = 0

                    if low_int > 0 or high_int > 0:
                        drop_parts = []
                        if low_int > 0:
                            drop_parts.append(f"drop {low_int} lowest")
                        if high_int > 0:
                            drop_parts.append(f"drop {high_int} highest")
                        rows.append(
                            {
                                "file": fname,
                                "type": "d2l_xml_audit",
                                "reason": (
                                    "Gradebook category with drop rule — "
                                    "configure in Canvas assignment group"
                                ),
                                "evidence": (
                                    f"{name} | {', '.join(drop_parts)} | weight={weight}%"
                                ),
                            }
                        )
                    else:
                        try:
                            weight_int = int(float(weight))
                        except ValueError:
                            weight_int = 0
                        if weight_int > 0:
                            rows.append(
                                {
                                    "file": fname,
                                    "type": "d2l_xml_audit",
                                    "reason": (
                                        "Gradebook category weight — "
                                        "verify in Canvas assignment group"
                                    ),
                                    "evidence": f"{name} | weight={weight}%",
                                }
                            )

                # ── Bonus / extra-credit items ──────────────────────────────
                items_match = re.search(r"<items>(.*?)</items>", raw, re.DOTALL)
                if items_match:
                    for item_m in re.finditer(
                        r"<item\b[^>]*>.*?</item>", items_match.group(1), re.DOTALL
                    ):
                        item_xml = item_m.group(0)
                        bonus_m = re.search(r"<is_bonus>(.*?)</is_bonus>", item_xml)
                        if not (bonus_m and bonus_m.group(1).strip().lower() == "true"):
                            continue
                        iname_m = re.search(r"<name>(.*?)</name>", item_xml)
                        item_name = (
                            iname_m.group(1).strip()
                            if iname_m
                            else "unknown bonus item"
                        )
                        rows.append(
                            {
                                "file": fname,
                                "type": "d2l_xml_audit",
                                "reason": (
                                    "Bonus/extra-credit grade item detected — "
                                    "configure in Canvas as extra credit"
                                ),
                                "evidence": item_name,
                            }
                        )
    except Exception:
        pass
    return rows


# D2L rubric scoring_method values
_RUBRIC_SCORING_METHOD_LABELS: dict[str, str] = {
    "1": "no-score (holistic/analytics only)",
    "2": "level-based points (all criteria share the same level values)",
    "3": "custom points (per-criterion cell values)",
}
_COURSE_CONTENT_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".html",
        ".htm",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".csv",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".mp4",
        ".webm",
        ".mov",
        ".mp3",
        ".wav",
        ".m4a",
        ".zip",
    }
)
_COURSE_CONTENT_RELOCATABLE_EXTENSIONS: frozenset[str] = frozenset(
    _COURSE_CONTENT_FILE_EXTENSIONS - {".html", ".htm", ".zip"}
)
_COURSE_CONTENT_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
)
_COURSE_CONTENT_POWERPOINT_EXTENSIONS: frozenset[str] = frozenset({".ppt", ".pptx"})
_COURSE_CONTENT_ROOT_FOLDER = "course-content"
_COURSE_CONTENT_IMAGES_FOLDER = "course-images"
_COURSE_CONTENT_POWERPOINTS_FOLDER = "powerpoints"
_COURSE_ALIGNMENT_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {".doc", ".docx", ".pdf", ".xls", ".xlsx"}
)
_PACKAGE_METADATA_BASENAMES: frozenset[str] = frozenset(
    {
        "imsmanifest.xml",
        "orgunitconfig",
        "questiondb.xml",
        "module_meta.xml",
        "files_meta.xml",
        "course_settings.xml",
        "context.xml",
        "late_policy.xml",
        "lti_context_controls.xml",
        "media_tracks.xml",
        "canvas_export.txt",
    }
)
_PACKAGE_METADATA_TOP_LEVEL_DIRS: frozenset[str] = frozenset(
    {
        "course_settings",
        "wiki_content",
        "web_resources",
        "templateassets",
        "non_cc_assessments",
    }
)
_D2L_EXPORT_METADATA_BASENAME_RE = re.compile(
    r".*_d2l(?:_\d+)?\.xml$",
    flags=re.IGNORECASE,
)
_QUIZ_MEDIA_REF_RE = re.compile(
    r"""(?:src|uri)\s*=\s*["'](?P<ref>[^"']+\.(?:png|jpg|jpeg|gif|svg|webp)[^"']*)["']""",
    flags=re.IGNORECASE,
)
_QUIZ_MEDIA_MARKER_RE = re.compile(
    r"&lt;img\b|<img\b|<matimage\b|<object\b|quizimages/|"
    r"(?:src|uri)\s*=\s*[\"'][^\"']+\.(?:png|jpg|jpeg|gif|svg|webp)[^\"']*[\"']",
    flags=re.IGNORECASE,
)
_LOCAL_PACKAGE_REF_ATTR_RE = re.compile(
    r"""(?:href|src|poster|data|uri)\s*=\s*["'](?P<ref>[^"']+)["']""",
    flags=re.IGNORECASE,
)
_LOCAL_PACKAGE_REF_ORIGINAL_ATTR_RE = re.compile(
    r"""data-migration-original-href\s*=\s*["'](?P<ref>[^"']+)["']""",
    flags=re.IGNORECASE,
)
_LOCAL_PACKAGE_REF_CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<ref>[^)"']+)(?P=quote)\s*\)""",
    flags=re.IGNORECASE,
)
_REFERENCED_TEXT_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {".html", ".htm", ".xml", ".css", ".svg", ".js", ".txt"}
)
_PROTECTED_PACKAGE_PREFIXES: tuple[str, ...] = (
    "web_resources/",
    "templateassets/",
    "template-images/",
    "course_image/",
)


def _load_rubric_name_map(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for rub_m in re.finditer(r"<rubric\b[^>]*>.*?</rubric>", raw, re.DOTALL):
        rub_xml = rub_m.group(0)
        id_m = re.search(r'\bid="([^"]*)"', rub_xml)
        name_m = re.search(r'\bname="([^"]*)"', rub_xml)
        rubric_id = id_m.group(1).strip() if id_m else ""
        rubric_name = name_m.group(1).strip() if name_m else ""
        if rubric_id and rubric_name:
            mapping[rubric_id] = rubric_name
    return mapping


def _is_package_metadata_file(path_text: str) -> bool:
    normalized = path_text.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return True
    basename = Path(normalized).name.lower()
    if basename in _PACKAGE_METADATA_BASENAMES:
        return True
    if _D2L_EXPORT_METADATA_BASENAME_RE.match(basename):
        return True
    top_level = normalized.split("/", 1)[0].lower()
    return top_level in _PACKAGE_METADATA_TOP_LEVEL_DIRS


def _is_course_content_file(path_text: str) -> bool:
    normalized = path_text.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return False
    if _is_package_metadata_file(normalized):
        return False
    return Path(normalized).suffix.lower() in _COURSE_CONTENT_FILE_EXTENSIONS


def _is_protected_package_file(path_text: str) -> bool:
    normalized = path_text.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return True
    if _is_package_metadata_file(normalized):
        return True
    lowered = normalized.lower()
    return any(lowered.startswith(prefix) for prefix in _PROTECTED_PACKAGE_PREFIXES)


def _resolve_local_package_ref(current_path: str, raw_ref: str) -> str | None:
    decoded = html.unescape(str(raw_ref or "").strip())
    if not decoded:
        return None
    lowered = decoded.lower()
    if lowered.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None

    parsed = urlparse(decoded)
    if parsed.scheme or parsed.netloc:
        return None

    path_text = (parsed.path or "").strip().replace("\\", "/")
    if not path_text:
        return None

    if path_text.startswith("/"):
        normalized = posixpath.normpath(path_text.lstrip("/"))
    else:
        normalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(current_path), path_text)
        )
    normalized = normalized.lstrip("./")
    if not normalized or normalized.startswith("../"):
        return None
    return normalized


def _extract_local_package_refs(current_path: str, text: str) -> set[str]:
    refs: set[str] = set()
    for pattern in (
        _LOCAL_PACKAGE_REF_ATTR_RE,
        _LOCAL_PACKAGE_REF_ORIGINAL_ATTR_RE,
        _LOCAL_PACKAGE_REF_CSS_URL_RE,
    ):
        for match in pattern.finditer(text or ""):
            resolved = _resolve_local_package_ref(current_path, match.group("ref"))
            if resolved:
                refs.add(resolved)
    return refs


def _collect_manifest_referenced_paths(unpack_dir: Path) -> set[str]:
    referenced: set[str] = set()
    for manifest_path in sorted(unpack_dir.rglob("imsmanifest.xml")):
        if not manifest_path.is_file():
            continue
        relative_manifest = str(manifest_path.relative_to(unpack_dir).as_posix())
        referenced.add(relative_manifest)
        try:
            tree = ET.parse(manifest_path)
        except ET.ParseError:
            continue
        root = tree.getroot()
        base_dir = posixpath.dirname(relative_manifest)
        for element in root.iter():
            for key, value in list(element.attrib.items()):
                if _local_name(key) != "href":
                    continue
                resolved = _resolve_local_package_ref(
                    posixpath.join(base_dir, "__manifest__"),
                    str(value or ""),
                )
                if resolved:
                    referenced.add(resolved)
    return referenced


def _expand_referenced_package_paths(
    unpack_dir: Path,
    *,
    initial_paths: set[str],
) -> set[str]:
    all_files = {
        str(path.relative_to(unpack_dir).as_posix())
        for path in unpack_dir.rglob("*")
        if path.is_file()
    }
    kept = {path for path in initial_paths if path in all_files}
    queue: deque[str] = deque(sorted(kept))

    while queue:
        current = queue.popleft()
        suffix = Path(current).suffix.lower()
        if suffix not in _REFERENCED_TEXT_FILE_EXTENSIONS:
            continue
        file_path = unpack_dir / Path(current)
        if not file_path.is_file():
            continue
        try:
            text = _read_text(file_path)
        except Exception:
            continue
        for resolved in _extract_local_package_refs(current, text):
            if resolved not in all_files or resolved in kept:
                continue
            kept.add(resolved)
            queue.append(resolved)
    return kept


def _trim_unreferenced_package_files(unpack_dir: Path) -> dict:
    all_files = {
        str(path.relative_to(unpack_dir).as_posix())
        for path in unpack_dir.rglob("*")
        if path.is_file()
    }
    protected = {path for path in all_files if _is_protected_package_file(path)}
    manifest_referenced = _collect_manifest_referenced_paths(unpack_dir)
    kept = _expand_referenced_package_paths(
        unpack_dir,
        initial_paths=protected | manifest_referenced,
    )

    removed_paths: list[str] = []
    removed_count = 0
    for relative_path in sorted(all_files):
        if relative_path in kept or _is_protected_package_file(relative_path):
            continue
        path = unpack_dir / Path(relative_path)
        if not path.is_file():
            continue
        path.unlink()
        removed_count += 1
        if len(removed_paths) < 20:
            removed_paths.append(relative_path)

    return {
        "pruning_enabled": True,
        "protected_files": len(protected),
        "manifest_seed_files": len(manifest_referenced),
        "kept_files": len(kept),
        "files_pruned": removed_count,
        "pruned_paths_sample": removed_paths,
    }


def _recommended_course_content_destination(
    path_text: str,
    *,
    course_content_root: str = _COURSE_CONTENT_ROOT_FOLDER,
) -> str | None:
    normalized = path_text.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/") or "/" in normalized:
        return None
    if _is_package_metadata_file(normalized):
        return None

    basename = Path(normalized).name
    suffix = Path(basename).suffix.lower()
    if suffix not in _COURSE_CONTENT_RELOCATABLE_EXTENSIONS:
        return None

    if suffix in _COURSE_CONTENT_IMAGE_EXTENSIONS:
        return (
            f"{course_content_root}/{_COURSE_CONTENT_IMAGES_FOLDER}/{basename}"
        )
    if suffix in _COURSE_CONTENT_POWERPOINT_EXTENSIONS:
        return (
            f"{course_content_root}/{_COURSE_CONTENT_POWERPOINTS_FOLDER}/{basename}"
        )
    return f"{course_content_root}/{basename}"


def _normalize_loose_course_content_layout(
    unpack_dir: Path,
    *,
    course_content_root: str = _COURSE_CONTENT_ROOT_FOLDER,
) -> tuple[dict[str, str], dict]:
    relocated: dict[str, str] = {}
    relocated_samples: list[str] = []
    collision_samples: list[str] = []
    loose_files_considered = 0
    collisions_skipped = 0

    for file_path in sorted(unpack_dir.iterdir(), key=lambda item: item.name.lower()):
        if not file_path.is_file():
            continue
        destination_relative = _recommended_course_content_destination(
            file_path.name,
            course_content_root=course_content_root,
        )
        if not destination_relative:
            continue

        loose_files_considered += 1
        destination_path = unpack_dir / Path(destination_relative)
        if destination_path.exists():
            collisions_skipped += 1
            if len(collision_samples) < 10:
                collision_samples.append(
                    f"{file_path.name} -> {destination_relative}"
                )
            continue

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.rename(destination_path)
        relocated[file_path.name] = destination_relative
        if len(relocated_samples) < 10:
            relocated_samples.append(f"{file_path.name} -> {destination_relative}")

    summary = {
        "enabled": True,
        "course_content_root": course_content_root,
        "loose_files_considered": loose_files_considered,
        "files_relocated": len(relocated),
        "collisions_skipped": collisions_skipped,
        "relocated_paths_sample": relocated_samples,
        "collision_paths_sample": collision_samples,
    }
    return relocated, summary


def _rewrite_manifest_hrefs_for_moved_files(
    unpack_dir: Path,
    moved_paths: dict[str, str],
) -> dict:
    if not moved_paths:
        return {"manifest_files_changed": 0, "manifest_hrefs_rewritten": 0}

    manifest_files_changed = 0
    manifest_hrefs_rewritten = 0

    for manifest_path in sorted(unpack_dir.rglob("imsmanifest.xml")):
        if not manifest_path.is_file():
            continue
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        manifest_changed = False
        rewritten_here = 0

        for element in root.iter():
            for key, value in list(element.attrib.items()):
                if _local_name(key) != "href":
                    continue
                normalized_value = value.strip().replace("\\", "/").lstrip("./")
                replacement = moved_paths.get(normalized_value)
                if not replacement or replacement == value:
                    continue
                element.set(key, replacement)
                manifest_changed = True
                rewritten_here += 1

        if manifest_changed:
            tree.write(manifest_path, encoding="utf-8", xml_declaration=True)
            manifest_files_changed += 1
            manifest_hrefs_rewritten += rewritten_here

    return {
        "manifest_files_changed": manifest_files_changed,
        "manifest_hrefs_rewritten": manifest_hrefs_rewritten,
    }


def _audit_rubrics(zip_path: Path) -> list[dict]:
    """Return one row per rubric found in ``rubrics_d2l.xml``, with criteria/level counts.

    D2L rubrics are NOT transferred by the standard Canvas IMSCC import; each must be
    recreated manually in Canvas.  This function inventories what needs to be recreated
    so the ID has a complete list with exact names and complexity indicators.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            rubric_files = [
                n for n in names if re.match(r"rubrics_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in rubric_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue

                for rub_m in re.finditer(
                    r"<rubric\b[^>]*>.*?</rubric>", raw, re.DOTALL
                ):
                    rub_xml = rub_m.group(0)

                    name_m = re.search(r'\bname="([^"]*)"', rub_xml)
                    method_m = re.search(r'\bscoring_method="([^"]*)"', rub_xml)
                    state_m = re.search(r'\bstate="([^"]*)"', rub_xml)

                    name = name_m.group(1).strip() if name_m else "unnamed rubric"
                    method_raw = method_m.group(1).strip() if method_m else ""
                    scoring_label = _RUBRIC_SCORING_METHOD_LABELS.get(
                        method_raw, f"scoring_method={method_raw}"
                    )

                    # State: 0=active, 1=archived, 2=draft (common D2L conventions)
                    state_raw = state_m.group(1).strip() if state_m else ""
                    state_label: str = {
                        "0": "active",
                        "1": "archived",
                        "2": "draft",
                    }.get(state_raw, f"state={state_raw}")

                    criteria = re.findall(r"<criterion\b", rub_xml)
                    levels = re.findall(r"<level\b", rub_xml)
                    criteria_count = len(criteria)
                    level_count = len(set(re.findall(r'level_id="([^"]*)"', rub_xml)))

                    # Detect range-style cells: cells where cell_value is empty
                    # (D2L level-based rubrics) vs fixed numeric (custom points)
                    cell_values = re.findall(r'cell_value="([^"]*)"', rub_xml)
                    has_empty_cells = any(v.strip() == "" for v in cell_values)
                    has_numeric_cells = any(
                        v.strip() not in ("", "0", "0.000000000") for v in cell_values
                    )

                    evidence_parts = [
                        f'rubric: "{name}"',
                        f"{criteria_count} criteria",
                        f"{level_count} levels",
                        scoring_label,
                        f"status: {state_label}",
                    ]
                    if has_empty_cells and not has_numeric_cells:
                        evidence_parts.append(
                            "NOTE: level-value cells — enable Range option in Canvas rubric"
                        )

                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": (
                                "D2L rubric detected — recreate in Canvas and attach to assignment"
                            ),
                            "evidence": " | ".join(evidence_parts),
                        }
                    )
    except Exception:
        pass
    return rows


def _audit_course_alignment_docs(zip_path: Path) -> list[dict]:
    """Return one advisory row when the export includes a course alignment document."""
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            matches: list[str] = []
            for name in zf.namelist():
                if not name or name.endswith("/"):
                    continue
                suffix = Path(name).suffix.lower()
                if suffix not in _COURSE_ALIGNMENT_DOC_EXTENSIONS:
                    continue
                normalized = name.lower().replace("_", " ").replace("-", " ")
                if (
                    "course alignment" in normalized
                    or "alignment document" in normalized
                    or "curriculum map" in normalized
                ):
                    matches.append(name)
            if matches:
                rows.append(
                    {
                        "file": matches[0],
                        "type": "d2l_xml_audit",
                        "reason": (
                            "Course alignment document detected — use during syllabus, module, and assessment verification"
                        ),
                        "evidence": " | ".join(sorted(matches)),
                    }
                )
    except Exception:
        pass
    return rows


def _audit_file_organization_risks(zip_path: Path) -> list[dict]:
    """Flag packages whose source file layout should be preserved during migration.

    The goal is advisory only: if the export has many loose top-level files or
    duplicate basenames across folders, auto-reorganizing the package during
    migration is more likely to break relative links or create collisions.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            content_files: list[str] = []
            top_level_loose_files: list[str] = []
            by_basename: dict[str, list[str]] = {}

            for name in zf.namelist():
                normalized = name.strip().replace("\\", "/").lstrip("/")
                if not _is_course_content_file(normalized):
                    continue
                content_files.append(normalized)
                if "/" not in normalized:
                    top_level_loose_files.append(normalized)
                basename = Path(normalized).name.lower()
                by_basename.setdefault(basename, []).append(normalized)

            duplicate_groups = {
                basename: sorted(paths)
                for basename, paths in by_basename.items()
                if len(paths) > 1
                and len({str(Path(path).parent).lower() for path in paths}) > 1
            }

            if len(top_level_loose_files) >= 6:
                evidence_parts = [
                    f"top-level loose content files: {len(top_level_loose_files)}",
                    f"total content files: {len(content_files)}",
                    "sample loose files: "
                    + ", ".join(sorted(top_level_loose_files)[:6]),
                ]
                if duplicate_groups:
                    evidence_parts.append(
                        f"duplicate basenames across folders: {len(duplicate_groups)}"
                    )
                rows.append(
                    {
                        "file": top_level_loose_files[0],
                        "type": "d2l_xml_audit",
                        "reason": (
                            "Scattered D2L file organization detected — preserve file paths during migration and plan optional Canvas Files cleanup later"
                        ),
                        "evidence": " | ".join(evidence_parts),
                    }
                )

            if duplicate_groups:
                duplicate_samples = [
                    f"{Path(paths[0]).name} ({len(paths)} copies)"
                    for _basename, paths in sorted(duplicate_groups.items())[:5]
                ]
                rows.append(
                    {
                        "file": sorted(next(iter(duplicate_groups.values())))[0],
                        "type": "d2l_xml_audit",
                        "reason": (
                            "Duplicate content filenames across D2L folders detected — avoid automatic file moves during migration"
                        ),
                        "evidence": " | ".join(
                            [
                                f"duplicate basenames across folders: {len(duplicate_groups)}",
                                "sample duplicates: " + ", ".join(duplicate_samples),
                            ]
                        ),
                    }
                )
    except Exception:
        pass
    return rows


def _audit_instructor_only_manifest_items(zip_path: Path) -> list[dict]:
    """Return one advisory row when the D2L manifest contains hidden/instructor-only items."""
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            manifest_items = _build_manifest_item_inventory(zf)
    except Exception:
        return rows

    flagged_titles: list[str] = []
    for item in manifest_items:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        title_lower = title.lower()
        is_hidden = (item.get("isvisible") or "").strip().lower() == "false"
        has_instructor_marker = any(
            token in title_lower
            for token in (
                "for faculty use only",
                "for instructor use only",
                "faculty use only",
                "instructor only",
                "staff only",
                "do not publish",
            )
        )
        if is_hidden or has_instructor_marker:
            flagged_titles.append(title)

    unique_titles: list[str] = []
    for title in flagged_titles:
        if title not in unique_titles:
            unique_titles.append(title)

    if unique_titles:
        evidence_parts = [f"items: {len(unique_titles)}"]
        evidence_parts.append(
            "sample titles: " + ", ".join(f'"{title}"' for title in unique_titles[:5])
        )
        rows.append(
            {
                "file": "imsmanifest.xml",
                "type": "d2l_xml_audit",
                "reason": (
                    "Hidden or instructor-only D2L content detected — keep unpublished in Canvas"
                ),
                "evidence": " | ".join(evidence_parts),
            }
        )
    return rows


def _audit_quiz_release_conditions(zip_path: Path) -> list[dict]:
    """Return one row per quiz-based D2L release rule that gated later content."""
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            manifest_items = _build_manifest_item_inventory(zf)
            if not manifest_items:
                return rows

            titles_by_condition_set: dict[str, list[str]] = {}
            quiz_title_by_rcode: dict[str, str] = {}
            for item in manifest_items:
                title = (item.get("title") or "").strip()
                condition_set = (item.get("condition_set") or "").strip()
                if title and condition_set:
                    titles_by_condition_set.setdefault(condition_set, []).append(title)
                href = (item.get("href") or "").strip()
                if href:
                    parsed = urlparse(href)
                    query = parse_qs(parsed.query)
                    if query.get("type", [""])[0].lower() == "quiz":
                        rcode = query.get("rcode", [""])[0].strip()
                        if rcode and title and rcode not in quiz_title_by_rcode:
                            quiz_title_by_rcode[rcode] = title

            grouped: dict[tuple[str, str], list[str]] = {}
            release_files = [
                n
                for n in zf.namelist()
                if re.match(r"conditionalrelease_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in release_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for condition_set in root.iter("condition_set"):
                    resource_code = (condition_set.get("resource_code") or "").strip()
                    if not resource_code:
                        continue
                    target_titles = titles_by_condition_set.get(resource_code, [])
                    for condition in condition_set.iter("condition"):
                        if (condition.get("condition_type") or "").strip() != "3":
                            continue
                        quiz_rcode = (condition.get("quiz") or "").strip()
                        if not quiz_rcode:
                            continue
                        threshold = (condition.get("percentage_value") or "").strip()
                        grouped.setdefault((quiz_rcode, threshold), []).extend(target_titles)

            for (quiz_rcode, threshold), target_titles in sorted(grouped.items()):
                unique_titles: list[str] = []
                for title in target_titles:
                    if title and title not in unique_titles:
                        unique_titles.append(title)
                if not unique_titles:
                    continue
                quiz_title = quiz_title_by_rcode.get(quiz_rcode, "")
                evidence_parts = [
                    f'quiz: "{quiz_title}"' if quiz_title else f"quiz rCode: {quiz_rcode}"
                ]
                if threshold:
                    try:
                        threshold_value = float(threshold)
                        threshold_label = (
                            f"{int(threshold_value)}%"
                            if threshold_value == int(threshold_value)
                            else f"{threshold_value:.1f}%"
                        )
                    except ValueError:
                        threshold_label = threshold
                    evidence_parts.append(f"required score: {threshold_label}")
                evidence_parts.append(f"gated items: {len(unique_titles)}")
                evidence_parts.append(
                    "sample targets: "
                    + ", ".join(f'"{title}"' for title in unique_titles[:5])
                )
                rows.append(
                    {
                        "file": "conditionalrelease_d2l.xml",
                        "type": "d2l_xml_audit",
                        "reason": (
                            "Quiz-based release condition detected — recreate module prerequisites/requirements in Canvas"
                        ),
                        "evidence": " | ".join(evidence_parts),
                    }
                )
    except Exception:
        pass
    return rows


def _audit_dropbox_folders(zip_path: Path) -> list[dict]:
    """Return one row per D2L Dropbox submission folder found in dropbox_d2l.xml.

    D2L Dropbox folders are the primary assignment-submission mechanism and map
    to Canvas Assignments.  Canvas does NOT auto-import them — they use the D2L
    proprietary ``d2ldropbox`` IMSCC resource type which Canvas ignores.  Each
    folder must be recreated manually in Canvas.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            rubric_name_map: dict[str, str] = {}
            rubric_files = [
                n for n in names if re.match(r"rubrics_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for rubric_file in rubric_files:
                try:
                    rubric_raw = zf.read(rubric_file).decode("utf-8", errors="replace")
                except Exception:
                    continue
                rubric_name_map.update(_load_rubric_name_map(rubric_raw))
            dropbox_files = [
                n for n in names if re.match(r"dropbox_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in dropbox_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                # Strip the d2l_2p0 namespace prefix so ET can parse cleanly
                raw = re.sub(r"\bd2l_2p0:", "", raw)
                raw = re.sub(r'\bxmlns:d2l_2p0="[^"]*"', "", raw)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for folder in root.iter("folder"):
                    name = folder.get("name", "").strip() or folder.get("id", "unknown")
                    out_of = folder.get("out_of", "").strip()
                    grade_item = folder.get("grade_item", "").strip()
                    is_hidden = folder.get("is_hidden", "false").strip().lower()

                    # Due date
                    due_el = folder.find("date_due")
                    due_str = ""
                    if due_el is not None and due_el.text:
                        due_str = due_el.text.strip()[:10]  # YYYY-MM-DD

                    # Availability window
                    start_el = folder.find("availability_start/availability_date")
                    end_el = folder.find("availability_end/availability_date")
                    avail_parts: list[str] = []
                    if start_el is not None and start_el.text:
                        avail_parts.append(f"open {start_el.text.strip()[:10]}")
                    if end_el is not None and end_el.text:
                        avail_parts.append(f"close {end_el.text.strip()[:10]}")
                    avail_str = " | ".join(avail_parts) if avail_parts else ""

                    # Rubric association
                    rubric_el = folder.find(".//rubric")
                    rubric_note = ""
                    if rubric_el is not None and rubric_el.text:
                        rubric_id = rubric_el.text.strip()
                        rubric_name = rubric_name_map.get(rubric_id, "")
                        if rubric_name:
                            rubric_note = f' | rubric: {rubric_id} ("{rubric_name}")'
                        else:
                            rubric_note = f" | rubric: {rubric_id}"

                    # Build evidence string
                    evidence_parts = [f'folder: "{name}"']
                    if out_of:
                        evidence_parts.append(
                            f"points: {out_of.rstrip('0').rstrip('.')}"
                        )
                    if grade_item:
                        evidence_parts.append(f"grade_item: {grade_item}")
                    if due_str:
                        evidence_parts.append(f"due: {due_str}")
                    if avail_str:
                        evidence_parts.append(avail_str)
                    if rubric_note:
                        evidence_parts.append(rubric_note.lstrip(" | "))
                    if is_hidden == "true":
                        evidence_parts.append("status: hidden")

                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": (
                                "D2L Dropbox assignment detected — "
                                "verify Canvas imported as Assignment and configure submission settings"
                            ),
                            "evidence": " | ".join(evidence_parts),
                        }
                    )
    except Exception:
        pass
    return rows


def _build_questiondb_item_map(zf: ZipFile) -> dict[str, str]:
    items: dict[str, str] = {}
    for name in zf.namelist():
        if name.split("/")[-1].lower() != "questiondb.xml":
            continue
        try:
            raw = zf.read(name).decode("utf-8", errors="replace")
        except Exception:
            continue
        for match in re.finditer(
            r'<item\b[^>]*ident="(?P<ident>[^"]+)"[^>]*>.*?</item>',
            raw,
            re.DOTALL | re.IGNORECASE,
        ):
            ident = match.group("ident").strip()
            if ident and ident not in items:
                items[ident] = match.group(0)
    return items


def _question_item_media_refs(item_xml: str) -> list[str]:
    candidates: list[str] = []
    decoded = html.unescape(item_xml)
    for source in (item_xml, decoded):
        for match in _QUIZ_MEDIA_REF_RE.finditer(source):
            ref = match.group("ref").strip()
            if ref and ref not in candidates:
                candidates.append(ref)
    return candidates


def _question_item_contains_media(item_xml: str) -> bool:
    decoded = html.unescape(item_xml)
    return bool(
        _QUIZ_MEDIA_MARKER_RE.search(item_xml) or _QUIZ_MEDIA_MARKER_RE.search(decoded)
    )


def _audit_quiz_embedded_media(zip_path: Path) -> list[dict]:
    """Flag quizzes whose question stems/options include embedded images or media refs.

    D2L often stores these in questiondb.xml as HTML-encoded <img> tags. Canvas New
    Quizzes can require manual rebuilding/re-uploading to preserve layout and image
    placement exactly.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            questiondb_items = _build_questiondb_item_map(zf)
            names = zf.namelist()
            quiz_files = [
                n for n in names if re.match(r"quiz_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in quiz_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                title_m = re.search(r'<assessment\b[^>]*title="([^"]*)"', raw)
                quiz_title = title_m.group(1).strip() if title_m else fname
                question_xml_by_id: dict[str, str] = {}
                for item_m in re.finditer(
                    r'<item\b[^>]*ident="(?P<ident>[^"]+)"[^>]*>.*?</item>',
                    raw,
                    re.DOTALL | re.IGNORECASE,
                ):
                    ident = item_m.group("ident").strip()
                    if ident:
                        question_xml_by_id[ident] = item_m.group(0)
                for itemref_m in re.finditer(
                    r'<itemref\b[^>]*linkrefid="(?P<ident>[^"]+)"',
                    raw,
                    re.IGNORECASE,
                ):
                    ident = itemref_m.group("ident").strip()
                    if ident and ident in questiondb_items and ident not in question_xml_by_id:
                        question_xml_by_id[ident] = questiondb_items[ident]

                media_question_ids: list[str] = []
                sample_refs: list[str] = []
                for ident, item_xml in question_xml_by_id.items():
                    if not _question_item_contains_media(item_xml):
                        continue
                    media_question_ids.append(ident)
                    for ref in _question_item_media_refs(item_xml):
                        if ref not in sample_refs:
                            sample_refs.append(ref)
                        if len(sample_refs) >= 4:
                            break

                if media_question_ids:
                    evidence_parts = [
                        f'quiz: "{quiz_title}"',
                        f"{len(media_question_ids)} question(s) with embedded images/media",
                        "sample question ids: " + ", ".join(media_question_ids[:4]),
                    ]
                    if sample_refs:
                        evidence_parts.append("sample refs: " + ", ".join(sample_refs[:4]))
                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": (
                                "Quiz question images/media detected — rebuild or verify in Canvas New Quizzes"
                            ),
                            "evidence": " | ".join(evidence_parts),
                        }
                    )
    except Exception:
        pass
    return rows


def _audit_unresolvable_grade_items(zip_path: Path) -> list[dict]:
    """Return one row per grade item that has no corresponding Canvas-importable D2L object.

    When D2L is exported as IMSCC, Canvas natively imports quizzes (QTI), discussions,
    and HTML pages — but NOT D2L Dropbox folders, and NOT external-tool grade items
    (e.g. Cengage, MyOpenMath, Respondus).  Any grade item whose ``resource_code``
    cannot be resolved to a known submission object will appear as an orphaned grade
    column in Canvas with no associated assignment for students to submit work.

    Skips:
    - Bonus items (already audited by ``_audit_gradebook_groups``).
    - Items whose resource_code matches a dropbox folder's ``grade_item`` attr
      (already audited by ``_audit_dropbox_folders``).
    - Items whose resource_code matches a discussion topic's ``grade_item_id``
      text (Canvas imports these discussions automatically; ``_audit_graded_discussions``
      handles the grading-setup reminder for them).
    - Items whose name (case-insensitive) matches a manifest quiz or discussion
      title — Canvas imports those objects and auto-creates connected grade columns.
    - Items without a ``resource_code`` (calculated/formula grade items).
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()

            # ── Resolved set 1: Dropbox grade_item attribute refs ──────────
            # folder[@grade_item] value IS the grade item's resource_code
            resolved_grade_rcs: set[str] = set()
            dropbox_files = [
                n for n in names if re.match(r"dropbox_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in dropbox_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                raw = re.sub(r"\bd2l_2p0:", "", raw)
                raw = re.sub(r'\bxmlns:d2l_2p0="[^"]*"', "", raw)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for folder in root.iter("folder"):
                    gi = folder.get("grade_item", "").strip()
                    if gi:
                        resolved_grade_rcs.add(gi)

            # ── Resolved set 2: Discussion topic grade_item_id refs ────────
            # Some D2L exports store the grade item RC directly in
            # <topic><properties><grade_item_id>RC</grade_item_id></properties>
            # instead of a <grade> child element.  The text IS the grade item
            # resource_code.  Canvas imports these discussions automatically;
            # _audit_graded_discussions emits the grading-setup reminder.
            disc_files = [
                n
                for n in names
                if re.match(r"discussion_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in disc_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for topic in root.iter("topic"):
                    props = topic.find("properties")
                    if props is None:
                        continue
                    gi = (props.findtext("grade_item_id") or "").strip()
                    if gi:
                        resolved_grade_rcs.add(gi)

            # ── Resolved set 3: Manifest quiz / discussion titles ───────────
            # Grade item resource_codes use a DIFFERENT internal ID space than
            # manifest item resource_codes, so direct RC matching is not possible
            # for quizzes and discussions.  Instead, match by (case-insensitive)
            # item title — Canvas QTI import and discussion import both auto-create
            # linked grade columns, so these items are already handled.
            _CANVAS_IMPORTED_TYPES = frozenset(
                {
                    "D2L.LE.Quizzing.Quiz",
                    "D2L.LE.Discussions.DiscussionTopic",
                }
            )
            manifest_resolved_names: set[str] = set()
            manifest_files = [
                n for n in names if re.match(r"imsmanifest\.xml$", n.rsplit("/", 1)[-1])
            ]
            _imscp_ns = "http://www.imsglobal.org/xsd/imscp_v1p1"
            for fname in manifest_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                try:
                    mroot = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                for mitem in mroot.iter(f"{{{_imscp_ns}}}item"):
                    rtk = mitem.get("resource_type_key", "")
                    if rtk not in _CANVAS_IMPORTED_TYPES:
                        continue
                    # Title is a child <title> element
                    title_el = mitem.find(f"{{{_imscp_ns}}}title")
                    if title_el is None:
                        title_el = mitem.find("title")
                    title = (
                        (title_el.text or "").strip() if title_el is not None else ""
                    )
                    if title:
                        manifest_resolved_names.add(title.lower())

            # ── Parse grades_d2l.xml ────────────────────────────────────────
            grades_files = [
                n for n in names if re.match(r"grades_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in grades_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                raw = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue

                # Build category id → name map
                cat_names: dict[str, str] = {}
                for cat in root.iter("category"):
                    cid = cat.get("identifier", "").strip() or cat.get("id", "").strip()
                    cname = (cat.findtext("name") or "").strip()
                    if cid:
                        cat_names[cid] = cname

                for item in root.iter("item"):
                    rc = item.get("resource_code", "").strip()
                    if not rc:
                        continue  # calculated / formula items — skip
                    is_bonus = (item.findtext("scoring/is_bonus") or "false").strip()
                    if is_bonus.lower() == "true":
                        continue  # handled by _audit_gradebook_groups
                    if rc in resolved_grade_rcs:
                        continue  # dropbox-linked — already in dropbox audit

                    name = (item.findtext("name") or "").strip() or rc
                    if name.lower() in manifest_resolved_names:
                        continue  # quiz or discussion Canvas imports automatically

                    cat_id = (item.findtext("category_id") or "").strip()
                    cat_name = cat_names.get(cat_id, "").strip()
                    # Points value
                    out_of = (item.findtext("scoring/out_of") or "").strip()
                    if out_of:
                        try:
                            pts = float(out_of)
                            out_of_str = (
                                str(int(pts)) if pts == int(pts) else f"{pts:.1f}"
                            )
                        except ValueError:
                            out_of_str = out_of
                    else:
                        out_of_str = ""

                    evidence_parts = [f'grade item: "{name}"']
                    if out_of_str:
                        evidence_parts.append(f"points: {out_of_str}")
                    if cat_name:
                        evidence_parts.append(f"category: {cat_name}")

                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": (
                                "Unresolvable grade item — no D2L submission object "
                                "found; create Canvas Assignment and connect to "
                                "gradebook after import"
                            ),
                            "evidence": " | ".join(evidence_parts),
                        }
                    )
    except Exception:
        pass
    return rows


def _audit_date_shift_items(zip_path: Path) -> list[dict]:
    """Inventory date-bearing D2L items to support Canvas date-shift planning.

    D2L content exports (IMSCC) do NOT include the course offering start date — that
    lives in the D2L enrollment system.  This function:
      1. Reports that the start date is absent (P1 advisory for every course).
      2. Surfaces quiz availability windows when present (courses other than ACC-2321 may have them).
      3. Extracts course announcement dates from news_d2l.xml as a proxy date-range hint.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()

            # ── Announcement dates (news_d2l.xml) ──────────────────────────
            news_files = [
                n for n in names if re.match(r"news_d2l\.xml$", n.rsplit("/", 1)[-1])
            ]
            all_news_dates: list[str] = []
            for fname in news_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                for item_m in re.finditer(r"<item\b[^>]*>.*?</item>", raw, re.DOTALL):
                    date_m = re.search(
                        r"<date_start>(.*?)</date_start>", item_m.group(0)
                    )
                    if date_m:
                        ds = date_m.group(1).strip()
                        if ds:
                            all_news_dates.append(ds)

            # ── Quiz availability windows ───────────────────────────────────
            quiz_files = [
                n
                for n in names
                if re.match(r"quiz_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in quiz_files:
                try:
                    raw = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                # D2L namespace prefix varies; match any prefix
                for ext_m in re.finditer(
                    r"<(?:[^>:\s]*:)?assess_procextension\b[^>]*>.*?"
                    r"</(?:[^>:\s]*:)?assess_procextension>",
                    raw,
                    re.DOTALL,
                ):
                    ext_xml = ext_m.group(0)
                    ds_m = re.search(
                        r"<(?:[^>:\s]*:)?date_start>(.*?)</(?:[^>:\s]*:)?date_start>",
                        ext_xml,
                    )
                    de_m = re.search(
                        r"<(?:[^>:\s]*:)?date_end>(.*?)</(?:[^>:\s]*:)?date_end>",
                        ext_xml,
                    )
                    dd_m = re.search(
                        r"<(?:[^>:\s]*:)?date_due>(.*?)</(?:[^>:\s]*:)?date_due>",
                        ext_xml,
                    )
                    ds = ds_m.group(1).strip() if ds_m else ""
                    de = de_m.group(1).strip() if de_m else ""
                    dd = dd_m.group(1).strip() if dd_m else ""
                    if not (ds or de or dd):
                        continue

                    title_m = re.search(r'<assessment[^>]+title="([^"]+)"', raw)
                    quiz_name = title_m.group(1).strip() if title_m else fname

                    parts = [f"quiz: {quiz_name}"]
                    if ds:
                        parts.append(f"available from: {ds}")
                    if de:
                        parts.append(f"available until: {de}")
                    if dd:
                        parts.append(f"due: {dd}")
                    rows.append(
                        {
                            "file": fname,
                            "type": "d2l_xml_audit",
                            "reason": (
                                "Quiz availability window detected — "
                                "verify dates after Canvas date-shift"
                            ),
                            "evidence": " | ".join(parts),
                        }
                    )

            # ── Course-start-date advisory (always emitted) ─────────────────
            if all_news_dates:
                earliest = min(all_news_dates)[:10]
                latest = max(all_news_dates)[:10]
                evidence = (
                    f"No course offering date in IMSCC export. "
                    f"Announcement date range (proxy): {earliest} → {latest}. "
                    "Use Canvas Settings > Adjust Events and Due Dates with the "
                    "actual new course start date."
                )
            else:
                evidence = (
                    "No course offering date in IMSCC export and no announcement "
                    "dates found. Use Canvas Settings > Adjust Events and Due Dates "
                    "with the actual new course start date."
                )
            rows.append(
                {
                    "file": "news_d2l.xml",
                    "type": "d2l_xml_audit",
                    "reason": (
                        "Course start date not in D2L export — "
                        "set manually during Canvas import"
                    ),
                    "evidence": evidence,
                }
            )
    except Exception:
        pass
    return rows


def _audit_quiz_question_types(zip_path: Path) -> list[dict]:
    """Return one row per quiz that contains at-risk question types (P1/P2).

    Uses the same ``_RISK_TYPES`` reference from ``quiz_audit`` so the
    compatibility table stays in a single place.  Emits one row per affected
    quiz listing each at-risk type, its count, and the recommended action.
    Quizzes with only clean types are silently skipped.
    """
    rows: list[dict] = []
    try:
        with ZipFile(zip_path) as zf:
            names = zf.namelist()
            quiz_files = [
                n
                for n in names
                if re.match(r"quiz_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
            ]
            for fname in sorted(quiz_files):
                try:
                    content = zf.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                quiz_info = _parse_quiz_xml_file(fname, content)
                if not quiz_info.compatibility_flags:
                    continue
                # Separate P1 flags from P2 flags
                p1_flags = [
                    f for f in quiz_info.compatibility_flags if f["level"] == "P1"
                ]
                p2_flags = [
                    f for f in quiz_info.compatibility_flags if f["level"] == "P2"
                ]
                # Emit the highest-priority row for this quiz so the checklist
                # properly classifies it.  Include all flag summaries in evidence.
                level = "P1" if p1_flags else "P2"
                flag_summaries = ", ".join(
                    f"{f['type']} \u00d7{f['count']}"
                    for f in quiz_info.compatibility_flags
                )
                reason = (
                    f"New Quizzes question-type compatibility risk ({level}) — "
                    "manual rebuild required for unsupported question types"
                )
                evidence = (
                    f"quiz: {quiz_info.title} | at-risk types: {flag_summaries} | "
                    f"total questions: {quiz_info.question_count}"
                )
                rows.append(
                    {
                        "file": fname,
                        "type": "d2l_xml_audit",
                        "reason": reason,
                        "evidence": evidence,
                    }
                )
    except Exception:
        pass
    return rows


def _audit_quiz_bank_usage(zip_path: Path) -> list[dict]:
    """Return one row per quiz that uses questiondb-backed items or random question order."""
    rows: list[dict] = []
    try:
        report = _audit_quizzes(zip_path)
    except Exception:
        return rows

    for q in report.quizzes:
        evidence_parts: list[str] = [f'quiz: "{q.title}"']
        has_workflow_risk = False
        if q.questiondb_item_count:
            has_workflow_risk = True
            evidence_parts.append(
                f"questiondb-backed items: {q.questiondb_item_count}"
            )
        if q.random_question_order:
            has_workflow_risk = True
            evidence_parts.append("random question order: yes")
        if not has_workflow_risk:
            continue
        rows.append(
            {
                "file": q.quiz_file,
                "type": "d2l_xml_audit",
                "reason": (
                    "Question bank/randomization workflow detected — verify Item Banks and shuffle behavior in Canvas"
                ),
                "evidence": " | ".join(evidence_parts),
            }
        )
    return rows


def _audit_quiz_settings_inventory(zip_path: Path) -> list[dict]:
    """Return one row per quiz with a per-quiz settings inventory.

    Canvas New Quizzes requires manual re-entry of every quiz's time limit,
    attempt count, shuffle settings, and availability window — they are NOT
    preserved through the D2L QTI → Canvas import process.  This emits a P1
    row per quiz so the fix checklist surfaces all settings the ID must
    re-enter, even for quizzes that have no question-type compatibility risk.
    """
    rows: list[dict] = []
    try:
        report = _audit_quizzes(zip_path)
    except Exception:
        return rows
    for q in report.quizzes:
        # Build a human-readable settings summary
        parts: list[str] = [f'quiz: "{q.title}"']
        if q.time_limit_minutes is not None:
            enforced = " (enforced)" if q.enforce_time_limit else " (not enforced)"
            parts.append(f"time limit: {q.time_limit_minutes} min{enforced}")
        else:
            parts.append("time limit: none")
        att = "unlimited" if q.attempts_allowed == 0 else str(q.attempts_allowed)
        parts.append(f"attempts: {att}")
        parts.append(f"shuffle: {q.shuffle_type}")
        if q.has_availability_window:
            window_parts: list[str] = []
            if q.date_start:
                window_parts.append(f"start: {q.date_start[:10]}")
            if q.date_end:
                window_parts.append(f"end: {q.date_end[:10]}")
            if q.date_due:
                window_parts.append(f"due: {q.date_due[:10]}")
            parts.append("window: " + ", ".join(window_parts))
        rows.append(
            {
                "file": q.quiz_file,
                "type": "d2l_xml_audit",
                "reason": (
                    "Quiz settings inventory — re-enter in Canvas New Quizzes "
                    "after import (time limit, attempts, shuffle are not "
                    "preserved through QTI import)"
                ),
                "evidence": " | ".join(parts),
            }
        )
    return rows


def _append_xml_audit_rows_to_csv(zip_path: Path, csv_path: Path) -> None:
    """Append package/XML audit rows to the manual-review CSV."""
    rows = _collect_package_audit_rows(zip_path)
    _append_package_audit_rows_to_csv(rows, csv_path)


def _collect_package_audit_rows(zip_path: Path) -> list[dict]:
    """Collect high-signal source-package audit rows for the manual-review CSV."""
    rows: list[dict] = []
    rows.extend(_audit_course_alignment_docs(zip_path))
    rows.extend(_audit_file_organization_risks(zip_path))
    rows.extend(_audit_instructor_only_manifest_items(zip_path))
    rows.extend(_audit_quiz_release_conditions(zip_path))
    rows.extend(_audit_graded_discussions(zip_path))
    rows.extend(_audit_availability_windows(zip_path))
    rows.extend(_audit_gradebook_groups(zip_path))
    rows.extend(_audit_rubrics(zip_path))
    rows.extend(_audit_dropbox_folders(zip_path))
    rows.extend(_audit_unresolvable_grade_items(zip_path))
    rows.extend(_audit_date_shift_items(zip_path))
    rows.extend(_audit_quiz_settings_inventory(zip_path))
    rows.extend(_audit_quiz_question_types(zip_path))
    rows.extend(_audit_quiz_bank_usage(zip_path))
    rows.extend(_audit_quiz_embedded_media(zip_path))
    return rows


def _append_package_audit_rows_to_csv(rows: list[dict], csv_path: Path) -> None:
    """Append already-collected package audit rows to the manual-review CSV."""
    if not rows:
        return
    with csv_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "type", "reason", "evidence"])
        for row in rows:
            writer.writerow(row)


def _build_course_kickoff_summary(report: dict, package_audit_rows: list[dict]) -> dict:
    """Build a compact kickoff brief for the next manual migration pass."""
    signal_defs = [
        (
            "course_alignment_docs",
            "Course alignment docs",
            ("course alignment document detected",),
        ),
        ("rubrics", "Rubrics", ("d2l rubric detected",)),
        (
            "instructor_only_content",
            "Hidden/instructor-only content",
            ("hidden or instructor-only d2l content detected",),
        ),
        (
            "quiz_release_conditions",
            "Quiz gating/release conditions",
            ("quiz-based release condition detected",),
        ),
        (
            "quiz_question_type_risks",
            "Question-type rebuild risks",
            ("new quizzes question-type compatibility risk",),
        ),
        (
            "quiz_bank_randomization",
            "Question bank/randomization workflows",
            ("question bank/randomization workflow detected",),
        ),
        (
            "quiz_embedded_media",
            "Quiz embedded media",
            ("quiz question images/media detected",),
        ),
        (
            "quiz_settings_inventory",
            "Quiz settings inventories",
            ("quiz settings inventory",),
        ),
        (
            "dropbox_assignments",
            "Dropbox assignments",
            ("d2l dropbox assignment detected",),
        ),
        (
            "unresolvable_grade_items",
            "Unresolvable grade items",
            ("unresolvable grade item",),
        ),
        (
            "gradebook_group_rules",
            "Gradebook weight/drop rules",
            (
                "gradebook category with drop rule",
                "gradebook category weight",
            ),
        ),
        (
            "extra_credit_items",
            "Extra-credit items",
            ("bonus/extra-credit grade item detected",),
        ),
        (
            "file_organization_risks",
            "File organization risks",
            (
                "scattered d2l file organization detected",
                "duplicate content filenames across d2l folders detected",
            ),
        ),
    ]

    def _sanitize_evidence(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value or "").strip()
        return cleaned.replace(" | ", "; ")

    signal_rows: dict[str, list[dict]] = {}
    for key, _label, tokens in signal_defs:
        signal_rows[key] = [
            row
            for row in package_audit_rows
            if any(token in str(row.get("reason", "")).lower() for token in tokens)
        ]

    signals: list[dict] = []
    for key, label, _tokens in signal_defs:
        rows = signal_rows[key]
        sample_evidence: list[str] = []
        for row in rows:
            evidence = _sanitize_evidence(str(row.get("evidence", "")))
            if evidence and evidence not in sample_evidence:
                sample_evidence.append(evidence)
            if len(sample_evidence) >= 3:
                break
        signals.append(
            {
                "id": key,
                "label": label,
                "count": len(rows),
                "sample_evidence": sample_evidence,
            }
        )

    issue_summary = report.get("issue_summary", {})
    top_manual_review = [
        {
            "reason": str(row.get("reason", "")).strip(),
            "count": int(row.get("count", 0)),
        }
        for row in issue_summary.get("top_manual_review_reasons", [])[:5]
        if isinstance(row, dict) and str(row.get("reason", "")).strip()
    ]
    top_accessibility = [
        {
            "reason": str(row.get("reason", "")).strip(),
            "count": int(row.get("count", 0)),
        }
        for row in issue_summary.get("top_accessibility_reasons", [])[:5]
        if isinstance(row, dict) and str(row.get("reason", "")).strip()
    ]

    recommendations: list[str] = []
    if signal_rows["file_organization_risks"]:
        recommendations.append(
            "Preserve D2L file paths during migration. Treat Canvas Files cleanup as a post-import task, especially when duplicate filenames exist."
        )
    if signal_rows["course_alignment_docs"]:
        recommendations.append(
            "Use the course alignment document as the verification source for syllabus tables, module coverage, and assessment sequencing."
        )
    if signal_rows["rubrics"]:
        recommendations.append(
            "Plan a separate rubric pass. Canvas will need manual rubric recreation/attachment when D2L rubrics are present."
        )
    if signal_rows["quiz_release_conditions"]:
        recommendations.append(
            "Decide the Canvas gating strategy early. Recreate D2L release conditions with module prerequisites/requirements before final module cleanup."
        )
    if (
        signal_rows["quiz_question_type_risks"]
        or signal_rows["quiz_bank_randomization"]
        or signal_rows["quiz_embedded_media"]
        or signal_rows["quiz_settings_inventory"]
    ):
        recommendations.append(
            "Budget a dedicated New Quizzes pass to rebuild unsupported question types, item-bank logic, media placement, and quiz settings."
        )
    if (
        signal_rows["dropbox_assignments"]
        or signal_rows["unresolvable_grade_items"]
        or signal_rows["gradebook_group_rules"]
        or signal_rows["extra_credit_items"]
    ):
        recommendations.append(
            "Expect manual gradebook reconstruction. Verify assignment groups, weights, drop rules, extra credit, and any external-tool grade columns."
        )
    if signal_rows["instructor_only_content"]:
        recommendations.append(
            "Keep faculty-only or hidden D2L content unpublished after import and review it separately from student-facing modules."
        )
    if not recommendations:
        recommendations.append(
            "No package-specific high-signal risks were detected beyond the standard migration review. Proceed with the usual preflight and page review workflow."
        )

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_zip": report.get("input_zip", ""),
        "output_zip": report.get("output_zip", ""),
        "policy_profile_id": str(report.get("policy_profile", {}).get("id", "")),
        "automated_summary": dict(report.get("summary", {})),
        "signals": signals,
        "top_manual_review": top_manual_review,
        "top_accessibility": top_accessibility,
        "recommendations": recommendations,
    }


def _write_course_kickoff_summary(
    summary: dict, *, output_json_path: Path, output_markdown_path: Path
) -> None:
    output_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    automated = summary.get("automated_summary", {})
    lines = [
        "# Course Kickoff Summary",
        "",
        f"- Input zip: `{summary.get('input_zip', '')}`",
        f"- Output zip: `{summary.get('output_zip', '')}`",
        f"- Policy profile: `{summary.get('policy_profile_id', '')}`",
        f"- Generated (UTC): `{summary.get('generated_utc', '')}`",
        "",
        "## Migration Snapshot",
        "",
        f"- HTML files changed: {automated.get('html_files_changed', 0)}",
        f"- Manual review issues: {automated.get('manual_review_issues', 0)}",
        f"- Accessibility issues: {automated.get('accessibility_issues', 0)}",
        "",
        "## High-Signal Course Findings",
        "",
    ]

    nonzero_signals = [
        signal
        for signal in summary.get("signals", [])
        if isinstance(signal, dict) and int(signal.get("count", 0)) > 0
    ]
    if nonzero_signals:
        for signal in nonzero_signals:
            lines.append(f"- {signal.get('label', '')}: {signal.get('count', 0)}")
            samples = signal.get("sample_evidence", [])
            for sample in samples[:2]:
                lines.append(f"  - {sample}")
    else:
        lines.append("- No high-signal source-package findings detected.")

    lines.extend(["", "## Recommended Stance", ""])
    for item in summary.get("recommendations", []):
        lines.append(f"- {item}")

    top_manual = summary.get("top_manual_review", [])
    if top_manual:
        lines.extend(["", "## Top Automated Review Findings", ""])
        for row in top_manual:
            lines.append(
                f"- Manual: {row.get('count', 0)} × {row.get('reason', '')}"
            )

    top_accessibility = summary.get("top_accessibility", [])
    if top_accessibility:
        lines.extend(["", "## Top Accessibility Findings", ""])
        for row in top_accessibility:
            lines.append(
                f"- Accessibility: {row.get('count', 0)} × {row.get('reason', '')}"
            )

    lines.append("")
    output_markdown_path.write_text("\n".join(lines), encoding="utf-8")


def _write_manual_review_csv(file_results: list[FileResult], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["file", "type", "reason", "evidence"],
        )
        writer.writeheader()

        for result in file_results:
            for issue in result.manual_issues:
                writer.writerow(
                    {
                        "file": result.path,
                        "type": "manual_review",
                        "reason": issue.reason,
                        "evidence": issue.evidence,
                    }
                )
            for issue in result.a11y_issues:
                writer.writerow(
                    {
                        "file": result.path,
                        "type": "accessibility",
                        "reason": issue.reason,
                        "evidence": issue.evidence,
                    }
                )


def _write_preflight_checklist(
    report: dict,
    profile: PolicyProfile,
    output_path: Path,
    manual_review_csv: Path | None = None,
) -> None:
    summary = report["summary"]
    manual_counts: Counter[str] = Counter()
    a11y_counts: Counter[str] = Counter()
    for file_entry in report["files"]:
        for issue in file_entry.get("manual_review_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                manual_counts[reason] += 1
        for issue in file_entry.get("accessibility_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                a11y_counts[reason] += 1

    # Read D2L XML audit rows from the CSV (written after report is built)
    xml_audit_counts: Counter = Counter()
    if manual_review_csv and manual_review_csv.exists():
        with manual_review_csv.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if str(row.get("type", "")).strip() == "d2l_xml_audit":
                    reason = str(row.get("reason", "")).strip()
                    if reason:
                        xml_audit_counts[reason] += 1

    lines = [
        "# Migration Preflight Checklist",
        "",
        f"- Input zip: `{report['input_zip']}`",
        f"- Output zip: `{report['output_zip']}`",
        f"- Policy profile: `{profile.profile_id}`",
        f"- Profile description: {profile.description}",
        "",
        "## Glossary",
        "",
        "| Abbreviation | Meaning |",
        "| --- | --- |",
        "| ID | Instructional Designer |",
        "| IC | Introduction and Checklist page (Canvas page combining D2L Introduction/Objectives and Module Checklist) |",
        "| CAD | Course Alignment Document (linked from syllabus) |",
        "| LTI | Learning Tools Interoperability (external tool integration standard) |",
        "",
        "## Automated Summary",
        "",
        f"- HTML files scanned: {summary['html_files_scanned']}",
        f"- HTML files changed: {summary['html_files_changed']}",
    ]

    # Build breakdown parentheticals for manual review and a11y
    # Human-readable label for each fix_checklist category code
    _CATEGORY_LABELS: dict[str, str] = {
        "lti_quicklink_reconfiguration": "LTI QuickLink",
        "lti_embed_reconfiguration": "LTI embed",
        "rubric_import_setup": "D2L rubric",
        "d2l_media_library_migration": "D2L media library",
        "email_submission_workflow": "Email submission",
        "graded_discussion_setup": "Graded discussion",
        "assignment_availability_window": "Availability window",
        "gradebook_drop_rule_setup": "Gradebook drop rule",
        "gradebook_group_weights": "Gradebook weight",
        "extra_credit_setup": "Extra credit",
        "canvas_date_shift_setup": "Course start date",
        "quiz_settings_inventory": "Quiz settings",
        "new_quizzes_question_type_rebuild": "New Quizzes compat",
        "unresolvable_grade_item_setup": "Unresolvable grade item",
        "dropbox_assignment_setup": "Dropbox assignment",
        "graded_discussion_reconnect": "Graded discussion",  # legacy alias
        "layout_css_rendering_review": "Layout CSS",
        "embedded_iframe_review": "Embedded iframe",
        "a11y_video_captions": "Video captions",
        "instructor_note_cleanup": "Instructor note",
        "template_placeholder_cleanup": "Template placeholder",
        "broken_link_review": "Broken link",
        "module_checklist_closer_missing": "Module Checklist closer",
    }

    def _top_reasons_summary(counter: Counter, limit: int = 3) -> str:
        # Aggregate raw reason strings by their fix_checklist category so that
        # many unique LTI QuickLink reason strings (each with a different title)
        # count as one group rather than appearing as N individual entries.
        category_counts: Counter = Counter()
        for reason, count in counter.items():
            try:
                _, cat, _, _ = _map_manual_review_group("manual_review", reason)
            except Exception:
                cat = ""
            label = _CATEGORY_LABELS.get(cat) if cat else None
            if label is None:
                # Fall back to a shortened form of the raw reason
                short = re.split(r"\s[—\-]\s", reason)[0].strip()
                if len(short) > 40:
                    short = short[:38].rstrip() + "…"
                label = short
            category_counts[label] += count

        parts = []
        for label, count in category_counts.most_common(limit):
            parts.append(f"{count} {label}")
        remainder = sum(category_counts.values()) - sum(
            v for _, v in category_counts.most_common(limit)
        )
        if remainder > 0:
            parts.append(f"{remainder} other")
        return f" ({', '.join(parts)})" if parts else ""

    manual_total = summary["manual_review_issues"]
    a11y_total = summary["accessibility_issues"]
    manual_breakdown = _top_reasons_summary(manual_counts) if manual_total else ""
    a11y_breakdown = _top_reasons_summary(a11y_counts) if a11y_total else ""

    lines.extend(
        [
            f"- Manual review issues: {manual_total}{manual_breakdown}",
            f"- Accessibility issues: {a11y_total}{a11y_breakdown}",
            "",
            "## Required Verifications Before Release",
            "",
        ]
    )

    if profile.preflight_items:
        for item in profile.preflight_items:
            lines.append(f"- [ ] {item}")
    else:
        lines.append(
            "- [ ] Review manual findings and accessibility findings before release."
        )

    lines.extend(["", "## Findings-Based Follow-Up", ""])
    if manual_counts:
        lines.append("### Manual Review Reasons")
        lines.append("")

        # --- Collect LTI QuickLink reasons to emit as a single grouped entry ---
        quicklink_entries: list[tuple[str, str]] = []  # [(title, rcode), ...]
        non_quicklink_counts: Counter = Counter()
        for reason, count in manual_counts.items():
            try:
                _p, cat, _o, _a = _map_manual_review_group("manual_review", reason)
            except Exception:
                cat = ""
            if cat == "lti_quicklink_reconfiguration":
                # Extract title (between '— '' and ' [rCode:') and rCode
                title_match = re.search(
                    r"—\s+'([^']+)'\s+\[rcode:", reason, re.IGNORECASE
                )
                rcode_match = re.search(r"\[rcode:\s*([^\]]+)\]", reason, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else reason
                rcode = rcode_match.group(1).strip() if rcode_match else ""
                # count copies (usually 1 per rCode, but honour count for safety)
                quicklink_entries.extend([(title, rcode)] * count)
            else:
                non_quicklink_counts[reason] += count

        # Emit the grouped LTI QuickLink block at the top (most-common-first among others)
        if quicklink_entries:
            total_ql = len(quicklink_entries)
            lines.append(
                f"- [ ] ({total_ql}) LTI tool embed{'s' if total_ql > 1 else ''} "
                "(D2L QuickLink) — reconfigure as Canvas LTI external tool"
                f"{'s' if total_ql > 1 else ''} after migration"
            )
            lines.append("  - **Owner:** Faculty/Course Coordinator")
            lines.append(
                "  - **Action:** These D2L LTI quick-links will NOT resolve after migration. "
                "For each item: (1) confirm with your Canvas admin that the LTI tool is "
                "configured in Canvas (Settings \u2192 Apps); (2) open the Canvas page, delete "
                "the broken embed, and re-insert the tool using the Rich Content Editor \u2192 "
                "Apps picker. The original D2L rCode URLs are institution-specific and cannot "
                "be reused in Canvas."
                if total_ql > 1
                else "  - **Action:** This D2L LTI quick-link will NOT resolve after migration. "
                "(1) Confirm with your Canvas admin that the LTI tool is configured in Canvas "
                "(Settings \u2192 Apps); (2) open the Canvas page, delete the broken embed, and "
                "re-insert the tool using the Rich Content Editor \u2192 Apps picker."
            )
            lines.append("")
            lines.append("  | # | Assignment Title | D2L rCode |")
            lines.append("  |---|---|---|")
            for idx, (title, rcode) in enumerate(quicklink_entries, 1):
                safe_title = title.replace("|", "\\|")
                lines.append(f"  | {idx} | {safe_title} | {rcode} |")
            lines.append("")

        # Emit remaining manual review reasons
        for reason, count in non_quicklink_counts.most_common():
            lines.append(f"- [ ] ({count}) {reason}")
            try:
                _p, _cat, owner, action = _map_manual_review_group(
                    "manual_review", reason
                )
                lines.append(f"  - **Owner:** {owner}")
                lines.append(f"  - **Action:** {action}")
            except Exception:
                pass
        lines.append("")
    if a11y_counts:
        lines.append("### Accessibility Reasons")
        lines.append("")
        for reason, count in a11y_counts.most_common():
            lines.append(f"- [ ] ({count}) {reason}")
            try:
                _p, _cat, owner, action = _map_manual_review_group(
                    "accessibility", reason
                )
                lines.append(f"  - **Owner:** {owner}")
                lines.append(f"  - **Action:** {action}")
            except Exception:
                pass
        lines.append("")
    if xml_audit_counts:
        lines.append("### D2L XML Audit Items (Require Canvas Configuration)")
        lines.append("")
        for reason, count in xml_audit_counts.most_common():
            lines.append(f"- [ ] ({count}) {reason}")
            try:
                _p, _cat, owner, action = _map_manual_review_group(
                    "d2l_xml_audit", reason
                )
                lines.append(f"  - **Owner:** {owner}")
                lines.append(f"  - **Action:** {action}")
            except Exception:
                pass
        lines.append("")
    if not manual_counts and not a11y_counts and not xml_audit_counts:
        lines.append("- [ ] No issues flagged by automation.")
        lines.append("")

    reference_alignment = report.get("reference_alignment")
    if isinstance(reference_alignment, dict):
        lines.extend(["## Reference Alignment Follow-Up", ""])
        critical_gaps = int(reference_alignment.get("critical_gap_count", 0))
        if critical_gaps > 0:
            lines.append(
                f"- [ ] Resolve `{critical_gaps}` critical instruction gap(s) identified in reference audit."
            )
        action_needed = int(
            reference_alignment.get("best_practice_action_needed_count", 0)
        )
        if action_needed > 0:
            lines.append(
                f"- [ ] Add migration rule/check coverage for `{action_needed}` best-practice topic(s)."
            )
        if not bool(
            reference_alignment.get("module_checklist_required_closer_present", True)
        ):
            lines.append(
                "- [ ] Update template/rules to enforce Module Checklist closing reminder."
            )
        placeholders = reference_alignment.get(
            "template_placeholder_patterns_detected", []
        )
        if isinstance(placeholders, list) and placeholders:
            lines.append(
                "- [ ] Verify unresolved template placeholders are cleaned up before release."
            )
        if critical_gaps == 0 and action_needed == 0:
            lines.append("- [ ] Reference audit shows no unresolved governance gaps.")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def run_migration(
    input_zip: Path,
    output_dir: Path,
    rules_path: Path,
    policy_profile_id: str = "strict",
    policy_profiles_path: Path = Path("rules/policy_profiles.json"),
    reference_audit_json: Path | None = None,
    best_practice_enforcer: bool = False,
    template_package: Path | None = None,
    template_alias_map_json: Path | None = None,
    math_handling: str = "preserve-semantic",
    accordion_handling: str = "smart",
    accordion_alignment: str = "left",
    accordion_flatten_hints: tuple[str, ...] = (),
    accordion_details_hints: tuple[str, ...] = (),
    apply_template_module_structure: bool = True,
    apply_template_visual_standards: bool = True,
    apply_template_color_standards: bool = True,
    apply_template_divider_standards: bool = True,
    image_layout_mode: str = "safe-block",
    template_merge: bool = False,
    full_template_shell: bool = False,
    seeded_starter_course: bool = False,
    intro_checklist_handling: str = "rebuild-when-confident",
    learning_activities_handling: str = "preserve",
) -> MigrationOutput:
    if full_template_shell and not template_merge:
        raise ValueError(
            "Full template shell requires template merge to be enabled."
        )
    if full_template_shell and template_package is None:
        raise ValueError(
            "Full template shell requires a template package."
        )
    if full_template_shell and seeded_starter_course:
        raise ValueError(
            "Seeded starter course mode cannot be combined with full template shell packaging."
        )

    rules = load_rules(rules_path)
    policy_profile = get_policy_profile(policy_profile_id, policy_profiles_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_zip = output_dir / f"{input_zip.stem}.canvas-ready.zip"
    report_json = output_dir / f"{input_zip.stem}.migration-report.json"
    report_markdown = output_dir / f"{input_zip.stem}.migration-report.md"
    manual_review_csv = output_dir / f"{input_zip.stem}.manual-review.csv"
    preflight_checklist = output_dir / f"{input_zip.stem}.preflight-checklist.md"
    quiz_audit_json = output_dir / f"{input_zip.stem}.quiz-audit.json"
    quiz_audit_md = output_dir / f"{input_zip.stem}.quiz-audit.md"
    kickoff_summary_json = output_dir / f"{input_zip.stem}.kickoff-summary.json"
    kickoff_summary_md = output_dir / f"{input_zip.stem}.kickoff-summary.md"
    template_overlay_report_json: Path | None = None
    template_overlay_report_payload: dict | None = None
    template_overlay_context = None
    template_materialization_summary: dict | None = None
    file_layout_summary: dict | None = None
    if template_package is not None:
        template_overlay_context = build_template_overlay_context(
            TemplateOverlayConfig(
                template_package=template_package,
                alias_map_json_path=template_alias_map_json,
                apply_visual_standards=apply_template_visual_standards,
                apply_color_standards=apply_template_visual_standards
                and apply_template_color_standards,
                apply_divider_standards=apply_template_visual_standards
                and apply_template_divider_standards,
                image_layout_mode=image_layout_mode,
                use_template_web_resources=bool(full_template_shell),
            )
        )

    with tempfile.TemporaryDirectory(prefix="lms-migration-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        unpack_dir = temp_dir / "unpacked"
        unpack_dir.mkdir(parents=True, exist_ok=True)

        with ZipFile(input_zip, "r") as zf:
            zf.extractall(unpack_dir)

        if template_overlay_context is not None:
            if seeded_starter_course and not full_template_shell:
                template_materialization_summary = {
                    "asset_dir": "starter-course-assets",
                    "assets_copied": 0,
                    "assets_skipped_collisions": 0,
                    "assets_skipped_existing": 0,
                    "copied_paths_sample": [],
                    "skipped_materialization": True,
                    "mode": "seeded-starter-course",
                }
            else:
                template_materialization_summary = materialize_template_assets(
                    context=template_overlay_context,
                    destination_root=unpack_dir,
                )

        moved_course_files, file_layout_summary = _normalize_loose_course_content_layout(
            unpack_dir,
            course_content_root=_COURSE_CONTENT_ROOT_FOLDER,
        )
        file_layout_summary.update(
            _rewrite_manifest_hrefs_for_moved_files(unpack_dir, moved_course_files)
        )
        file_layout_summary.update(_trim_unreferenced_package_files(unpack_dir))

        manifest_found = any(unpack_dir.rglob("imsmanifest.xml"))

        html_files = [
            path
            for path in unpack_dir.rglob("*")
            if path.suffix.lower() in {".html", ".htm"}
        ]
        available_paths = {
            str(path.relative_to(unpack_dir).as_posix())
            for path in unpack_dir.rglob("*")
            if path.is_file()
        }
        file_results: list[FileResult] = []
        template_overlay_file_summaries: list[dict] = []
        sanitizer_policy = CanvasSanitizerPolicy(
            sanitize_brightspace_assets=policy_profile.sanitize_brightspace_assets,
            neutralize_legacy_d2l_links=policy_profile.neutralize_legacy_d2l_links,
            use_alt_text_for_removed_template_images=policy_profile.use_alt_text_for_removed_template_images,
            repair_missing_local_references=policy_profile.repair_missing_local_references,
            normalize_divider_styling=bool(apply_template_divider_standards),
            math_handling=math_handling,
            accordion_handling=accordion_handling,
            accordion_summary_alignment=accordion_alignment,
            accordion_flatten_hints=accordion_flatten_hints,
            accordion_details_hints=accordion_details_hints,
        )

        for html_file in html_files:
            original = _read_text(html_file)
            updated = original
            applied_changes: list[AppliedChange] = []
            relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())

            updated, replacement_changes = apply_replacements(
                updated, rules.replacements
            )
            applied_changes.extend(replacement_changes)

            updated, rewrite_changes = apply_link_rewrites(updated, rules.link_rewrites)
            applied_changes.extend(rewrite_changes)

            overlay_issues: list[ManualReviewIssue] = []
            if template_overlay_context is not None:
                updated, overlay_changes, overlay_issues, overlay_file_summary = (
                    apply_template_overlay(
                        updated,
                        file_path=relative_html_path,
                        context=template_overlay_context,
                    )
                )
                applied_changes.extend(overlay_changes)
                template_overlay_file_summaries.append(overlay_file_summary)

            updated, banner_changes = apply_banner_rule(updated, rules.banner)
            applied_changes.extend(banner_changes)

            updated, sanitizer_changes = apply_canvas_sanitizer(
                updated,
                policy=sanitizer_policy,
                file_path=relative_html_path,
            )
            applied_changes.extend(sanitizer_changes)

            if sanitizer_policy.repair_missing_local_references:
                updated, repaired_ref_changes = repair_missing_local_references(
                    updated,
                    file_path=relative_html_path,
                    available_paths=available_paths,
                    keep_alt_text_for_missing_images=sanitizer_policy.use_alt_text_for_removed_template_images,
                )
                applied_changes.extend(repaired_ref_changes)

            best_practice_issues: list[ManualReviewIssue] = []
            if best_practice_enforcer:
                updated, best_practice_changes, best_practice_issues = (
                    apply_best_practice_enforcer(
                        updated,
                        file_path=relative_html_path,
                        policy=BestPracticeEnforcerPolicy(
                            enabled=True,
                            enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                            ensure_external_links_new_tab=True,
                        ),
                    )
                )
                applied_changes.extend(best_practice_changes)

            if _is_intro_objectives_page(relative_html_path):
                topic_phrase_hits = len(re.findall(r"(?i)\bthis topic\b", updated))
                topic_ref_hits = len(
                    re.findall(r"(?i)\btopic\s*0*\d+\s*(?:\||-|:)\s*", updated)
                )
                topic_phrase_hits += len(re.findall(r"(?i)\bthe topic\b", updated))
                normalized_intro = _normalize_module_checklist_wording(updated)
                if normalized_intro != updated:
                    updated = normalized_intro
                    applied_changes.append(
                        AppliedChange(
                            category="structure",
                            description='Normalized "topic" wording to "module" on Introduction and Objectives pages',
                            count=max(1, topic_phrase_hits + topic_ref_hits),
                        )
                    )

            # Inject a top-of-page accent divider for pages that lack an
            # icon heading (those pages miss the border-bottom on the h2).
            if template_overlay_context is not None:
                updated, accent_hr_changes = inject_accent_divider(updated)
                applied_changes.extend(accent_hr_changes)

            manual_issues = detect_manual_review_issues(
                updated, rules.manual_review_triggers
            )
            manual_issues.extend(overlay_issues)
            manual_issues.extend(best_practice_issues)
            if policy_profile.template_checks_enabled:
                manual_issues.extend(
                    check_template_heuristics(
                        updated,
                        file_path=relative_html_path,
                        policy=TemplateCheckPolicy(
                            check_instructor_notes=policy_profile.check_instructor_notes,
                            check_template_placeholders=policy_profile.check_template_placeholders,
                            check_legacy_quiz_wording=policy_profile.check_legacy_quiz_wording,
                            require_mc_closing_bullet=policy_profile.require_mc_closing_bullet,
                        ),
                    )
                )
            manual_issues.extend(detect_layout_breaking_issues(updated))
            # Detect LTI and media-library issues on the ORIGINAL content: the
            # sanitizer neutralises quickLink hrefs to '#' before we get here.
            manual_issues.extend(detect_lti_embed_issues(original))
            manual_issues.extend(detect_iframe_issues(updated))
            manual_issues.extend(detect_d2l_media_library_embeds(original))
            manual_issues.extend(detect_email_submission_issues(original))
            a11y_issues = check_accessibility_heuristics(updated)

            changed = updated != original
            if changed:
                _write_text(html_file, updated)

            file_results.append(
                FileResult(
                    path=relative_html_path,
                    changed=changed,
                    applied_changes=applied_changes,
                    manual_issues=manual_issues,
                    a11y_issues=a11y_issues,
                )
            )

        d2l_xml_files = [
            path for path in unpack_dir.rglob("*_d2l.xml") if path.is_file()
        ]
        for d2l_xml_file in d2l_xml_files:
            original_xml = _read_text(d2l_xml_file)
            updated_xml = original_xml
            xml_changes: list[AppliedChange] = []

            if sanitizer_policy.neutralize_legacy_d2l_links:
                (
                    updated_xml,
                    rewritten_quicklink_xml_links,
                    neutralized_xml_links,
                ) = neutralize_legacy_d2l_hrefs_in_markup(updated_xml)
                if rewritten_quicklink_xml_links:
                    xml_changes.append(
                        AppliedChange(
                            category="sanitizer",
                            description="Converted D2L quickLink coursefile links in D2L XML payloads to package-relative file references",
                            count=rewritten_quicklink_xml_links,
                        )
                    )
                if neutralized_xml_links:
                    xml_changes.append(
                        AppliedChange(
                            category="sanitizer",
                            description="Neutralized legacy D2L links in D2L XML content payloads",
                            count=neutralized_xml_links,
                        )
                    )

            xml_changed = updated_xml != original_xml
            if xml_changed:
                _write_text(d2l_xml_file, updated_xml)

            if xml_changes or xml_changed:
                file_results.append(
                    FileResult(
                        path=str(d2l_xml_file.relative_to(unpack_dir).as_posix()),
                        changed=xml_changed,
                        applied_changes=xml_changes,
                        manual_issues=[],
                        a11y_issues=[],
                    )
                )

        manifest_paths = [
            path for path in unpack_dir.rglob("imsmanifest.xml") if path.is_file()
        ]
        for manifest_path in manifest_paths:
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            manifest_changed = False
            relative_manifest_path = str(
                manifest_path.relative_to(unpack_dir).as_posix()
            )

            for item in root.iter():
                if _local_name(item.tag) != "item":
                    continue
                description = item.attrib.get("description", "")
                if "<" not in description:
                    continue

                title_text = ""
                for child in list(item):
                    if _local_name(child.tag) == "title":
                        title_text = (child.text or "").strip()
                        break

                original = description
                updated = original
                applied_changes = []

                updated, duplicate_title_count = _remove_leading_duplicate_title_block(
                    updated, title_text
                )
                if duplicate_title_count:
                    applied_changes.append(
                        AppliedChange(
                            category="sanitizer",
                            description="Removed duplicate in-body heading/paragraph that repeated the Canvas page title",
                            count=duplicate_title_count,
                        )
                    )

                updated, replacement_changes = apply_replacements(
                    updated, rules.replacements
                )
                applied_changes.extend(replacement_changes)

                updated, rewrite_changes = apply_link_rewrites(
                    updated, rules.link_rewrites
                )
                applied_changes.extend(rewrite_changes)

                overlay_issues = []
                if template_overlay_context is not None:
                    updated, overlay_changes, overlay_issues, overlay_file_summary = (
                        apply_template_overlay(
                            updated,
                            file_path=f"{relative_manifest_path}::item[{title_text or item.attrib.get('identifier', '')}]",
                            context=template_overlay_context,
                        )
                    )
                    applied_changes.extend(overlay_changes)
                    template_overlay_file_summaries.append(overlay_file_summary)

                updated, sanitizer_changes = apply_canvas_sanitizer(
                    updated,
                    policy=sanitizer_policy,
                    file_path=f"{relative_manifest_path}::item[{title_text or item.attrib.get('identifier', '')}]",
                )
                applied_changes.extend(sanitizer_changes)

                if sanitizer_policy.repair_missing_local_references:
                    updated, repaired_ref_changes = repair_missing_local_references(
                        updated,
                        file_path=relative_manifest_path,
                        available_paths=available_paths,
                        keep_alt_text_for_missing_images=sanitizer_policy.use_alt_text_for_removed_template_images,
                    )
                    applied_changes.extend(repaired_ref_changes)

                manifest_best_practice_issues: list[ManualReviewIssue] = []
                if best_practice_enforcer:
                    updated, best_practice_changes, manifest_best_practice_issues = (
                        apply_best_practice_enforcer(
                            updated,
                            file_path=relative_manifest_path,
                            policy=BestPracticeEnforcerPolicy(
                                enabled=True,
                                enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                                ensure_external_links_new_tab=True,
                            ),
                        )
                    )
                    applied_changes.extend(best_practice_changes)

                manual_issues = detect_manual_review_issues(
                    updated, rules.manual_review_triggers
                )
                manual_issues.extend(overlay_issues)
                manual_issues.extend(manifest_best_practice_issues)
                if policy_profile.template_checks_enabled:
                    manual_issues.extend(
                        check_template_heuristics(
                            updated,
                            file_path=relative_manifest_path,
                            policy=TemplateCheckPolicy(
                                check_instructor_notes=policy_profile.check_instructor_notes,
                                check_template_placeholders=policy_profile.check_template_placeholders,
                                check_legacy_quiz_wording=policy_profile.check_legacy_quiz_wording,
                                require_mc_closing_bullet=policy_profile.require_mc_closing_bullet,
                            ),
                        )
                    )
                manual_issues.extend(detect_layout_breaking_issues(updated))
                # Detect on original: sanitizer neutralises quickLink hrefs.
                manual_issues.extend(detect_lti_embed_issues(original))
                manual_issues.extend(detect_iframe_issues(updated))
                manual_issues.extend(detect_d2l_media_library_embeds(original))
                manual_issues.extend(detect_email_submission_issues(original))
                a11y_issues = check_accessibility_heuristics(updated)

                changed = updated != original
                if changed:
                    item.set("description", updated)
                    manifest_changed = True

                entry_label = title_text or item.attrib.get("identifier", "")
                file_results.append(
                    FileResult(
                        path=f"{relative_manifest_path}::item[{entry_label}]",
                        changed=changed,
                        applied_changes=applied_changes,
                        manual_issues=manual_issues,
                        a11y_issues=a11y_issues,
                    )
                )

            topic_title_renamed = 0
            topic_description_merged = 0
            resource_hrefs = _resource_href_map(root)
            intro_title_candidates = {
                "introduction and objectives",
                "introduction & objectives",
                "intro and objectives",
            }
            for organization in [
                element
                for element in root.iter()
                if _local_name(element.tag) == "organization"
            ]:
                top_level_items = [
                    child
                    for child in list(organization)
                    if _local_name(child.tag) == "item"
                ]
                for module_item in top_level_items:
                    title_element, module_title = _extract_item_title(module_item)
                    if title_element is None or not module_title:
                        continue
                    match = _TOPIC_MODULE_TITLE_RE.match(module_title)
                    if match is None:
                        continue

                    module_number = int(match.group("number"))
                    module_label = match.group("label").strip()
                    expected_title = f"Module {module_number}: {module_label}"
                    if expected_title != module_title:
                        title_element.text = expected_title
                        manifest_changed = True
                        topic_title_renamed += 1

                    module_description = (
                        module_item.attrib.get("description") or ""
                    ).strip()
                    if not module_description or "<" not in module_description:
                        continue
                    module_description = _normalize_module_checklist_wording(
                        module_description
                    )

                    intro_item: ET.Element | None = None
                    for child_item in [
                        child
                        for child in list(module_item)
                        if _local_name(child.tag) == "item"
                    ]:
                        _, child_title = _extract_item_title(child_item)
                        normalized_child_title = _normalize_compare_text(child_title)
                        if normalized_child_title in intro_title_candidates:
                            intro_item = child_item
                            break
                    if intro_item is None:
                        continue

                    intro_identifier = (
                        intro_item.attrib.get("identifierref") or ""
                    ).strip()
                    intro_href = resource_hrefs.get(intro_identifier, "")
                    if not intro_href:
                        continue

                    intro_html_path = manifest_path.parent / Path(
                        intro_href.replace("\\", "/")
                    )
                    if not intro_html_path.exists() or not intro_html_path.is_file():
                        continue

                    intro_original = _read_text(intro_html_path)
                    intro_normalized = _normalize_fragment_text(intro_original)
                    module_normalized = _normalize_fragment_text(module_description)
                    if module_normalized and module_normalized in intro_normalized:
                        module_item.attrib.pop("description", None)
                        manifest_changed = True
                        topic_description_merged += 1
                        continue

                    intro_updated = _append_html_fragment(
                        intro_original, module_description
                    )
                    intro_applied_changes: list[AppliedChange] = [
                        AppliedChange(
                            category="structure",
                            description="Moved module learning objectives/checklist block into Introduction and Objectives page",
                            count=1,
                        )
                    ]
                    intro_overlay_issues: list[ManualReviewIssue] = []
                    intro_relative_path = str(
                        intro_html_path.relative_to(unpack_dir).as_posix()
                    )

                    if template_overlay_context is not None:
                        (
                            intro_updated,
                            intro_overlay_changes,
                            intro_overlay_issues,
                            intro_overlay_file_summary,
                        ) = apply_template_overlay(
                            intro_updated,
                            file_path=intro_relative_path,
                            context=template_overlay_context,
                        )
                        intro_applied_changes.extend(intro_overlay_changes)
                        template_overlay_file_summaries.append(
                            intro_overlay_file_summary
                        )

                    intro_updated, intro_sanitizer_changes = apply_canvas_sanitizer(
                        intro_updated,
                        policy=sanitizer_policy,
                        file_path=intro_relative_path,
                    )
                    intro_applied_changes.extend(intro_sanitizer_changes)

                    if sanitizer_policy.repair_missing_local_references:
                        intro_updated, intro_repaired_ref_changes = (
                            repair_missing_local_references(
                                intro_updated,
                                file_path=intro_relative_path,
                                available_paths=available_paths,
                                keep_alt_text_for_missing_images=sanitizer_policy.use_alt_text_for_removed_template_images,
                            )
                        )
                        intro_applied_changes.extend(intro_repaired_ref_changes)

                    intro_best_practice_issues: list[ManualReviewIssue] = []
                    if best_practice_enforcer:
                        (
                            intro_updated,
                            intro_best_practice_changes,
                            intro_best_practice_issues,
                        ) = apply_best_practice_enforcer(
                            intro_updated,
                            file_path=intro_relative_path,
                            policy=BestPracticeEnforcerPolicy(
                                enabled=True,
                                enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                                ensure_external_links_new_tab=True,
                            ),
                        )
                        intro_applied_changes.extend(intro_best_practice_changes)

                    if intro_updated != intro_original:
                        _write_text(intro_html_path, intro_updated)
                    module_item.attrib.pop("description", None)
                    manifest_changed = True
                    topic_description_merged += 1
                    intro_manual_issues = detect_manual_review_issues(
                        intro_updated,
                        rules.manual_review_triggers,
                    )
                    intro_manual_issues.extend(intro_overlay_issues)
                    intro_manual_issues.extend(intro_best_practice_issues)
                    if policy_profile.template_checks_enabled:
                        intro_manual_issues.extend(
                            check_template_heuristics(
                                intro_updated,
                                file_path=intro_relative_path,
                                policy=TemplateCheckPolicy(
                                    check_instructor_notes=policy_profile.check_instructor_notes,
                                    check_template_placeholders=policy_profile.check_template_placeholders,
                                    check_legacy_quiz_wording=policy_profile.check_legacy_quiz_wording,
                                    require_mc_closing_bullet=policy_profile.require_mc_closing_bullet,
                                ),
                            )
                        )
                    intro_manual_issues.extend(
                        detect_layout_breaking_issues(intro_updated)
                    )
                    # Detect on original: sanitizer neutralises quickLink hrefs.
                    intro_manual_issues.extend(detect_lti_embed_issues(intro_original))
                    intro_manual_issues.extend(detect_iframe_issues(intro_updated))
                    intro_manual_issues.extend(
                        detect_email_submission_issues(intro_original)
                    )
                    intro_a11y_issues = check_accessibility_heuristics(intro_updated)

                    _upsert_file_result(
                        file_results,
                        FileResult(
                            path=intro_relative_path,
                            changed=True,
                            applied_changes=intro_applied_changes,
                            manual_issues=intro_manual_issues,
                            a11y_issues=intro_a11y_issues,
                        ),
                        merge_applied_changes=True,
                    )

            if topic_title_renamed:
                file_results.append(
                    FileResult(
                        path=f"{relative_manifest_path}::organization",
                        changed=True,
                        applied_changes=[
                            AppliedChange(
                                category="structure",
                                description='Renamed module titles from "Topic N | ..." to "Module N: ..."',
                                count=topic_title_renamed,
                            )
                        ],
                        manual_issues=[],
                        a11y_issues=[],
                    )
                )

            if topic_description_merged:
                file_results.append(
                    FileResult(
                        path=f"{relative_manifest_path}::organization",
                        changed=True,
                        applied_changes=[
                            AppliedChange(
                                category="structure",
                                description="Cleared migrated module description blocks after merging into Introduction and Objectives pages",
                                count=topic_description_merged,
                            )
                        ],
                        manual_issues=[],
                        a11y_issues=[],
                    )
                )

            if apply_template_module_structure:
                manifest_item_identifiers = {
                    (element.attrib.get("identifier") or "").strip()
                    for element in root.iter()
                    if _local_name(element.tag) == "item"
                    and (element.attrib.get("identifier") or "").strip()
                }
                top_level_renames = 0
                child_title_renames = 0
                delimiter_title_renames = 0
                reordered_modules = 0
                inserted_subheaders = 0
                for organization in [
                    element
                    for element in root.iter()
                    if _local_name(element.tag) == "organization"
                ]:
                    (
                        org_top_level_renames,
                        org_child_title_renames,
                        org_delimiter_title_renames,
                        org_reordered_modules,
                        org_inserted_subheaders,
                    ) = _apply_template_module_structure_to_organization(
                        organization,
                        existing_identifiers=manifest_item_identifiers,
                        preserve_template_shell_modules=template_package is not None,
                    )
                    top_level_renames += org_top_level_renames
                    child_title_renames += org_child_title_renames
                    delimiter_title_renames += org_delimiter_title_renames
                    reordered_modules += org_reordered_modules
                    inserted_subheaders += org_inserted_subheaders
                delimiter_title_renames += _normalize_manifest_item_title_delimiters(
                    root
                )

                if top_level_renames:
                    manifest_changed = True
                    file_results.append(
                        FileResult(
                            path=f"{relative_manifest_path}::organization",
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description=(
                                        "Staged D2L overview/instructor modules for manual placement so template shell modules stay intact"
                                        if template_package is not None
                                        else 'Aligned top-level module names to template conventions (e.g., "Start Here", "Instructor Module")'
                                    ),
                                    count=top_level_renames,
                                )
                            ],
                            manual_issues=[],
                            a11y_issues=[],
                        )
                    )
                if child_title_renames:
                    manifest_changed = True
                    file_results.append(
                        FileResult(
                            path=f"{relative_manifest_path}::organization",
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description="Aligned numbered module item titles to template naming conventions",
                                    count=child_title_renames,
                                )
                            ],
                            manual_issues=[],
                            a11y_issues=[],
                        )
                    )
                if delimiter_title_renames:
                    manifest_changed = True
                    file_results.append(
                        FileResult(
                            path=f"{relative_manifest_path}::organization",
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description="Normalized legacy pipe-delimited page titles to Canvas colon-style naming",
                                    count=delimiter_title_renames,
                                )
                            ],
                            manual_issues=[],
                            a11y_issues=[],
                        )
                    )
                if reordered_modules:
                    manifest_changed = True
                    file_results.append(
                        FileResult(
                            path=f"{relative_manifest_path}::organization",
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description="Applied template module section structure (Overview / Activities / Review)",
                                    count=reordered_modules,
                                ),
                                AppliedChange(
                                    category="structure",
                                    description="Inserted template module section subheaders",
                                    count=inserted_subheaders,
                                ),
                            ],
                            manual_issues=[],
                            a11y_issues=[],
                        )
                    )

            if manifest_changed:
                tree.write(manifest_path, encoding="utf-8", xml_declaration=True)

        final_html_files = sorted(unpack_dir.rglob("*.html"))

        if best_practice_enforcer:
            for html_file in final_html_files:
                relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())
                original = _read_text(html_file)
                updated, final_best_practice_changes, final_best_practice_issues = (
                    apply_best_practice_enforcer(
                        original,
                        file_path=relative_html_path,
                        policy=BestPracticeEnforcerPolicy(
                            enabled=True,
                            enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                            ensure_external_links_new_tab=True,
                        ),
                    )
                )
                if (
                    not final_best_practice_changes
                    and not final_best_practice_issues
                    and updated == original
                ):
                    continue

                if updated != original:
                    _write_text(html_file, updated)

                final_manual_issues = detect_manual_review_issues(
                    updated,
                    rules.manual_review_triggers,
                )
                final_manual_issues.extend(final_best_practice_issues)
                if policy_profile.template_checks_enabled:
                    final_manual_issues.extend(
                        check_template_heuristics(
                            updated,
                            file_path=relative_html_path,
                            policy=TemplateCheckPolicy(
                                check_instructor_notes=policy_profile.check_instructor_notes,
                                check_template_placeholders=policy_profile.check_template_placeholders,
                                check_legacy_quiz_wording=policy_profile.check_legacy_quiz_wording,
                                require_mc_closing_bullet=policy_profile.require_mc_closing_bullet,
                            ),
                        )
                    )
                final_manual_issues.extend(detect_layout_breaking_issues(updated))
                # Detect on original: sanitizer neutralises quickLink hrefs.
                final_manual_issues.extend(detect_lti_embed_issues(original))
                final_manual_issues.extend(detect_iframe_issues(updated))
                final_manual_issues.extend(detect_email_submission_issues(original))
                final_a11y_issues = check_accessibility_heuristics(updated)
                _upsert_file_result(
                    file_results,
                    FileResult(
                        path=relative_html_path,
                        changed=updated != original,
                        applied_changes=final_best_practice_changes,
                        manual_issues=final_manual_issues,
                        a11y_issues=final_a11y_issues,
                    ),
                    merge_applied_changes=True,
                )

        if template_overlay_context is not None:
            for html_file in final_html_files:
                relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())
                original = _read_text(html_file)
                updated, divider_added = ensure_canonical_closing_divider(
                    original,
                    file_path=relative_html_path,
                    apply_divider_standards=template_overlay_context.apply_divider_standards,
                )
                if not divider_added:
                    continue
                _write_text(html_file, updated)
                _upsert_file_result(
                    file_results,
                    FileResult(
                        path=relative_html_path,
                        changed=True,
                        applied_changes=[
                            AppliedChange(
                                category="template_overlay",
                                description="Added canonical closing red divider to template content page",
                                count=1,
                            )
                        ],
                        manual_issues=[],
                        a11y_issues=[],
                    ),
                    merge_applied_changes=True,
                )

        if template_overlay_context is not None:
            template_overlay_report_json = (
                output_dir / f"{input_zip.stem}.template-overlay-report.json"
            )
            template_overlay_report_payload = build_template_overlay_report(
                context=template_overlay_context,
                file_summaries=template_overlay_file_summaries,
                output_json_path=template_overlay_report_json,
                materialization=template_materialization_summary,
            )

        if template_merge and template_package is not None:
            run_template_merge(
                unpack_dir=unpack_dir,
                template_package=template_package,
                intro_checklist_handling=intro_checklist_handling,
                learning_activities_handling=learning_activities_handling,
                full_template_shell=full_template_shell,
                seeded_starter_course=seeded_starter_course,
            )
            if template_overlay_context is not None:
                for html_file in sorted(unpack_dir.rglob("*.html")):
                    relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())
                    original = _read_text(html_file)
                    updated, divider_added = ensure_canonical_closing_divider(
                        original,
                        file_path=relative_html_path,
                        apply_divider_standards=template_overlay_context.apply_divider_standards,
                    )
                    if divider_added:
                        _write_text(html_file, updated)

        _zip_directory(unpack_dir, output_zip)

    reference_alignment = _load_reference_alignment(reference_audit_json)

    report = _build_report(
        input_zip=input_zip,
        output_zip=output_zip,
        rules_path=rules_path,
        policy_profile=policy_profile,
        manifest_found=manifest_found,
        file_results=file_results,
        best_practice_enforcer_enabled=best_practice_enforcer,
        math_handling=math_handling,
        reference_alignment=reference_alignment,
        template_overlay={
            "enabled": template_overlay_context is not None,
            "inputs": (
                {
                    "template_package": str(template_package),
                    "alias_map_json": (
                        str(template_alias_map_json)
                        if template_alias_map_json is not None
                        else ""
                    ),
                    "apply_visual_standards": bool(apply_template_visual_standards),
                    "apply_color_standards": bool(apply_template_color_standards),
                    "apply_divider_standards": bool(apply_template_divider_standards),
                    "image_layout_mode": image_layout_mode,
                    "apply_module_structure": bool(apply_template_module_structure),
                    "seeded_starter_course": bool(seeded_starter_course),
                }
                if template_overlay_context is not None
                else {
                    "apply_visual_standards": bool(apply_template_visual_standards),
                    "apply_color_standards": bool(apply_template_color_standards),
                    "apply_divider_standards": bool(apply_template_divider_standards),
                    "image_layout_mode": image_layout_mode,
                    "apply_module_structure": bool(apply_template_module_structure),
                    "seeded_starter_course": bool(seeded_starter_course),
                }
            ),
            "summary": (
                template_overlay_report_payload.get("summary", {})
                if isinstance(template_overlay_report_payload, dict)
                else {}
            ),
            "materialization": (
                template_overlay_report_payload.get("materialization", {})
                if isinstance(template_overlay_report_payload, dict)
                else {}
            ),
            "report_json": (
                str(template_overlay_report_json)
                if template_overlay_report_json is not None
                else ""
            ),
        },
        file_layout=file_layout_summary,
    )

    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown_report(report, report_markdown)
    _write_manual_review_csv(file_results, manual_review_csv)
    package_audit_rows = _collect_package_audit_rows(input_zip)
    _append_package_audit_rows_to_csv(package_audit_rows, manual_review_csv)

    # Write standalone quiz-audit reports (supplement to preflight checklist)
    try:
        _quiz_report = _audit_quizzes(input_zip)
        _write_quiz_json_report(_quiz_report, quiz_audit_json)
        _write_quiz_markdown_report(_quiz_report, quiz_audit_md)
    except Exception:
        quiz_audit_json = None  # type: ignore[assignment]
        quiz_audit_md = None  # type: ignore[assignment]

    kickoff_summary = _build_course_kickoff_summary(report, package_audit_rows)
    _write_course_kickoff_summary(
        kickoff_summary,
        output_json_path=kickoff_summary_json,
        output_markdown_path=kickoff_summary_md,
    )
    _write_preflight_checklist(
        report, policy_profile, preflight_checklist, manual_review_csv
    )

    return MigrationOutput(
        output_zip=output_zip,
        report_json=report_json,
        report_markdown=report_markdown,
        manual_review_csv=manual_review_csv,
        preflight_checklist=preflight_checklist,
        policy_profile_id=policy_profile.profile_id,
        template_overlay_report_json=template_overlay_report_json,
        quiz_audit_json=quiz_audit_json,
        quiz_audit_md=quiz_audit_md,
        kickoff_summary_json=kickoff_summary_json,
        kickoff_summary_md=kickoff_summary_md,
    )

from __future__ import annotations

import csv
import html
import json
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
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
    detect_manual_review_issues,
    neutralize_legacy_d2l_hrefs_in_markup,
    repair_missing_local_references,
)
from .policy_profiles import PolicyProfile, get_policy_profile
from .rules import load_rules
from .template_overlay import (
    TemplateOverlayConfig,
    apply_template_overlay,
    build_template_overlay_context,
    build_template_overlay_report,
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


def _remove_leading_duplicate_title_block(fragment: str, title_text: str) -> tuple[str, int]:
    normalized_title = _normalize_compare_text(title_text)
    if not normalized_title:
        return fragment, 0

    def _tokenize(value: str) -> list[str]:
        return [token for token in value.split(" ") if token and token not in {"the", "a", "an"}]

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


def _append_html_fragment(existing_html: str, fragment: str) -> str:
    separator = (
        '\n<hr style="border: 0; height: 2px; background-color: #ac1a2f; width: 100%; margin: 16px 0;">\n'
    )
    payload = f"{separator}{fragment.strip()}\n"
    if re.search(r"</body>", existing_html, flags=re.IGNORECASE):
        return re.sub(r"</body>", payload + "</body>", existing_html, count=1, flags=re.IGNORECASE)
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
    return updated


def _is_intro_objectives_page(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lower()
    return bool(re.search(r"topic\s*\d+/introduction and objectives\.html$", normalized))


_MODULE_NUMBERED_TITLE_RE = re.compile(r"^\s*Module\s*0*(?P<number>\d+)\s*:\s*(?P<label>.+?)\s*$", flags=re.IGNORECASE)


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
    if "introduction and objectives" in normalized or "introduction and checklist" in normalized:
        return "overview_intro"
    if normalized.startswith("introduction"):
        return "overview_intro"
    if "learning activities" in normalized:
        return "overview_learning"
    if "lesson" in normalized:
        return "overview_lesson"
    if normalized == "module review" or normalized.endswith(" review"):
        return "review_page"
    if any(token in normalized for token in ("discussion", "assignment", "quiz", "test", "survey", "exam")):
        return "activity"
    return "activity"


def _strip_title_prefix(value: str, pattern: str) -> str:
    stripped = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
    return stripped.strip("-:| ")


def _template_module_child_title(*, module_number: int, original_title: str, kind: str) -> str:
    if kind == "overview_intro":
        return f"Module {module_number}: Introduction and Checklist"
    if kind == "overview_learning":
        return f"Module {module_number}: Learning Activities"
    if kind == "overview_lesson":
        tail = _strip_title_prefix(original_title, r"^\s*lesson(?:\s*page)?\s*(?:\||:|-)?\s*")
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
        tail = _strip_title_prefix(original_title, r"^\s*(?:quiz|test|survey|exam)\s*(?:\||:|-)?\s*")
        if not tail:
            tail = "[Title Here]"
        return f"Quiz: {tail}"
    return original_title


def _apply_template_module_structure_to_organization(
    organization: ET.Element,
    *,
    existing_identifiers: set[str],
) -> tuple[int, int, int, int]:
    top_level_renames = 0
    child_title_renames = 0
    reordered_modules = 0
    inserted_subheaders = 0

    for top_item in [child for child in list(organization) if _local_name(child.tag) == "item"]:
        top_title_element, top_title = _extract_item_title(top_item)
        if top_title_element is None:
            continue

        top_normalized = _normalize_compare_text(top_title)
        desired_top_title = ""
        if top_normalized.startswith("faculty resources") or "hidden from students" in top_normalized:
            desired_top_title = "Instructor Module (Do Not Publish)"
        elif top_normalized.startswith("course overview") or top_normalized == "start here":
            desired_top_title = "Start Here"
        if desired_top_title and top_title != desired_top_title:
            top_title_element.text = desired_top_title
            top_title = desired_top_title
            top_level_renames += 1

        module_number = _module_number_from_title(top_title)
        if module_number is None:
            top_child_items = [child for child in list(top_item) if _local_name(child.tag) == "item"]
            top_child_titles = [_extract_item_title(child)[1] for child in top_child_items]
            normalized_top_title = _normalize_compare_text(top_title)

            if normalized_top_title == _normalize_compare_text("Start Here"):
                has_support_subheader = any(
                    _normalize_compare_text(child_title) == _normalize_compare_text("Canvas Support Resources")
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

            if normalized_top_title == _normalize_compare_text("Instructor Module (Do Not Publish)"):
                has_about_subheader = any(
                    _normalize_compare_text(child_title) == _normalize_compare_text("About This Template")
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

        child_items = [child for child in list(top_item) if _local_name(child.tag) == "item"]
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

    return top_level_renames, child_title_renames, reordered_modules, inserted_subheaders


def _to_serializable_issue(issue: ManualReviewIssue) -> dict[str, str]:
    return {"reason": issue.reason, "evidence": issue.evidence}


def _build_report(
    input_zip: Path,
    output_zip: Path,
    rules_path: Path,
    policy_profile: PolicyProfile,
    manifest_found: bool,
    file_results: list[FileResult],
    best_practice_enforcer_enabled: bool = False,
    reference_alignment: dict | None = None,
    template_overlay: dict | None = None,
) -> dict:
    total_files = len(file_results)
    changed_files = sum(1 for result in file_results if result.changed)

    change_count = sum(change.count for result in file_results for change in result.applied_changes)
    manual_issue_count = sum(len(result.manual_issues) for result in file_results)
    a11y_issue_count = sum(len(result.a11y_issues) for result in file_results)

    report = {
        "input_zip": str(input_zip),
        "output_zip": str(output_zip),
        "rules": str(rules_path),
        "policy_profile": {
            "id": policy_profile.profile_id,
            "description": policy_profile.description,
        },
        "best_practice_enforcer_enabled": bool(best_practice_enforcer_enabled),
        "manifest_found": manifest_found,
        "summary": {
            "html_files_scanned": total_files,
            "html_files_changed": changed_files,
            "total_automated_changes": change_count,
            "manual_review_issues": manual_issue_count,
            "accessibility_issues": a11y_issue_count,
        },
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
        "critical_gap_ids": [str(gap.get("id", "")).strip() for gap in critical_gaps if isinstance(gap, dict)],
        "best_practice_action_needed_count": len(action_needed),
        "best_practice_action_needed_ids": [
            str(row.get("id", "")).strip() for row in action_needed if isinstance(row, dict)
        ],
        "template_placeholder_patterns_detected": [str(item) for item in placeholders],
        "module_checklist_required_closer_present": bool(
            template.get("module_checklist_required_closer_present", True)
        ),
    }


def _write_markdown_report(report: dict, output_path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# LMS Migration Pilot Report",
        "",
        f"Input zip: `{report['input_zip']}`",
        f"Output zip: `{report['output_zip']}`",
        f"Rules: `{report['rules']}`",
        f"Policy profile: `{report['policy_profile']['id']}`",
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
        has_issues = file_entry["manual_review_issues"] or file_entry["accessibility_issues"]
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
        critical_ids = [item for item in reference_alignment.get("critical_gap_ids", []) if item]
        if critical_ids:
            lines.append(f"- Critical gap IDs: {', '.join(critical_ids)}")
        coverage_ids = [item for item in reference_alignment.get("best_practice_action_needed_ids", []) if item]
        if coverage_ids:
            lines.append(f"- Coverage action IDs: {', '.join(coverage_ids)}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


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


def _write_preflight_checklist(report: dict, profile: PolicyProfile, output_path: Path) -> None:
    summary = report["summary"]
    manual_counts = Counter()
    a11y_counts = Counter()
    for file_entry in report["files"]:
        for issue in file_entry.get("manual_review_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                manual_counts[reason] += 1
        for issue in file_entry.get("accessibility_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                a11y_counts[reason] += 1

    lines = [
        "# Migration Preflight Checklist",
        "",
        f"- Input zip: `{report['input_zip']}`",
        f"- Output zip: `{report['output_zip']}`",
        f"- Policy profile: `{profile.profile_id}`",
        f"- Profile description: {profile.description}",
        "",
        "## Automated Summary",
        "",
        f"- HTML files scanned: {summary['html_files_scanned']}",
        f"- HTML files changed: {summary['html_files_changed']}",
        f"- Manual review issues: {summary['manual_review_issues']}",
        f"- Accessibility issues: {summary['accessibility_issues']}",
        "",
        "## Required Verifications Before Release",
        "",
    ]

    if profile.preflight_items:
        for item in profile.preflight_items:
            lines.append(f"- [ ] {item}")
    else:
        lines.append("- [ ] Review manual findings and accessibility findings before release.")

    lines.extend(["", "## Findings-Based Follow-Up", ""])
    if manual_counts:
        lines.append("### Manual Review Reasons")
        lines.append("")
        for reason, count in manual_counts.most_common():
            lines.append(f"- [ ] ({count}) {reason}")
        lines.append("")
    if a11y_counts:
        lines.append("### Accessibility Reasons")
        lines.append("")
        for reason, count in a11y_counts.most_common():
            lines.append(f"- [ ] ({count}) {reason}")
        lines.append("")
    if not manual_counts and not a11y_counts:
        lines.append("- [ ] No issues flagged by automation.")
        lines.append("")

    reference_alignment = report.get("reference_alignment")
    if isinstance(reference_alignment, dict):
        lines.extend(["## Reference Alignment Follow-Up", ""])
        critical_gaps = int(reference_alignment.get("critical_gap_count", 0))
        if critical_gaps > 0:
            lines.append(f"- [ ] Resolve `{critical_gaps}` critical instruction gap(s) identified in reference audit.")
        action_needed = int(reference_alignment.get("best_practice_action_needed_count", 0))
        if action_needed > 0:
            lines.append(f"- [ ] Add migration rule/check coverage for `{action_needed}` best-practice topic(s).")
        if not bool(reference_alignment.get("module_checklist_required_closer_present", True)):
            lines.append("- [ ] Update template/rules to enforce Module Checklist closing reminder.")
        placeholders = reference_alignment.get("template_placeholder_patterns_detected", [])
        if isinstance(placeholders, list) and placeholders:
            lines.append("- [ ] Verify unresolved template placeholders are cleaned up before release.")
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
    accordion_handling: str = "flatten",
    apply_template_module_structure: bool = True,
    apply_template_visual_standards: bool = True,
) -> MigrationOutput:
    rules = load_rules(rules_path)
    policy_profile = get_policy_profile(policy_profile_id, policy_profiles_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_zip = output_dir / f"{input_zip.stem}.canvas-ready.zip"
    report_json = output_dir / f"{input_zip.stem}.migration-report.json"
    report_markdown = output_dir / f"{input_zip.stem}.migration-report.md"
    manual_review_csv = output_dir / f"{input_zip.stem}.manual-review.csv"
    preflight_checklist = output_dir / f"{input_zip.stem}.preflight-checklist.md"
    template_overlay_report_json: Path | None = None
    template_overlay_report_payload: dict | None = None
    template_overlay_context = None
    template_materialization_summary: dict | None = None
    if template_package is not None:
        template_overlay_context = build_template_overlay_context(
            TemplateOverlayConfig(
                template_package=template_package,
                alias_map_json_path=template_alias_map_json,
                apply_visual_standards=apply_template_visual_standards,
            )
        )

    with tempfile.TemporaryDirectory(prefix="lms-migration-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        unpack_dir = temp_dir / "unpacked"
        unpack_dir.mkdir(parents=True, exist_ok=True)

        with ZipFile(input_zip, "r") as zf:
            zf.extractall(unpack_dir)

        if template_overlay_context is not None:
            template_materialization_summary = materialize_template_assets(
                context=template_overlay_context,
                destination_root=unpack_dir,
            )

        manifest_found = any(unpack_dir.rglob("imsmanifest.xml"))

        html_files = [
            path for path in unpack_dir.rglob("*") if path.suffix.lower() in {".html", ".htm"}
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
            accordion_handling=accordion_handling,
        )

        for html_file in html_files:
            original = _read_text(html_file)
            updated = original
            applied_changes: list[AppliedChange] = []
            relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())

            updated, replacement_changes = apply_replacements(updated, rules.replacements)
            applied_changes.extend(replacement_changes)

            updated, rewrite_changes = apply_link_rewrites(updated, rules.link_rewrites)
            applied_changes.extend(rewrite_changes)

            overlay_issues: list[ManualReviewIssue] = []
            if template_overlay_context is not None:
                updated, overlay_changes, overlay_issues, overlay_file_summary = apply_template_overlay(
                    updated,
                    file_path=relative_html_path,
                    context=template_overlay_context,
                )
                applied_changes.extend(overlay_changes)
                template_overlay_file_summaries.append(overlay_file_summary)

            updated, banner_changes = apply_banner_rule(updated, rules.banner)
            applied_changes.extend(banner_changes)

            updated, sanitizer_changes = apply_canvas_sanitizer(
                updated,
                policy=sanitizer_policy,
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

            if best_practice_enforcer:
                updated, best_practice_changes = apply_best_practice_enforcer(
                    updated,
                    file_path=relative_html_path,
                    policy=BestPracticeEnforcerPolicy(
                        enabled=True,
                        enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                        ensure_external_links_new_tab=True,
                    ),
                )
                applied_changes.extend(best_practice_changes)

            if _is_intro_objectives_page(relative_html_path):
                topic_phrase_hits = len(re.findall(r"(?i)\bthis topic\b", updated))
                topic_ref_hits = len(re.findall(r"(?i)\btopic\s*0*\d+\s*(?:\||-|:)\s*", updated))
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

            manual_issues = detect_manual_review_issues(updated, rules.manual_review_triggers)
            manual_issues.extend(overlay_issues)
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
            path
            for path in unpack_dir.rglob("*_d2l.xml")
            if path.is_file()
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

        manifest_paths = [path for path in unpack_dir.rglob("imsmanifest.xml") if path.is_file()]
        for manifest_path in manifest_paths:
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            manifest_changed = False
            relative_manifest_path = str(manifest_path.relative_to(unpack_dir).as_posix())

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
                applied_changes: list[AppliedChange] = []

                updated, duplicate_title_count = _remove_leading_duplicate_title_block(updated, title_text)
                if duplicate_title_count:
                    applied_changes.append(
                        AppliedChange(
                            category="sanitizer",
                            description="Removed duplicate in-body heading/paragraph that repeated the Canvas page title",
                            count=duplicate_title_count,
                        )
                    )

                updated, replacement_changes = apply_replacements(updated, rules.replacements)
                applied_changes.extend(replacement_changes)

                updated, rewrite_changes = apply_link_rewrites(updated, rules.link_rewrites)
                applied_changes.extend(rewrite_changes)

                overlay_issues: list[ManualReviewIssue] = []
                if template_overlay_context is not None:
                    updated, overlay_changes, overlay_issues, overlay_file_summary = apply_template_overlay(
                        updated,
                        file_path=f"{relative_manifest_path}::item[{title_text or item.attrib.get('identifier', '')}]",
                        context=template_overlay_context,
                    )
                    applied_changes.extend(overlay_changes)
                    template_overlay_file_summaries.append(overlay_file_summary)

                updated, sanitizer_changes = apply_canvas_sanitizer(updated, policy=sanitizer_policy)
                applied_changes.extend(sanitizer_changes)

                if sanitizer_policy.repair_missing_local_references:
                    updated, repaired_ref_changes = repair_missing_local_references(
                        updated,
                        file_path=relative_manifest_path,
                        available_paths=available_paths,
                        keep_alt_text_for_missing_images=sanitizer_policy.use_alt_text_for_removed_template_images,
                    )
                    applied_changes.extend(repaired_ref_changes)

                if best_practice_enforcer:
                    updated, best_practice_changes = apply_best_practice_enforcer(
                        updated,
                        file_path=relative_manifest_path,
                        policy=BestPracticeEnforcerPolicy(
                            enabled=True,
                            enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                            ensure_external_links_new_tab=True,
                        ),
                    )
                    applied_changes.extend(best_practice_changes)

                manual_issues = detect_manual_review_issues(updated, rules.manual_review_triggers)
                manual_issues.extend(overlay_issues)
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
            for organization in [element for element in root.iter() if _local_name(element.tag) == "organization"]:
                top_level_items = [child for child in list(organization) if _local_name(child.tag) == "item"]
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

                    module_description = (module_item.attrib.get("description") or "").strip()
                    if not module_description or "<" not in module_description:
                        continue
                    module_description = _normalize_module_checklist_wording(module_description)

                    intro_item: ET.Element | None = None
                    for child_item in [child for child in list(module_item) if _local_name(child.tag) == "item"]:
                        _, child_title = _extract_item_title(child_item)
                        normalized_child_title = _normalize_compare_text(child_title)
                        if normalized_child_title in intro_title_candidates:
                            intro_item = child_item
                            break
                    if intro_item is None:
                        continue

                    intro_identifier = (intro_item.attrib.get("identifierref") or "").strip()
                    intro_href = resource_hrefs.get(intro_identifier, "")
                    if not intro_href:
                        continue

                    intro_html_path = manifest_path.parent / Path(intro_href.replace("\\", "/"))
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

                    intro_updated = _append_html_fragment(intro_original, module_description)
                    if intro_updated != intro_original:
                        _write_text(intro_html_path, intro_updated)
                    module_item.attrib.pop("description", None)
                    manifest_changed = True
                    topic_description_merged += 1
                    file_results.append(
                        FileResult(
                            path=str(intro_html_path.relative_to(unpack_dir).as_posix()),
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description="Moved module learning objectives/checklist block into Introduction and Objectives page",
                                    count=1,
                                )
                            ],
                            manual_issues=[],
                            a11y_issues=[],
                        )
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
                    if _local_name(element.tag) == "item" and (element.attrib.get("identifier") or "").strip()
                }
                top_level_renames = 0
                child_title_renames = 0
                reordered_modules = 0
                inserted_subheaders = 0
                for organization in [element for element in root.iter() if _local_name(element.tag) == "organization"]:
                    (
                        org_top_level_renames,
                        org_child_title_renames,
                        org_reordered_modules,
                        org_inserted_subheaders,
                    ) = _apply_template_module_structure_to_organization(
                        organization,
                        existing_identifiers=manifest_item_identifiers,
                    )
                    top_level_renames += org_top_level_renames
                    child_title_renames += org_child_title_renames
                    reordered_modules += org_reordered_modules
                    inserted_subheaders += org_inserted_subheaders

                if top_level_renames:
                    manifest_changed = True
                    file_results.append(
                        FileResult(
                            path=f"{relative_manifest_path}::organization",
                            changed=True,
                            applied_changes=[
                                AppliedChange(
                                    category="structure",
                                    description='Aligned top-level module names to template conventions (e.g., "Start Here", "Instructor Module")',
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

        if template_overlay_context is not None:
            template_overlay_report_json = output_dir / f"{input_zip.stem}.template-overlay-report.json"
            template_overlay_report_payload = build_template_overlay_report(
                context=template_overlay_context,
                file_summaries=template_overlay_file_summaries,
                output_json_path=template_overlay_report_json,
                materialization=template_materialization_summary,
            )

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
        reference_alignment=reference_alignment,
        template_overlay={
            "enabled": template_overlay_context is not None,
            "inputs": (
                {
                    "template_package": str(template_package),
                    "alias_map_json": str(template_alias_map_json) if template_alias_map_json is not None else "",
                    "apply_visual_standards": bool(apply_template_visual_standards),
                    "apply_module_structure": bool(apply_template_module_structure),
                }
                if template_overlay_context is not None
                else {
                    "apply_visual_standards": bool(apply_template_visual_standards),
                    "apply_module_structure": bool(apply_template_module_structure),
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
            "report_json": str(template_overlay_report_json) if template_overlay_report_json is not None else "",
        },
    )

    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown_report(report, report_markdown)
    _write_manual_review_csv(file_results, manual_review_csv)
    _write_preflight_checklist(report, policy_profile, preflight_checklist)

    return MigrationOutput(
        output_zip=output_zip,
        report_json=report_json,
        report_markdown=report_markdown,
        manual_review_csv=manual_review_csv,
        preflight_checklist=preflight_checklist,
        policy_profile_id=policy_profile.profile_id,
        template_overlay_report_json=template_overlay_report_json,
    )

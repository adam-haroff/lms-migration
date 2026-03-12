from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChecklistItem:
    priority: str
    source: str
    category: str
    owner: str
    description: str
    action: str
    count: int = 1
    reference: str = ""


def _priority_rank(priority: str) -> int:
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(priority.strip().upper(), 9)


def _map_canvas_issue(issue: dict) -> ChecklistItem:
    description = str(issue.get("description", "")).strip()
    reference = str(issue.get("fix_issue_html_url", "")).strip()
    lowered = description.lower()

    if "couldn't determine the correct answers" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="quiz_answer_key",
            owner="Faculty/ID",
            description=description,
            action="Open the question and set/verify correct answer(s), points, and feedback in Canvas.",
            reference=reference,
        )

    if "missing links found in imported content - wiki page body" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_page_link",
            owner="ID",
            description=description,
            action="Open page, relink missing resources to Canvas destinations, save, and republish.",
            reference=reference,
        )

    if "missing links found in imported content - announcement message" in lowered:
        return ChecklistItem(
            priority="P2",
            source="canvas_import",
            category="missing_announcement_link",
            owner="ID",
            description=description,
            action="Edit announcement links to Canvas resources or remove broken legacy links.",
            reference=reference,
        )

    if "missing links found in imported content - assignment description" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_assignment_link",
            owner="ID",
            description=description,
            action="Open assignment instructions, relink missing resources to Canvas files/pages, and resave.",
            reference=reference,
        )

    if "missing links found in imported content - assessment question question_text" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_assessment_question_link",
            owner="Faculty/ID",
            description=description,
            action="Open quiz question text and relink or remove unresolved references.",
            reference=reference,
        )

    if description.startswith("Import Error: Module Item - "):
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="module_item_import_error",
            owner="ID",
            description=description,
            action="Open module and resolve missing/failed item import, replacing with the correct Canvas item.",
            reference=reference,
        )

    if description.startswith("Import Error: Quiz - "):
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="quiz_import_error",
            owner="Faculty/ID",
            description=description,
            action="Open quiz settings/questions and rebuild missing items as needed.",
            reference=reference,
        )

    return ChecklistItem(
        priority="P2",
        source="canvas_import",
        category="canvas_import_warning",
        owner="ID",
        description=description or "Canvas import warning",
        action="Review warning and resolve in Canvas.",
        reference=reference,
    )


def _map_manual_review_group(issue_type: str, reason: str) -> tuple[str, str, str, str]:
    lowered = reason.lower()

    if "legacy script blocks" in lowered:
        return ("P1", "html_script_cleanup", "ID", "Remove legacy script behavior and verify page rendering in Canvas.")
    if "embedded iframe" in lowered:
        return ("P1", "embedded_iframe_review", "ID", "Review each iframe for accessibility, security, and responsive behavior.")
    if "template placeholder text remains" in lowered:
        return ("P1", "template_placeholder_cleanup", "Faculty/ID", "Replace unresolved template placeholders with final course-specific content.")
    if "template asset reference not mapped to canvas template package" in lowered:
        return (
            "P1",
            "template_asset_mapping_review",
            "ID",
            "Map unresolved Brightspace template assets to approved Canvas template assets and re-run migration.",
        )
    if "legacy d2l links were neutralized" in lowered:
        return ("P1", "relink_neutralized_d2l_links", "ID", "Replace neutralized D2L links with valid Canvas links.")
    if "question bank migration requires manual verification" in lowered:
        return ("P1", "question_bank_logic_review", "Faculty/ID", "Verify randomization, draw counts, and alignment for migrated item banks.")
    if "youtube embeds may violate ad-free requirement" in lowered:
        return ("P2", "youtube_hosting_review", "Faculty/ID", "Confirm hosting approach (for example Canvas Studio or approved platform).")
    if "announcement migration behavior is non-standard" in lowered:
        return ("P2", "announcement_settings_review", "ID", "Verify announcement posting state and notification behavior in Canvas.")
    if "panopto embed requires permissions" in lowered:
        return ("P1", "panopto_permissions_review", "ID", "Validate Panopto folder permissions and embed behavior for students.")
    if "h5p content requires manual conversion" in lowered:
        return ("P2", "h5p_conversion_decision", "ID", "Replace or rebuild H5P content using approved Canvas-compatible workflow.")
    if "scorm packages require upload" in lowered:
        return ("P1", "scorm_upload_workflow", "ID", "Re-import SCORM package through Canvas SCORM integration workflow.")

    if issue_type == "accessibility":
        if "image missing alt attribute" in lowered or "image alt attribute is empty" in lowered:
            return ("P1", "a11y_alt_text", "Faculty/ID", "Add meaningful alt text or mark decorative images appropriately.")
        if "heading level jump detected" in lowered:
            return ("P2", "a11y_heading_order", "Faculty/ID", "Fix heading hierarchy to avoid level jumps.")
        if "table missing caption" in lowered:
            return ("P2", "a11y_table_caption", "Faculty/ID", "Add table captions and header associations as needed.")
        if "non-descriptive link text" in lowered:
            return ("P2", "a11y_link_text", "Faculty/ID", "Replace vague link text with descriptive labels.")

    return ("P2", "manual_review_item", "ID", "Review and resolve this migration finding.")


def _load_canvas_items(path: Path) -> list[ChecklistItem]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [_map_canvas_issue(item) for item in payload if isinstance(item, dict)]


def _load_manual_review_items(path: Path | None) -> list[ChecklistItem]:
    if path is None or not path.exists():
        return []

    grouped: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "files": []}
    )
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issue_type = str(row.get("type", "")).strip() or "manual_review"
            reason = str(row.get("reason", "")).strip()
            file_path = str(row.get("file", "")).strip()
            if not reason:
                continue
            key = (issue_type, reason)
            grouped[key]["count"] += 1
            if file_path and file_path not in grouped[key]["files"]:
                grouped[key]["files"].append(file_path)

    items: list[ChecklistItem] = []
    for (issue_type, reason), meta in grouped.items():
        priority, category, owner, action = _map_manual_review_group(issue_type, reason)
        sample_files = ", ".join(meta["files"][:3])
        reference = f"{path.name} | sample files: {sample_files}" if sample_files else path.name
        items.append(
            ChecklistItem(
                priority=priority,
                source="manual_review",
                category=category,
                owner=owner,
                description=reason,
                action=action,
                count=int(meta["count"]),
                reference=reference,
            )
        )
    return items


def _load_reference_items(path: Path | None) -> list[ChecklistItem]:
    if path is None or not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    items: list[ChecklistItem] = []

    instruction = payload.get("instruction_comparison", {})
    for gap in instruction.get("critical_gaps", []):
        phrase = str(gap.get("phrase", "")).strip()
        rationale = str(gap.get("rationale", "")).strip()
        if not phrase:
            continue
        items.append(
            ChecklistItem(
                priority="P2",
                source="reference_audit",
                category=f"reference_instruction_gap:{gap.get('id', 'gap')}",
                owner="ID Lead",
                description=phrase,
                action="Decide whether to encode this guidance into migration rules, checklist, or process docs.",
                reference=rationale,
            )
        )

    coverage = payload.get("best_practices_coverage", {})
    for row in coverage.get("coverage_rows", []):
        if not bool(row.get("action_needed")):
            continue
        label = str(row.get("label", "")).strip()
        if not label:
            continue
        items.append(
            ChecklistItem(
                priority="P2",
                source="reference_audit",
                category=f"reference_best_practice_gap:{row.get('id', 'coverage')}",
                owner="ID Lead",
                description=label,
                action="Add explicit rule, trigger, or preflight check to cover this best-practice topic.",
                reference=path.name,
            )
        )

    template = payload.get("template_analysis", {})
    if not bool(template.get("module_checklist_required_closer_present", True)):
        items.append(
            ChecklistItem(
                priority="P1",
                source="reference_audit",
                category="reference_template_mc_closer",
                owner="ID Lead",
                description="Template missing required Module Checklist closing reminder.",
                action="Update template and migration validation to enforce required checklist closer.",
                reference=path.name,
            )
        )

    placeholder_patterns = template.get("placeholder_patterns_detected", [])
    if isinstance(placeholder_patterns, list) and placeholder_patterns:
        items.append(
            ChecklistItem(
                priority="P2",
                source="reference_audit",
                category="reference_template_placeholders",
                owner="ID Lead",
                description="Template documents contain placeholder patterns.",
                action="Confirm placeholders are intentional template tokens and enforce final cleanup rules.",
                reference=", ".join(str(x) for x in placeholder_patterns[:5]),
            )
        )

    return items


def build_fix_checklist(
    *,
    canvas_issues_json: Path,
    output_dir: Path,
    manual_review_csv: Path | None = None,
    reference_audit_json: Path | None = None,
    basename: str = "migration-fix-checklist",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{basename}.csv"
    md_path = output_dir / f"{basename}.md"

    items = []
    items.extend(_load_canvas_items(canvas_issues_json))
    items.extend(_load_manual_review_items(manual_review_csv))
    items.extend(_load_reference_items(reference_audit_json))
    items.sort(key=lambda item: (_priority_rank(item.priority), item.source, item.category, item.description))

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "priority",
                "source",
                "category",
                "owner",
                "description",
                "action",
                "count",
                "reference",
                "status",
                "notes",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "priority": item.priority,
                    "source": item.source,
                    "category": item.category,
                    "owner": item.owner,
                    "description": item.description,
                    "action": item.action,
                    "count": item.count,
                    "reference": item.reference,
                    "status": "todo",
                    "notes": "",
                }
            )

    by_priority = Counter(item.priority for item in items)
    by_source = Counter(item.source for item in items)
    by_category = Counter(item.category for item in items)

    lines = [
        "# Migration Fix Checklist",
        "",
        f"- Canvas issues input: `{canvas_issues_json}`",
        f"- Manual review input: `{manual_review_csv}`" if manual_review_csv else "- Manual review input: none",
        f"- Reference audit input: `{reference_audit_json}`" if reference_audit_json else "- Reference audit input: none",
        f"- Total checklist items: {len(items)}",
        f"- CSV output: `{csv_path}`",
        "",
        "## Summary",
        "",
    ]
    for priority, count in sorted(by_priority.items(), key=lambda x: _priority_rank(x[0])):
        lines.append(f"- {priority}: {count}")
    lines.append("")
    for source, count in by_source.items():
        lines.append(f"- {source}: {count}")

    lines.extend(["", "## Category Counts", ""])
    for category, count in by_category.most_common():
        lines.append(f"- {category}: {count}")

    lines.extend(["", "## Action Items", ""])
    for item in items:
        lines.append(
            f"- [ ] ({item.priority}) [{item.source}] {item.category} | {item.description} | owner: {item.owner} | count: {item.count}"
        )
        lines.append(f"  - Action: {item.action}")
        if item.reference:
            lines.append(f"  - Reference: {item.reference}")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-build-fix-checklist",
        description="Build migration fix checklist from Canvas import issues and optional audit files.",
    )
    parser.add_argument("canvas_issues_json", type=Path, help="Path to canvas-migration-issues.json")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Output directory")
    parser.add_argument("--manual-review-csv", type=Path, default=None, help="Optional manual-review CSV")
    parser.add_argument("--reference-audit-json", type=Path, default=None, help="Optional reference-audit JSON")
    parser.add_argument("--basename", type=str, default="migration-fix-checklist", help="Output base name")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.canvas_issues_json.exists():
        parser.error(f"Canvas issues JSON not found: {args.canvas_issues_json}")
    if args.manual_review_csv is not None and not args.manual_review_csv.exists():
        parser.error(f"Manual review CSV not found: {args.manual_review_csv}")
    if args.reference_audit_json is not None and not args.reference_audit_json.exists():
        parser.error(f"Reference audit JSON not found: {args.reference_audit_json}")

    csv_path, md_path = build_fix_checklist(
        canvas_issues_json=args.canvas_issues_json,
        output_dir=args.output_dir,
        manual_review_csv=args.manual_review_csv,
        reference_audit_json=args.reference_audit_json,
        basename=args.basename,
    )
    print(f"Checklist CSV: {csv_path}")
    print(f"Checklist Markdown: {md_path}")


if __name__ == "__main__":
    main()

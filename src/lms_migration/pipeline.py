from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
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
    repair_missing_local_references,
)
from .policy_profiles import PolicyProfile, get_policy_profile
from .rules import load_rules


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
        "## Files With Issues",
        "",
    ]

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
) -> MigrationOutput:
    rules = load_rules(rules_path)
    policy_profile = get_policy_profile(policy_profile_id, policy_profiles_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_zip = output_dir / f"{input_zip.stem}.canvas-ready.zip"
    report_json = output_dir / f"{input_zip.stem}.migration-report.json"
    report_markdown = output_dir / f"{input_zip.stem}.migration-report.md"
    manual_review_csv = output_dir / f"{input_zip.stem}.manual-review.csv"
    preflight_checklist = output_dir / f"{input_zip.stem}.preflight-checklist.md"

    with tempfile.TemporaryDirectory(prefix="lms-migration-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        unpack_dir = temp_dir / "unpacked"
        unpack_dir.mkdir(parents=True, exist_ok=True)

        with ZipFile(input_zip, "r") as zf:
            zf.extractall(unpack_dir)

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

        for html_file in html_files:
            original = _read_text(html_file)
            updated = original
            applied_changes: list[AppliedChange] = []
            relative_html_path = str(html_file.relative_to(unpack_dir).as_posix())

            updated, replacement_changes = apply_replacements(updated, rules.replacements)
            applied_changes.extend(replacement_changes)

            updated, rewrite_changes = apply_link_rewrites(updated, rules.link_rewrites)
            applied_changes.extend(rewrite_changes)

            updated, banner_changes = apply_banner_rule(updated, rules.banner)
            applied_changes.extend(banner_changes)

            sanitizer_policy = CanvasSanitizerPolicy(
                sanitize_brightspace_assets=policy_profile.sanitize_brightspace_assets,
                neutralize_legacy_d2l_links=policy_profile.neutralize_legacy_d2l_links,
                use_alt_text_for_removed_template_images=policy_profile.use_alt_text_for_removed_template_images,
                repair_missing_local_references=policy_profile.repair_missing_local_references,
            )
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

            manual_issues = detect_manual_review_issues(updated, rules.manual_review_triggers)
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
    )

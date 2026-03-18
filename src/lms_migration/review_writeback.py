from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .html_tools import (
    AppliedChange,
    BestPracticeEnforcerPolicy,
    CanvasSanitizerPolicy,
    apply_best_practice_enforcer,
    apply_canvas_sanitizer,
    check_accessibility_heuristics,
    detect_manual_review_issues,
)
from .policy_profiles import get_policy_profile
from .rules import load_rules


_BODY_RE = re.compile(r"(<body\b[^>]*>)(?P<body>.*?)(</body>)", flags=re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ReviewWritebackResult:
    output_zip: Path
    report_json: Path
    report_markdown: Path


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
                zf.write(file_path, file_path.relative_to(source_dir))


def _replace_body_html(document_html: str, body_html: str) -> str:
    match = _BODY_RE.search(document_html)
    replacement = body_html.strip()
    if match is None:
        return replacement
    return (
        document_html[: match.start("body")]
        + ("\n" + replacement + "\n" if replacement else "\n")
        + document_html[match.end("body") :]
    )


def _default_output_zip_path(converted_zip: Path) -> Path:
    name = converted_zip.name
    if name.endswith(".canvas-ready.zip"):
        return converted_zip.with_name(name.replace(".canvas-ready.zip", ".canvas-reviewed.zip"))
    if name.endswith(".zip"):
        return converted_zip.with_name(name[:-4] + ".reviewed.zip")
    return converted_zip.with_name(name + ".reviewed.zip")


def _default_output_json_path(output_zip: Path) -> Path:
    stem = output_zip.name[:-4] if output_zip.name.endswith(".zip") else output_zip.name
    return output_zip.with_name(f"{stem}.review-writeback.json")


def _default_output_markdown_path(output_json: Path) -> Path:
    return output_json.with_suffix(".md")


def apply_review_draft(
    *,
    draft_json: Path,
    converted_zip: Path,
    rules_path: Path | None,
    policy_profile_id: str,
    policy_profiles_path: Path,
    math_handling: str = "preserve-semantic",
    accordion_handling: str = "smart",
    accordion_alignment: str = "left",
    accordion_flatten_hints: tuple[str, ...] = (),
    accordion_details_hints: tuple[str, ...] = (),
    apply_template_divider_standards: bool = True,
    best_practice_enforcer: bool = True,
    output_zip_path: Path | None = None,
    output_json_path: Path | None = None,
    output_markdown_path: Path | None = None,
) -> ReviewWritebackResult:
    draft_payload = json.loads(draft_json.read_text(encoding="utf-8"))
    if not isinstance(draft_payload, dict):
        raise ValueError("Review draft JSON must be an object.")

    raw_pages = draft_payload.get("pages", [])
    if not isinstance(raw_pages, list):
        raise ValueError("Review draft JSON must include a pages array.")

    edited_pages = [
        row
        for row in raw_pages
        if isinstance(row, dict)
        and str(row.get("path", "")).strip()
        and "edited_body_html" in row
    ]
    if not edited_pages:
        raise ValueError("Review draft JSON does not contain any edited pages.")

    policy_profile = get_policy_profile(policy_profile_id, policy_profiles_path)
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
    rules = load_rules(rules_path) if rules_path is not None else None

    output_zip = output_zip_path or _default_output_zip_path(converted_zip)
    report_json = output_json_path or _default_output_json_path(output_zip)
    report_markdown = output_markdown_path or _default_output_markdown_path(report_json)
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)

    page_results: list[dict] = []
    changed_pages = 0
    missing_pages = 0
    manual_issue_total = 0
    accessibility_issue_total = 0
    sanitizer_change_total = 0
    best_practice_change_total = 0

    with tempfile.TemporaryDirectory(prefix="lms-review-writeback-") as tmp_dir:
        unpack_dir = Path(tmp_dir) / "unpacked"
        with ZipFile(converted_zip, "r") as zf:
            zf.extractall(unpack_dir)

        for row in edited_pages:
            relative_path = str(row.get("path", "")).strip().replace("\\", "/")
            edited_body_html = str(row.get("edited_body_html", ""))
            original_body_html = str(row.get("original_body_html", ""))
            page_file = unpack_dir / Path(relative_path)

            if not page_file.exists() or not page_file.is_file():
                page_results.append(
                    {
                        "path": relative_path,
                        "status": "missing",
                        "manual_review_issues": [],
                        "accessibility_issues": [],
                        "applied_changes": [],
                    }
                )
                missing_pages += 1
                continue

            original_document = _read_text(page_file)
            updated_document = _replace_body_html(original_document, edited_body_html)

            applied_changes: list[AppliedChange] = []
            updated_document, sanitizer_changes = apply_canvas_sanitizer(
                updated_document,
                policy=sanitizer_policy,
                file_path=relative_path,
            )
            applied_changes.extend(sanitizer_changes)
            sanitizer_change_total += sum(change.count for change in sanitizer_changes)

            if best_practice_enforcer:
                updated_document, best_practice_changes = apply_best_practice_enforcer(
                    updated_document,
                    file_path=relative_path,
                    policy=BestPracticeEnforcerPolicy(
                        enabled=True,
                        enforce_module_checklist_closer=policy_profile.require_mc_closing_bullet,
                        ensure_external_links_new_tab=True,
                    ),
                )
                applied_changes.extend(best_practice_changes)
                best_practice_change_total += sum(change.count for change in best_practice_changes)

            manual_issues = (
                detect_manual_review_issues(updated_document, rules.manual_review_triggers)
                if rules is not None
                else []
            )
            accessibility_issues = check_accessibility_heuristics(updated_document)

            manual_issue_total += len(manual_issues)
            accessibility_issue_total += len(accessibility_issues)

            document_changed = updated_document != original_document
            if document_changed:
                _write_text(page_file, updated_document)
                changed_pages += 1

            page_results.append(
                {
                    "path": relative_path,
                    "status": "updated" if document_changed else "unchanged",
                    "edited_body_changed": edited_body_html.strip() != original_body_html.strip(),
                    "manual_review_issues": [
                        {"reason": issue.reason, "evidence": issue.evidence}
                        for issue in manual_issues
                    ],
                    "accessibility_issues": [
                        {"reason": issue.reason, "evidence": issue.evidence}
                        for issue in accessibility_issues
                    ],
                    "applied_changes": [
                        {
                            "category": change.category,
                            "description": change.description,
                            "count": change.count,
                        }
                        for change in applied_changes
                    ],
                }
            )

        _zip_directory(unpack_dir, output_zip)

    report = {
        "inputs": {
            "draft_json": str(draft_json),
            "converted_zip": str(converted_zip),
            "rules_path": str(rules_path) if rules_path is not None else "",
            "policy_profile_id": policy_profile_id,
            "math_handling": math_handling,
            "accordion_handling": accordion_handling,
            "accordion_alignment": accordion_alignment,
            "accordion_flatten_hints": list(accordion_flatten_hints),
            "accordion_details_hints": list(accordion_details_hints),
            "best_practice_enforcer": best_practice_enforcer,
        },
        "outputs": {
            "output_zip": str(output_zip),
            "report_json": str(report_json),
            "report_markdown": str(report_markdown),
        },
        "summary": {
            "draft_pages": len(edited_pages),
            "pages_updated": changed_pages,
            "pages_missing": missing_pages,
            "manual_review_issues": manual_issue_total,
            "accessibility_issues": accessibility_issue_total,
            "sanitizer_changes": sanitizer_change_total,
            "best_practice_changes": best_practice_change_total,
        },
        "pages": page_results,
    }

    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Review Write-Back",
        "",
        "## Summary",
        "",
        f"- Math handling: {report['inputs']['math_handling']}",
        f"- Draft pages: {report['summary']['draft_pages']}",
        f"- Pages updated: {report['summary']['pages_updated']}",
        f"- Pages missing from package: {report['summary']['pages_missing']}",
        f"- Manual review issues after write-back: {report['summary']['manual_review_issues']}",
        f"- Accessibility issues after write-back: {report['summary']['accessibility_issues']}",
        f"- Sanitizer changes applied: {report['summary']['sanitizer_changes']}",
        f"- Best-practice changes applied: {report['summary']['best_practice_changes']}",
        "",
        "## Pages",
        "",
    ]
    for row in page_results:
        lines.append(f"- `{row['path']}` | {row['status']}")
    report_markdown.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    return ReviewWritebackResult(
        output_zip=output_zip,
        report_json=report_json,
        report_markdown=report_markdown,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lms-review-writeback",
        description="Apply a deterministic review draft back into a converted Canvas package.",
    )
    parser.add_argument("--draft-json", type=Path, required=True, help="Review draft JSON exported from the workbench")
    parser.add_argument("--converted-zip", type=Path, required=True, help="Converted canvas-ready zip")
    parser.add_argument("--rules", type=Path, default=None, help="Optional rules JSON used for manual-review triggers")
    parser.add_argument(
        "--policy-profile",
        default="strict",
        help="Policy profile id to reuse for the post-writeback sanitizer pass",
    )
    parser.add_argument(
        "--policy-profiles",
        type=Path,
        default=Path("rules/policy_profiles.json"),
        help="Path to policy profiles JSON",
    )
    parser.add_argument(
        "--math-handling",
        choices=("preserve-semantic", "canvas-equation-compatible", "audit-only"),
        default="preserve-semantic",
        help="Math handling mode for the sanitizer pass.",
    )
    parser.add_argument(
        "--accordion-handling",
        choices=("smart", "details", "flatten", "none"),
        default="smart",
        help="Accordion handling mode for the sanitizer pass",
    )
    parser.add_argument(
        "--accordion-align",
        choices=("left", "center"),
        default="left",
        help="Summary alignment for converted accessible accordion blocks.",
    )
    parser.add_argument(
        "--accordion-flatten-hints",
        default="",
        help="Comma-separated path/title hints that should always flatten legacy accordion blocks.",
    )
    parser.add_argument(
        "--accordion-details-hints",
        default="",
        help="Comma-separated path/title hints that should always convert legacy accordion blocks to <details>.",
    )
    parser.add_argument(
        "--disable-template-divider-standards",
        action="store_true",
        help="Skip divider normalization during review write-back.",
    )
    parser.add_argument(
        "--disable-best-practice-enforcer",
        action="store_true",
        help="Skip the best-practice enforcer during write-back",
    )
    parser.add_argument("--output-zip", type=Path, default=None, help="Optional reviewed zip output path")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional write-back report JSON path")
    parser.add_argument("--output-markdown", type=Path, default=None, help="Optional write-back report Markdown path")
    args = parser.parse_args()

    result = apply_review_draft(
        draft_json=args.draft_json,
        converted_zip=args.converted_zip,
        rules_path=args.rules,
        policy_profile_id=args.policy_profile,
        policy_profiles_path=args.policy_profiles,
        math_handling=args.math_handling,
        accordion_handling=args.accordion_handling,
        accordion_alignment=args.accordion_align,
        accordion_flatten_hints=tuple(
            token.strip().lower() for token in args.accordion_flatten_hints.split(",") if token.strip()
        ),
        accordion_details_hints=tuple(
            token.strip().lower() for token in args.accordion_details_hints.split(",") if token.strip()
        ),
        apply_template_divider_standards=not args.disable_template_divider_standards,
        best_practice_enforcer=not args.disable_best_practice_enforcer,
        output_zip_path=args.output_zip,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(f"Reviewed zip: {result.output_zip}")
    print(f"Write-back report JSON: {result.report_json}")
    print(f"Write-back report Markdown: {result.report_markdown}")


if __name__ == "__main__":
    main()

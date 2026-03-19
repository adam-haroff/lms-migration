from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_migration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-migrate",
        description="Pilot D2L export preprocessor for Canvas import",
    )
    parser.add_argument(
        "input_zip",
        type=Path,
        help="Path to the D2L export zip",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("rules/default_rules.json"),
        help="Path to rules JSON file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for output artifacts",
    )
    parser.add_argument(
        "--policy-profile",
        type=str,
        default="strict",
        help="Policy profile id (e.g., standard or strict)",
    )
    parser.add_argument(
        "--policy-profiles",
        type=Path,
        default=Path("rules/policy_profiles.json"),
        help="Path to policy profiles JSON file",
    )
    parser.add_argument(
        "--reference-audit-json",
        type=Path,
        default=None,
        help="Optional reference-audit JSON to include alignment checks in migration report",
    )
    parser.add_argument(
        "--best-practice-enforcer",
        action="store_true",
        help="Enable safe best-practice enforcement during HTML preprocessing.",
    )
    parser.add_argument(
        "--template-package",
        type=Path,
        default=None,
        help="Optional Canvas template export package (.imscc) used for template asset overlay mapping.",
    )
    parser.add_argument(
        "--template-alias-map-json",
        type=Path,
        default=None,
        help="Optional alias map JSON used with --template-package for legacy-to-template basename mapping.",
    )
    parser.add_argument(
        "--math-handling",
        type=str,
        choices=("preserve-semantic", "canvas-equation-compatible", "audit-only"),
        default="preserve-semantic",
        help="Math handling policy. Preserve semantic math is the recommended default; audit-only skips math cleanup.",
    )
    parser.add_argument(
        "--accordion-handling",
        type=str,
        choices=("none", "details", "flatten", "smart"),
        default="smart",
        help="How to handle legacy Bootstrap accordions during conversion. 'smart' flattens document-style pages and keeps accessible details blocks on content pages.",
    )
    parser.add_argument(
        "--accordion-align",
        type=str,
        choices=("left", "center"),
        default="left",
        help="Summary alignment for converted accessible accordion blocks.",
    )
    parser.add_argument(
        "--accordion-flatten-hints",
        type=str,
        default="",
        help="Comma-separated path/title hints that should always flatten legacy accordions.",
    )
    parser.add_argument(
        "--accordion-details-hints",
        type=str,
        default="",
        help="Comma-separated path/title hints that should always convert legacy accordions to accessible details blocks.",
    )
    parser.add_argument(
        "--no-template-module-structure",
        action="store_true",
        help="Disable template-style module structuring (Overview/Activities/Review + module item naming).",
    )
    parser.add_argument(
        "--no-template-visual-standards",
        action="store_true",
        help="Disable template visual normalization (icon+heading layout, icon sizing/labels, heading color hints).",
    )
    parser.add_argument(
        "--no-template-color-standards",
        action="store_true",
        help="Disable template color normalization while leaving other template visual standards enabled.",
    )
    parser.add_argument(
        "--no-template-divider-standards",
        action="store_true",
        help="Disable template divider normalization while leaving other template visual standards enabled.",
    )
    parser.add_argument(
        "--image-layout-mode",
        type=str,
        choices=("safe-block", "preserve-wrap"),
        default="safe-block",
        help="How to handle large floated content images. safe-block avoids text overlap; preserve-wrap keeps optional wrapped-text layouts within safer width limits.",
    )
    parser.add_argument(
        "--template-merge",
        action="store_true",
        default=False,
        help=(
            "Apply Phase 3 template shell merger: wrap module intro pages with the "
            "standard template structure (star/bullseye/checkmark icons), merge the "
            "D2L Welcome from Instructor page into the About the Instructor template, "
            "and add standalone template pages (home, policies, resources). "
            "Requires --template-package."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.input_zip.exists():
        parser.error(f"Input zip does not exist: {args.input_zip}")

    if not args.rules.exists():
        parser.error(f"Rules file does not exist: {args.rules}")

    if not args.policy_profiles.exists():
        parser.error(f"Policy profiles file does not exist: {args.policy_profiles}")
    if args.reference_audit_json is not None and not args.reference_audit_json.exists():
        parser.error(
            f"Reference audit file does not exist: {args.reference_audit_json}"
        )
    if args.template_package is not None and not args.template_package.exists():
        parser.error(f"Template package does not exist: {args.template_package}")
    if (
        args.template_alias_map_json is not None
        and not args.template_alias_map_json.exists()
    ):
        parser.error(
            f"Template alias map JSON does not exist: {args.template_alias_map_json}"
        )

    try:
        result = run_migration(
            input_zip=args.input_zip,
            output_dir=args.output_dir,
            rules_path=args.rules,
            policy_profile_id=args.policy_profile,
            policy_profiles_path=args.policy_profiles,
            reference_audit_json=args.reference_audit_json,
            best_practice_enforcer=bool(args.best_practice_enforcer),
            template_package=args.template_package,
            template_alias_map_json=args.template_alias_map_json,
            math_handling=args.math_handling,
            accordion_handling=args.accordion_handling,
            accordion_alignment=args.accordion_align,
            accordion_flatten_hints=tuple(
                token.strip().lower()
                for token in args.accordion_flatten_hints.split(",")
                if token.strip()
            ),
            accordion_details_hints=tuple(
                token.strip().lower()
                for token in args.accordion_details_hints.split(",")
                if token.strip()
            ),
            apply_template_module_structure=not bool(args.no_template_module_structure),
            apply_template_visual_standards=not bool(args.no_template_visual_standards),
            apply_template_color_standards=not bool(args.no_template_color_standards),
            apply_template_divider_standards=not bool(
                args.no_template_divider_standards
            ),
            image_layout_mode=args.image_layout_mode,
            template_merge=bool(args.template_merge),
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Canvas-ready zip: {result.output_zip}")
    print(f"JSON report: {result.report_json}")
    print(f"Markdown report: {result.report_markdown}")
    print(f"Manual review CSV: {result.manual_review_csv}")
    print(f"Preflight checklist: {result.preflight_checklist}")
    if result.template_overlay_report_json is not None:
        print(f"Template overlay report JSON: {result.template_overlay_report_json}")
    print(f"Policy profile: {result.policy_profile_id}")


if __name__ == "__main__":
    main()

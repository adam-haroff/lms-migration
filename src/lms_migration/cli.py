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
        parser.error(f"Reference audit file does not exist: {args.reference_audit_json}")

    try:
        result = run_migration(
            input_zip=args.input_zip,
            output_dir=args.output_dir,
            rules_path=args.rules,
            policy_profile_id=args.policy_profile,
            policy_profiles_path=args.policy_profiles,
            reference_audit_json=args.reference_audit_json,
            best_practice_enforcer=bool(args.best_practice_enforcer),
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Canvas-ready zip: {result.output_zip}")
    print(f"JSON report: {result.report_json}")
    print(f"Markdown report: {result.report_markdown}")
    print(f"Manual review CSV: {result.manual_review_csv}")
    print(f"Preflight checklist: {result.preflight_checklist}")
    print(f"Policy profile: {result.policy_profile_id}")


if __name__ == "__main__":
    main()

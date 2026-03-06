from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


SUMMARY_KEYS = (
    "html_files_scanned",
    "html_files_changed",
    "total_automated_changes",
    "manual_review_issues",
    "accessibility_issues",
)


def build_safe_summary(report: dict) -> str:
    summary = report.get("summary", {})
    policy_profile = report.get("policy_profile", {})
    policy_profile_id = str(policy_profile.get("id", "unknown"))
    manual_counts: Counter[str] = Counter()
    a11y_counts: Counter[str] = Counter()

    for file_entry in report.get("files", []):
        for issue in file_entry.get("manual_review_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                manual_counts[reason] += 1
        for issue in file_entry.get("accessibility_issues", []):
            reason = str(issue.get("reason", "")).strip()
            if reason:
                a11y_counts[reason] += 1

    lines = ["SUMMARY", f"policy_profile: {policy_profile_id}"]
    for key in SUMMARY_KEYS:
        lines.append(f"{key}: {summary.get(key, 0)}")

    lines.append("")
    lines.append("MANUAL_REVIEW_REASON_COUNTS")
    if manual_counts:
        for reason, count in manual_counts.most_common():
            lines.append(f"{count} | {reason}")
    else:
        lines.append("0 | none")

    lines.append("")
    lines.append("ACCESSIBILITY_REASON_COUNTS")
    if a11y_counts:
        for reason, count in a11y_counts.most_common():
            lines.append(f"{count} | {reason}")
    else:
        lines.append("0 | none")

    return "\n".join(lines) + "\n"


def build_safe_summary_from_path(report_path: Path) -> str:
    with report_path.open("r", encoding="utf-8") as fh:
        report = json.load(fh)
    return build_safe_summary(report)


def _default_output_path(report_path: Path) -> Path:
    if report_path.name.endswith(".migration-report.json"):
        return report_path.with_name(report_path.name.replace(".migration-report.json", ".safe-summary.txt"))
    return report_path.with_suffix(".safe-summary.txt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-safe-summary",
        description="Generate non-sensitive summary from migration report JSON",
    )
    parser.add_argument("report_json", type=Path, help="Path to *.migration-report.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output text path for safe summary",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.report_json.exists():
        parser.error(f"Report JSON not found: {args.report_json}")

    safe_text = build_safe_summary_from_path(args.report_json)
    output_path = args.output or _default_output_path(args.report_json)
    output_path.write_text(safe_text, encoding="utf-8")
    print(f"Safe summary written: {output_path}")
    print("")
    print(safe_text.rstrip())


if __name__ == "__main__":
    main()

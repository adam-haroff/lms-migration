from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_RESOURCE_TYPES = {
    "page",
    "assignment",
    "discussion",
    "quiz",
    "announcement",
    "syllabus",
}


@dataclass(frozen=True)
class ValidatorIssue:
    resource_title: str
    resource_type: str
    issue_heading: str
    details: tuple[str, ...]


def _normalize_line(value: str) -> str:
    return value.replace("\u2002", " ").replace("\u00a0", " ").strip()


def parse_link_validator_text(text: str) -> list[ValidatorIssue]:
    lines = [_normalize_line(line) for line in text.splitlines()]
    issues: list[ValidatorIssue] = []
    i = 0
    while i < len(lines):
        title = lines[i]
        if not title:
            i += 1
            continue
        if i + 2 >= len(lines):
            break
        resource_type = lines[i + 1].lower()
        issue_heading = lines[i + 2]
        if resource_type not in _RESOURCE_TYPES or "resource" not in issue_heading.lower():
            i += 1
            continue
        i += 3
        details: list[str] = []
        while i < len(lines):
            line = lines[i]
            next_type = line.lower()
            if not line:
                i += 1
                if details:
                    break
                continue
            if (
                i + 2 < len(lines)
                and next_type not in _RESOURCE_TYPES
                and lines[i + 1].lower() in _RESOURCE_TYPES
                and "resource" in lines[i + 2].lower()
            ):
                break
            details.append(line)
            i += 1
        issues.append(
            ValidatorIssue(
                resource_title=title,
                resource_type=resource_type.title(),
                issue_heading=issue_heading,
                details=tuple(details),
            )
        )
    return issues


def _classify_issue(issue: ValidatorIssue) -> tuple[str, str]:
    heading = issue.issue_heading.lower()
    detail_text = " ".join(issue.details).lower()
    if "unpublished content referenced" in heading:
        return (
            "internal_unpublished_reference",
            "Remove the reference, publish the linked item, or remove both unpublished items if they are not needed.",
        )
    if "external links in this resource were unreachable" in heading:
        if any(token in detail_text for token in ("404", "page not found", "content off-line", "offline")):
            return (
                "likely_dead_or_offline_external_link",
                "Replace or remove the link unless a current working URL is found.",
            )
        if any(token in detail_text for token in ("paywall", "subscription", "subscribe")):
            return (
                "likely_paywalled_external_link",
                "Consider replacing the link with an open-access alternative if student access is expected.",
            )
        if any(token in detail_text for token in ("bot", "blocked", "cookie", "javascript", "false positive")):
            return (
                "likely_bot_blocked_or_validator_false_positive",
                "Check the link in a normal browser session before treating it as broken.",
            )
        return (
            "external_unreachable_needs_browser_check",
            "Open the link in a normal browser session and classify it as working, dead, paywalled, or blocked.",
        )
    return (
        "needs_manual_review",
        "Review the item manually and decide whether it should be fixed, removed, or documented.",
    )


def build_link_validator_triage_report(
    *,
    source_text: str,
    output_json_path: Path,
    output_markdown_path: Path | None = None,
) -> dict[str, Any]:
    issues = parse_link_validator_text(source_text)
    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for issue in issues:
        category, recommendation = _classify_issue(issue)
        counts[category] = counts.get(category, 0) + 1
        results.append(
            {
                "resource_title": issue.resource_title,
                "resource_type": issue.resource_type,
                "issue_heading": issue.issue_heading,
                "details": list(issue.details),
                "category": category,
                "recommendation": recommendation,
            }
        )

    report = {
        "summary": {
            "issues": len(results),
            "categories": counts,
        },
        "issues": results,
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if output_markdown_path is not None:
        lines = [
            "# Link Validator Triage Notes",
            "",
            "This report preserves the source issue order and adds a first-pass category plus recommended action.",
            "",
        ]
        for index, item in enumerate(results, start=1):
            lines.append(f"## {index}. {item['resource_title']}")
            lines.append("")
            lines.append(f"- Resource type: `{item['resource_type']}`")
            lines.append(f"- Issue: `{item['issue_heading']}`")
            lines.append(f"- Category: `{item['category']}`")
            if item["details"]:
                lines.append("- Details:")
                for detail in item["details"]:
                    lines.append(f"  - {detail}")
            lines.append(f"- Recommendation: {item['recommendation']}")
            lines.append("")
        output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse pasted Canvas Link Validator text and generate a first-pass triage report."
        )
    )
    parser.add_argument("--input-text", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source_text = args.input_text.read_text(encoding="utf-8")
    report = build_link_validator_triage_report(
        source_text=source_text,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(json.dumps(report["summary"], indent=2))
    print(args.output_json)
    if args.output_markdown:
        print(args.output_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

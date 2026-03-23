"""canvas_a11y_audit.py — Post-import Canvas accessibility audit.

After a D2L export has been imported into Canvas, this module fetches the live
page HTML via the Canvas REST API and runs the same heuristic accessibility
checks that the pre-import pipeline applies.  The result highlights any
regressions introduced by Canvas's own import transformation.

Outputs per run::

    <stem>.a11y-post-import.json   — machine-readable full report
    <stem>.a11y-post-import.md     — human-readable summary

CLI::

    lms-a11y-audit --base-url https://canvas.example.edu \\
                   --course-id 15610 \\
                   --token $CANVAS_TOKEN \\
                   [--pre-import-report output/acc-2321/d2l-export.migration-report.json] \\
                   [--output-dir output/acc-2321]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lms_migration.canvas_api import CanvasAPIError, fetch_course_page, fetch_course_pages
from lms_migration.html_tools import check_accessibility_heuristics


class A11yAuditError(RuntimeError):
    """Raised when the post-import accessibility audit cannot proceed."""


# ─── Data structures ─────────────────────────────────────────────────────────


@dataclass
class PageA11yResult:
    """Accessibility audit result for a single Canvas page."""

    page_url: str
    """Canvas page URL-slug (e.g. ``introduction-to-accounting``)."""

    page_title: str
    """Human-readable page title."""

    canvas_url: str
    """Full API URL used to fetch this page."""

    issues: list[dict[str, str]] = field(default_factory=list)
    """Each issue: ``{"reason": ..., "evidence": ...}``."""

    @property
    def issue_count(self) -> int:
        return len(self.issues)


@dataclass
class A11yAuditResult:
    """Aggregated accessibility audit across all pages in a Canvas course."""

    course_id: str
    base_url: str
    pages_audited: int
    pages_with_issues: int
    total_issues: int
    results: list[PageA11yResult]
    regressions: list[dict[str, Any]] = field(default_factory=list)
    """Issues present post-import that were NOT found pre-import (new regressions)."""


# ─── Core audit ──────────────────────────────────────────────────────────────


def audit_course_pages(
    *,
    base_url: str,
    course_id: str,
    token: str,
) -> A11yAuditResult:
    """Fetch every page in *course_id* and run the heuristic a11y checker.

    Args:
        base_url: Canvas instance root URL (e.g. ``https://canvas.example.edu``).
        course_id: Canvas course ID (numeric or slug string).
        token: Canvas API bearer token.

    Returns:
        :class:`A11yAuditResult` with per-page issues.

    Raises:
        :exc:`A11yAuditError` wrapping any :exc:`CanvasAPIError`.
    """
    try:
        pages = fetch_course_pages(base_url=base_url, course_id=course_id, token=token)
    except CanvasAPIError as exc:
        raise A11yAuditError(f"Failed to fetch page list: {exc}") from exc

    results: list[PageA11yResult] = []

    for page_meta in pages:
        page_url = page_meta.get("url") or page_meta.get("page_id") or ""
        page_title = page_meta.get("title") or page_url
        canvas_url = page_meta.get("html_url") or ""

        if not page_url:
            continue

        try:
            page_data = fetch_course_page(
                base_url=base_url,
                course_id=course_id,
                page_url=page_url,
                token=token,
            )
        except CanvasAPIError:
            # Non-fatal — record the page but skip body analysis
            results.append(
                PageA11yResult(
                    page_url=page_url,
                    page_title=page_title,
                    canvas_url=canvas_url,
                )
            )
            continue

        body = page_data.get("body") or ""
        raw_issues = check_accessibility_heuristics(body)
        issues = [{"reason": i.reason, "evidence": i.evidence} for i in raw_issues]

        results.append(
            PageA11yResult(
                page_url=page_url,
                page_title=page_title,
                canvas_url=canvas_url,
                issues=issues,
            )
        )

    pages_with_issues = sum(1 for r in results if r.issue_count > 0)
    total_issues = sum(r.issue_count for r in results)

    return A11yAuditResult(
        course_id=str(course_id),
        base_url=base_url,
        pages_audited=len(results),
        pages_with_issues=pages_with_issues,
        total_issues=total_issues,
        results=results,
    )


# ─── Regression diff ─────────────────────────────────────────────────────────


def compute_regressions(
    result: A11yAuditResult,
    pre_import_report_path: Path,
) -> list[dict[str, Any]]:
    """Compare *result* against a pre-import migration report and return new issues.

    A "regression" is a page+reason combination that appears post-import but was
    NOT in the pre-import ``accessibility_issues`` list.

    Args:
        result: Post-import audit result.
        pre_import_report_path: Path to the ``d2l-export.migration-report.json``
            (or ``d2l-export.page-review.json``) produced by the pre-import pipeline.

    Returns:
        List of ``{"page_url", "page_title", "reason", "evidence"}`` dicts for
        regressions only.  Returns an empty list if the pre-import report cannot
        be read or does not contain accessibility data.
    """
    try:
        raw = json.loads(pre_import_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    # Build a set of (page_identifier, reason) pairs from the pre-import report.
    pre_issues: set[tuple[str, str]] = set()

    # migration-report.json structure: {"files": [{"file": "...", "accessibility_issues": [...]}]}
    for entry in raw.get("files", []):
        page_identifier = Path(entry.get("file", "")).stem.lower()
        for issue in entry.get("accessibility_issues", []):
            reason = issue.get("reason", "") if isinstance(issue, dict) else str(issue)
            pre_issues.add((page_identifier, reason.lower()))

    # page-review.json structure: list of {"file": "...", "accessibility_issues": [...]}
    if isinstance(raw, list):
        for entry in raw:
            page_identifier = Path(entry.get("file", "")).stem.lower()
            for issue in entry.get("accessibility_issues", []):
                reason = issue.get("reason", "") if isinstance(issue, dict) else str(issue)
                pre_issues.add((page_identifier, reason.lower()))

    regressions: list[dict[str, Any]] = []
    for page_result in result.results:
        # Try to match on the Canvas URL slug vs the D2L filename stem
        slug = page_result.page_url.lower()
        for issue in page_result.issues:
            reason = issue["reason"].lower()
            # A regression: the reason does not appear for this page in the pre-import report
            if (slug, reason) not in pre_issues:
                regressions.append(
                    {
                        "page_url": page_result.page_url,
                        "page_title": page_result.page_title,
                        "reason": issue["reason"],
                        "evidence": issue["evidence"],
                    }
                )

    result.regressions = regressions
    return regressions


# ─── Report writers ───────────────────────────────────────────────────────────


def write_a11y_reports(
    result: A11yAuditResult,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    """Write JSON and Markdown accessibility reports to *output_dir*.

    Args:
        result: Post-import audit result.
        output_dir: Directory to write into (created if absent).
        stem: Filename stem (e.g. ``"d2l-export"``).

    Returns:
        ``(json_path, md_path)``
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{stem}.a11y-post-import.json"
    md_path = output_dir / f"{stem}.a11y-post-import.md"

    # ── JSON ────────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "course_id": result.course_id,
        "base_url": result.base_url,
        "pages_audited": result.pages_audited,
        "pages_with_issues": result.pages_with_issues,
        "total_issues": result.total_issues,
        "regressions_count": len(result.regressions),
        "regressions": result.regressions,
        "results": [
            {
                "page_url": r.page_url,
                "page_title": r.page_title,
                "canvas_url": r.canvas_url,
                "issue_count": r.issue_count,
                "issues": r.issues,
            }
            for r in result.results
        ],
    }
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Markdown ─────────────────────────────────────────────────────────────
    lines: list[str] = [
        "# Post-Import Accessibility Audit",
        "",
        f"**Course ID:** `{result.course_id}`  ",
        f"**Canvas:** {result.base_url}  ",
        f"**Pages audited:** {result.pages_audited}  ",
        f"**Pages with issues:** {result.pages_with_issues}  ",
        f"**Total issues:** {result.total_issues}  ",
        f"**Regressions (new post-import):** {len(result.regressions)}",
        "",
    ]

    if result.regressions:
        lines += [
            "## Regressions — New Issues Introduced by Canvas Import",
            "",
            "These issues were NOT present in the pre-import content.",
            "",
            "| Page | Issue | Evidence |",
            "|------|-------|----------|",
        ]
        for reg in result.regressions:
            title = reg["page_title"].replace("|", "\\|")
            reason = reg["reason"].replace("|", "\\|")
            evidence = reg["evidence"][:80].replace("|", "\\|")
            lines.append(f'| {title} | {reason} | `{evidence}` |')
        lines.append("")

    if result.pages_with_issues:
        lines += [
            "## All Accessibility Issues by Page",
            "",
        ]
        for page_result in result.results:
            if not page_result.issues:
                continue
            lines += [
                f"### {page_result.page_title}",
                "",
                f"**URL:** `{page_result.page_url}`",
                "",
                "| Issue | Evidence |",
                "|-------|----------|",
            ]
            for issue in page_result.issues:
                reason = issue["reason"].replace("|", "\\|")
                evidence = issue["evidence"][:80].replace("|", "\\|")
                lines.append(f"| {reason} | `{evidence}` |")
            lines.append("")
    else:
        lines += [
            "## Result",
            "",
            "No accessibility issues detected on any imported page. ✓",
            "",
        ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ─── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lms-a11y-audit",
        description="Run post-import accessibility checks on Canvas course pages.",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("CANVAS_BASE_URL", ""),
        help="Canvas instance root URL (or set CANVAS_BASE_URL env var).",
    )
    p.add_argument(
        "--course-id",
        default=os.environ.get("CANVAS_COURSE_ID", ""),
        help="Canvas course ID (or set CANVAS_COURSE_ID env var).",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("CANVAS_TOKEN", ""),
        help="Canvas API bearer token (or set CANVAS_TOKEN env var).",
    )
    p.add_argument(
        "--pre-import-report",
        type=Path,
        default=None,
        help=(
            "Optional path to d2l-export.migration-report.json or "
            "d2l-export.page-review.json from the pre-import pipeline. "
            "When provided, regressions (new post-import issues) are highlighted."
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write reports into (default: current directory).",
    )
    p.add_argument(
        "--stem",
        default="d2l-export",
        help="Filename stem for output files (default: d2l-export).",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    for flag, val in [
        ("--base-url", args.base_url),
        ("--course-id", args.course_id),
        ("--token", args.token),
    ]:
        if not val:
            print(f"Error: {flag} is required (or set via env var).", file=sys.stderr)
            sys.exit(1)

    output_dir = args.output_dir or Path(".")
    print(f"Auditing accessibility for Canvas course {args.course_id} …", file=sys.stderr)

    try:
        result = audit_course_pages(
            base_url=args.base_url,
            course_id=args.course_id,
            token=args.token,
        )
    except A11yAuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.pre_import_report and args.pre_import_report.exists():
        compute_regressions(result, args.pre_import_report)

    json_p, md_p = write_a11y_reports(result, output_dir, args.stem)

    print(
        f"  Pages audited    : {result.pages_audited}\n"
        f"  Pages with issues: {result.pages_with_issues}\n"
        f"  Total issues     : {result.total_issues}\n"
        f"  Regressions      : {len(result.regressions)}"
    )
    print(f"\n  Reports written to {output_dir}/")
    print(f"    {json_p.name}")
    print(f"    {md_p.name}")

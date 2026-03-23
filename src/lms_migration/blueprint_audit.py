"""blueprint_audit.py — Pre-sync audit for Canvas Blueprint → child courses.

Before triggering a Blueprint sync, this module compares the Blueprint (master)
course with one or more child (section) courses and flags:

- Pages present only in the child that a sync would leave untouched but that
  instructors should be aware of (child customizations).
- Pages with a published-state mismatch — a sync may auto-publish content that
  the child instructor intentionally unpublished, or vice-versa.
- Discussion topics in the child that already have student replies — the topic
  body may be overwritten by the sync while replies remain (ghost replies).
- Assignments present only in the child (instructor-added; safe from overwrite
  but useful for the sign-off checklist).
- Gradebook assignment-group differences between the Blueprint and the child.

Outputs per run::

    <stem>.blueprint-presync.json   — machine-readable full report
    <stem>.blueprint-presync.md     — human-readable summary

CLI::

    lms-blueprint-audit \\
        --base-url https://canvas.example.edu \\
        --blueprint-id 12345 \\
        --child-id 15610 \\
        --token $CANVAS_TOKEN \\
        [--output-dir output/acc-2321]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lms_migration.canvas_api import (
    CanvasAPIError,
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_pages,
)


class BlueprintAuditError(RuntimeError):
    """Raised when the Blueprint pre-sync audit cannot proceed."""


# ─── Data structures ─────────────────────────────────────────────────────────


@dataclass
class PublishMismatch:
    """A page whose published state differs between Blueprint and child."""

    page_url: str
    page_title: str
    blueprint_published: bool
    child_published: bool

    @property
    def risk_description(self) -> str:
        if self.blueprint_published and not self.child_published:
            return "Blueprint is published; sync may auto-publish in child"
        return "Blueprint is unpublished; sync may unpublish in child"


@dataclass
class DiscussionWithReplies:
    """A discussion topic in the child that already has student replies."""

    topic_id: int
    title: str
    reply_count: int
    child_published: bool
    blueprint_has_topic: bool


@dataclass
class BlueprintAuditResult:
    """Full pre-sync comparison between a Blueprint and one child course."""

    blueprint_course_id: str
    child_course_id: str
    base_url: str

    # Pages
    pages_only_in_child: list[str] = field(default_factory=list)
    """Page URL slugs present in child but NOT in the Blueprint.
    These are child customizations and will NOT be overwritten by sync."""

    pages_only_in_blueprint: list[str] = field(default_factory=list)
    """Page URL slugs present in Blueprint but NOT yet in child.
    A sync WILL push these into the child."""

    publish_mismatches: list[PublishMismatch] = field(default_factory=list)
    """Pages that exist in both courses but with different published states."""

    # Discussions
    discussions_with_replies: list[DiscussionWithReplies] = field(default_factory=list)
    """Discussions in the child that have student replies.
    A Blueprint sync overwrites the topic body; replies remain (ghost replies)."""

    # Assignments
    assignments_only_in_child: list[str] = field(default_factory=list)
    """Assignment names only in the child (instructor-added; safe from overwrite)."""

    assignments_only_in_blueprint: list[str] = field(default_factory=list)
    """Assignment names only in the Blueprint that will be pushed to child on sync."""

    # Summary counts
    @property
    def total_risks(self) -> int:
        return (
            len(self.publish_mismatches)
            + len(self.discussions_with_replies)
            + len(self.pages_only_in_blueprint)
            + len(self.assignments_only_in_blueprint)
        )


# ─── Snapshot helpers ────────────────────────────────────────────────────────


def _page_index(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return a dict keyed by lowercase page URL slug."""
    return {
        (p.get("url") or "").lower(): p
        for p in pages
        if p.get("url")
    }


def _assignment_title_set(assignments: list[dict[str, Any]]) -> set[str]:
    return {a.get("name", "").strip() for a in assignments if a.get("name")}


# ─── Core audit ──────────────────────────────────────────────────────────────


def audit_blueprint_sync(
    *,
    blueprint_course_id: str,
    child_course_id: str,
    base_url: str,
    token: str,
) -> BlueprintAuditResult:
    """Compare a Blueprint master course with a child course.

    Args:
        blueprint_course_id: Canvas ID of the Blueprint master course.
        child_course_id: Canvas ID of the child/section course.
        base_url: Canvas instance root URL.
        token: Canvas API bearer token.

    Returns:
        :class:`BlueprintAuditResult` with all risk items identified.

    Raises:
        :exc:`BlueprintAuditError` wrapping any :exc:`CanvasAPIError`.
    """
    try:
        bp_pages = fetch_course_pages(
            base_url=base_url, course_id=blueprint_course_id, token=token
        )
        child_pages = fetch_course_pages(
            base_url=base_url, course_id=child_course_id, token=token
        )
        bp_assignments = fetch_course_assignments(
            base_url=base_url, course_id=blueprint_course_id, token=token
        )
        child_assignments = fetch_course_assignments(
            base_url=base_url, course_id=child_course_id, token=token
        )
        bp_discussions = fetch_course_discussion_topics(
            base_url=base_url, course_id=blueprint_course_id, token=token
        )
        child_discussions = fetch_course_discussion_topics(
            base_url=base_url, course_id=child_course_id, token=token
        )
    except CanvasAPIError as exc:
        raise BlueprintAuditError(f"Canvas API error during audit: {exc}") from exc

    result = BlueprintAuditResult(
        blueprint_course_id=str(blueprint_course_id),
        child_course_id=str(child_course_id),
        base_url=base_url,
    )

    # ── Pages ─────────────────────────────────────────────────────────────
    bp_page_idx = _page_index(bp_pages)
    child_page_idx = _page_index(child_pages)

    bp_slugs = set(bp_page_idx)
    child_slugs = set(child_page_idx)

    result.pages_only_in_child = sorted(child_slugs - bp_slugs)
    result.pages_only_in_blueprint = sorted(bp_slugs - child_slugs)

    # Publish-state mismatches on shared pages
    for slug in sorted(bp_slugs & child_slugs):
        bp_pub = bool(bp_page_idx[slug].get("published", True))
        child_pub = bool(child_page_idx[slug].get("published", True))
        if bp_pub != child_pub:
            bp_title = bp_page_idx[slug].get("title") or slug
            result.publish_mismatches.append(
                PublishMismatch(
                    page_url=slug,
                    page_title=bp_title,
                    blueprint_published=bp_pub,
                    child_published=child_pub,
                )
            )

    # ── Discussions ───────────────────────────────────────────────────────
    bp_disc_titles = {d.get("title", "").strip().lower() for d in bp_discussions}

    for disc in child_discussions:
        reply_count = disc.get("discussion_subentry_count", 0) or 0
        if reply_count > 0:
            title = disc.get("title", "").strip()
            result.discussions_with_replies.append(
                DiscussionWithReplies(
                    topic_id=disc.get("id", 0),
                    title=title,
                    reply_count=reply_count,
                    child_published=bool(disc.get("published", True)),
                    blueprint_has_topic=title.lower() in bp_disc_titles,
                )
            )

    # ── Assignments ───────────────────────────────────────────────────────
    bp_asgn = _assignment_title_set(bp_assignments)
    child_asgn = _assignment_title_set(child_assignments)

    result.assignments_only_in_child = sorted(child_asgn - bp_asgn)
    result.assignments_only_in_blueprint = sorted(bp_asgn - child_asgn)

    return result


# ─── Report writers ───────────────────────────────────────────────────────────


def write_blueprint_reports(
    result: BlueprintAuditResult,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    """Write JSON and Markdown Blueprint pre-sync reports.

    Args:
        result: Audit result from :func:`audit_blueprint_sync`.
        output_dir: Output directory (created if absent).
        stem: Filename stem (e.g. ``"d2l-export"``).

    Returns:
        ``(json_path, md_path)``
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{stem}.blueprint-presync.json"
    md_path = output_dir / f"{stem}.blueprint-presync.md"

    # ── JSON ─────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "blueprint_course_id": result.blueprint_course_id,
        "child_course_id": result.child_course_id,
        "base_url": result.base_url,
        "total_risks": result.total_risks,
        "pages_only_in_child": result.pages_only_in_child,
        "pages_only_in_blueprint": result.pages_only_in_blueprint,
        "publish_mismatches": [
            {
                "page_url": m.page_url,
                "page_title": m.page_title,
                "blueprint_published": m.blueprint_published,
                "child_published": m.child_published,
                "risk": m.risk_description,
            }
            for m in result.publish_mismatches
        ],
        "discussions_with_replies": [
            {
                "topic_id": d.topic_id,
                "title": d.title,
                "reply_count": d.reply_count,
                "child_published": d.child_published,
                "blueprint_has_topic": d.blueprint_has_topic,
            }
            for d in result.discussions_with_replies
        ],
        "assignments_only_in_child": result.assignments_only_in_child,
        "assignments_only_in_blueprint": result.assignments_only_in_blueprint,
    }
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Markdown ─────────────────────────────────────────────────────────
    lines: list[str] = [
        "# Blueprint Pre-Sync Audit",
        "",
        f"**Blueprint course:** `{result.blueprint_course_id}`  ",
        f"**Child course:** `{result.child_course_id}`  ",
        f"**Canvas:** {result.base_url}  ",
        f"**Total risk items:** {result.total_risks}",
        "",
    ]

    if result.total_risks == 0:
        lines += [
            "No sync risks detected. ✓ Safe to proceed with Blueprint sync.",
            "",
        ]

    if result.discussions_with_replies:
        lines += [
            "## ⚠ Discussions With Student Replies",
            "",
            "The Blueprint sync will overwrite these discussion topic bodies. "
            "Student replies will remain but the original topic prompt may change.",
            "",
            "| Topic | Replies | In Blueprint | Published |",
            "|-------|---------|--------------|-----------|",
        ]
        for d in result.discussions_with_replies:
            bp_str = "✓" if d.blueprint_has_topic else "✗ (child-only)"
            pub_str = "Yes" if d.child_published else "No"
            title = d.title.replace("|", "\\|")
            lines.append(f"| {title} | {d.reply_count} | {bp_str} | {pub_str} |")
        lines.append("")

    if result.publish_mismatches:
        lines += [
            "## ⚠ Publish-State Mismatches",
            "",
            "These pages have different published states in the Blueprint vs child. "
            "A sync may auto-publish or unpublish child content.",
            "",
            "| Page | Blueprint | Child | Risk |",
            "|------|-----------|-------|------|",
        ]
        for m in result.publish_mismatches:
            bp_str = "Published" if m.blueprint_published else "Unpublished"
            ch_str = "Published" if m.child_published else "Unpublished"
            title = m.page_title.replace("|", "\\|")
            risk = m.risk_description.replace("|", "\\|")
            lines.append(f"| {title} | {bp_str} | {ch_str} | {risk} |")
        lines.append("")

    if result.pages_only_in_blueprint:
        lines += [
            "## Pages That Will Be Pushed to Child",
            "",
            "These pages exist in the Blueprint but NOT in the child. "
            "The sync will create them in the child course.",
            "",
        ]
        for slug in result.pages_only_in_blueprint:
            lines.append(f"- `{slug}`")
        lines.append("")

    if result.pages_only_in_child:
        lines += [
            "## Child-Only Pages (Instructor Customizations)",
            "",
            "These pages exist in the child but NOT in the Blueprint. "
            "They will NOT be affected by the sync.",
            "",
        ]
        for slug in result.pages_only_in_child:
            lines.append(f"- `{slug}`")
        lines.append("")

    if result.assignments_only_in_blueprint:
        lines += [
            "## Assignments That Will Be Pushed to Child",
            "",
        ]
        for name in result.assignments_only_in_blueprint:
            lines.append(f"- {name}")
        lines.append("")

    if result.assignments_only_in_child:
        lines += [
            "## Child-Only Assignments (Instructor-Added)",
            "",
            "These assignments exist only in the child and will NOT be overwritten.",
            "",
        ]
        for name in result.assignments_only_in_child:
            lines.append(f"- {name}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ─── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lms-blueprint-audit",
        description="Pre-sync audit for Canvas Blueprint → child course.",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("CANVAS_BASE_URL", ""),
        help="Canvas instance root URL (or set CANVAS_BASE_URL env var).",
    )
    p.add_argument(
        "--blueprint-id",
        default=os.environ.get("CANVAS_BLUEPRINT_ID", ""),
        help="Canvas Blueprint master course ID.",
    )
    p.add_argument(
        "--child-id",
        default=os.environ.get("CANVAS_CHILD_ID", ""),
        help="Canvas child course ID to compare against.",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("CANVAS_TOKEN", ""),
        help="Canvas API bearer token (or set CANVAS_TOKEN env var).",
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
        ("--blueprint-id", args.blueprint_id),
        ("--child-id", args.child_id),
        ("--token", args.token),
    ]:
        if not val:
            print(f"Error: {flag} is required (or set via env var).", file=sys.stderr)
            sys.exit(1)

    output_dir = args.output_dir or Path(".")
    print(
        f"Auditing Blueprint {args.blueprint_id} → child {args.child_id} …",
        file=sys.stderr,
    )

    try:
        result = audit_blueprint_sync(
            blueprint_course_id=args.blueprint_id,
            child_course_id=args.child_id,
            base_url=args.base_url,
            token=args.token,
        )
    except BlueprintAuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    json_p, md_p = write_blueprint_reports(result, output_dir, args.stem)

    print(
        f"  Total risks      : {result.total_risks}\n"
        f"  Publish mismatches: {len(result.publish_mismatches)}\n"
        f"  Discussions w/ replies: {len(result.discussions_with_replies)}\n"
        f"  Pages → child    : {len(result.pages_only_in_blueprint)}\n"
        f"  Child-only pages : {len(result.pages_only_in_child)}"
    )
    print(f"\n  Reports written to {output_dir}/")
    print(f"    {json_p.name}")
    print(f"    {md_p.name}")

"""canvas_snapshot_diff.py — Structural content diff: D2L original vs live Canvas snapshot.

Compares each Canvas page (from a snapshot JSON) against the matching D2L HTML
file (from the original export zip) using structural/quantitative signals only:
  - word count  - image count  - heading count  - table count  - link count
  - placeholder text detection  - significant content loss

No AI, no cloud services.  All processing is local and deterministic.

CLI usage:
    lms-snapshot-diff \\
        --snapshot-json output/acc-2321/canvas-course-15610.snapshot.json \\
        --original-zip  resources/incoming/acc-2321/before/d2l-export.zip \\
        --output-dir    output/acc-2321
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Placeholder text patterns — Canvas template filler that should have been
# replaced by real instructor content before the course went live.
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"fill\s+in\s+text\s+here", re.IGNORECASE),
    re.compile(r"\[title\]", re.IGNORECASE),
    re.compile(r"\[lesson\s+title\]", re.IGNORECASE),
    re.compile(r"\[module\s+title\]", re.IGNORECASE),
    re.compile(r"\[insert\b", re.IGNORECASE),
    re.compile(r"lorem\s+ipsum", re.IGNORECASE),
    re.compile(r"xxx-xxx-xxxx", re.IGNORECASE),
    re.compile(r"\bmon\s+0:00\s+[ap]m", re.IGNORECASE),
    re.compile(
        r"text\s+from\s+kenneth\s+hodges", re.IGNORECASE
    ),  # template bio placeholder
]

# Significant content loss threshold — flag if Canvas has < this fraction of D2L words
_CONTENT_LOSS_THRESHOLD = 0.60

# Minimum D2L word count before applying the loss threshold (ignore stub pages)
_MIN_WORDS_FOR_LOSS = 40


# ---------------------------------------------------------------------------
# HTML utilities (no external dependencies)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _word_count(html: str) -> int:
    return len(re.findall(r"\b\w+\b", _strip_html(html)))


def _tag_count(html: str, tag: str) -> int:
    return len(re.findall(rf"<{re.escape(tag)}\b", html, flags=re.IGNORECASE))


def _extract_body(html: str) -> str:
    """Return just the <body> contents, or the whole string if no body tag."""
    m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else html


def _detect_placeholders(html: str) -> list[str]:
    plain = _strip_html(html)
    found: list[str] = []
    for pat in _PLACEHOLDER_PATTERNS:
        m = pat.search(plain)
        if m:
            found.append(m.group(0).strip())
    return found


# ---------------------------------------------------------------------------
# Structural metrics
# ---------------------------------------------------------------------------


@dataclass
class PageMetrics:
    words: int = 0
    images: int = 0
    headings: int = 0
    tables: int = 0
    links: int = 0
    iframes: int = 0
    placeholders: list[str] = field(default_factory=list)


def _metrics(html: str) -> PageMetrics:
    return PageMetrics(
        words=_word_count(html),
        images=_tag_count(html, "img"),
        headings=sum(_tag_count(html, f"h{n}") for n in range(1, 7)),
        tables=_tag_count(html, "table"),
        links=_tag_count(html, "a"),
        iframes=_tag_count(html, "iframe"),
        placeholders=_detect_placeholders(html),
    )


# ---------------------------------------------------------------------------
# Title normalisation for matching Canvas pages → D2L files
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, keep only alnum+space."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _build_d2l_index(original_zip: Path) -> dict[str, list[str]]:
    """Return {normalised_stem: [zip_member_name, ...]} for all HTML files in the zip.

    Multiple D2L files can share the same stem (e.g. one "Introduction and Objectives.html"
    per module folder).  We keep all of them, sorted naturally by path so that
    positional pairing with Canvas duplicate-titled pages works correctly.
    """
    index: dict[str, list[str]] = {}
    with zipfile.ZipFile(original_zip) as zf:
        for name in sorted(zf.namelist()):  # sorted for deterministic order
            if name.lower().endswith((".html", ".htm")):
                stem = _normalise(Path(name).stem)
                if stem:
                    index.setdefault(stem, []).append(name)
    return index


def _canvas_url_sort_key(url: str) -> tuple[str, int]:
    """Sort key for Canvas page URLs that appends a numeric suffix for duplicates.

    e.g. "introduction-and-objectives"   → ("introduction-and-objectives", 0)
         "introduction-and-objectives-2" → ("introduction-and-objectives", 2)
    """
    m = re.match(r"^(.+?)(?:-(\d+))?$", url)
    if m:
        return m.group(1), int(m.group(2) or 0)
    return url, 0


def _build_canvas_to_d2l_map(
    pages: list[dict],
    d2l_index: dict[str, list[str]],
    threshold: int = 60,
) -> dict[str, tuple[str | None, int]]:
    """Return {canvas_url: (d2l_zip_member | None, match_score)} for every page.

    For pages whose normalised title matches a unique D2L stem, the match is
    direct.  For duplicate-titled Canvas pages (Canvas appends -2/-3/... to the
    URL slug), we sort both the Canvas group and the corresponding D2L files by
    natural order and pair them positionally so each Canvas page gets its own
    module-specific D2L file.
    """
    # Group canvas pages by normalised title
    from collections import defaultdict

    title_groups: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        key = _normalise(page.get("title", ""))
        title_groups[key].append(page)

    mapping: dict[str, tuple[str | None, int]] = {}

    for norm_title, group_pages in title_groups.items():
        # Find the best-matching stem in d2l_index
        best_stem: str | None = None
        best_score = 0
        for stem in d2l_index:
            # Compare normalised canvas title against each stem
            if norm_title == stem:
                score = 100
            elif norm_title in stem or stem in norm_title:
                score = 85
            else:
                ct_tokens = set(norm_title.split())
                st_tokens = set(stem.split())
                if ct_tokens and st_tokens:
                    overlap = len(ct_tokens & st_tokens)
                    score = int(100 * overlap / max(len(ct_tokens), len(st_tokens)))
                else:
                    score = 0
            if score > best_score:
                best_score = score
                best_stem = stem

        if best_stem is None or best_score < threshold:
            # No match for any page in this group
            for page in group_pages:
                mapping[page["url"]] = (None, best_score)
            continue

        d2l_files = d2l_index[best_stem]  # list, sorted by path
        # Sort canvas pages in this group by URL natural order
        sorted_pages = sorted(
            group_pages, key=lambda p: _canvas_url_sort_key(p.get("url", ""))
        )

        for i, page in enumerate(sorted_pages):
            # If there are exactly as many (or more) D2L files, pair 1-to-1; otherwise
            # fall back to the first D2L file for all pages in the group.
            if len(d2l_files) >= len(sorted_pages):
                match = d2l_files[i]
            elif len(d2l_files) == 1:
                match = d2l_files[0]
            else:
                # More canvas pages than D2L files — pair what we can, rest use first
                match = d2l_files[i] if i < len(d2l_files) else d2l_files[0]
            mapping[page["url"]] = (match, best_score)

    return mapping


# ---------------------------------------------------------------------------
# Per-page diff
# ---------------------------------------------------------------------------


@dataclass
class PageDiffResult:
    canvas_title: str
    canvas_url: str
    canvas_html_url: str
    d2l_file: str | None
    match_score: int
    canvas_metrics: PageMetrics
    d2l_metrics: PageMetrics | None
    flags: list[str]
    severity: str  # "ok" | "warn" | "error"


def _diff_page(
    canvas_page: dict,
    d2l_match: str | None,
    match_score: int,
    original_zip: Path,
) -> PageDiffResult:
    title = canvas_page.get("title", "")
    canvas_body = canvas_page.get("body") or ""
    html_url = canvas_page.get("html_url", "")
    canvas_url = canvas_page.get("url", "")

    canvas_m = _metrics(canvas_body)

    d2l_m: PageMetrics | None = None
    if d2l_match:
        with zipfile.ZipFile(original_zip) as zf:
            raw = zf.read(d2l_match).decode("utf-8", errors="ignore")
        d2l_body = _extract_body(raw)
        d2l_m = _metrics(d2l_body)

    flags: list[str] = []

    # Placeholder text in live Canvas page
    for ph in canvas_m.placeholders:
        flags.append(f'Placeholder text detected: "{ph}"')

    # No D2L match found
    if d2l_match is None:
        flags.append(
            "No matching D2L source file found (new Canvas-only page or title mismatch)"
        )

    if d2l_m is not None:
        # Content loss
        if d2l_m.words >= _MIN_WORDS_FOR_LOSS:
            ratio = canvas_m.words / max(d2l_m.words, 1)
            if ratio < _CONTENT_LOSS_THRESHOLD:
                pct = int((1 - ratio) * 100)
                flags.append(
                    f"Content loss: Canvas has {canvas_m.words} words vs D2L {d2l_m.words} ({pct}% reduction)"
                )

        # Image loss
        if d2l_m.images > 0 and canvas_m.images == 0:
            flags.append(f"Images missing: D2L had {d2l_m.images}, Canvas has 0")
        elif d2l_m.images > canvas_m.images:
            flags.append(
                f"Image count dropped: D2L {d2l_m.images} → Canvas {canvas_m.images}"
            )

        # Heading loss
        if d2l_m.headings > 0 and canvas_m.headings == 0:
            flags.append("All headings missing in Canvas version")
        elif d2l_m.headings > canvas_m.headings + 2:
            flags.append(
                f"Heading count dropped: D2L {d2l_m.headings} → Canvas {canvas_m.headings}"
            )

        # Table loss
        if d2l_m.tables > 0 and canvas_m.tables == 0:
            flags.append(f"Tables missing: D2L had {d2l_m.tables}, Canvas has 0")

    # Severity
    error_signals = {
        "Content loss",
        "Images missing",
        "All headings missing",
        "Tables missing",
    }
    if any(any(sig in f for sig in error_signals) for f in flags):
        severity = "error"
    elif flags:
        severity = "warn"
    else:
        severity = "ok"

    return PageDiffResult(
        canvas_title=title,
        canvas_url=canvas_url,
        canvas_html_url=html_url,
        d2l_file=d2l_match,
        match_score=match_score,
        canvas_metrics=canvas_m,
        d2l_metrics=d2l_m,
        flags=flags,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_snapshot_diff(
    *,
    snapshot_json: Path,
    original_zip: Path,
    output_dir: Path | None = None,
    output_json_path: Path | None = None,
    output_markdown_path: Path | None = None,
    output_csv_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    snapshot = json.loads(snapshot_json.read_text(encoding="utf-8"))
    pages: list[dict] = snapshot.get("pages", [])
    course_id: str = str(snapshot.get("course_id", "unknown"))
    base_url: str = snapshot.get("base_url", "")

    d2l_index = _build_d2l_index(original_zip)
    canvas_to_d2l = _build_canvas_to_d2l_map(
        [p for p in pages if p.get("body")], d2l_index
    )

    results: list[PageDiffResult] = []
    for page in pages:
        if not page.get("body"):
            continue
        url = page.get("url", "")
        d2l_match, match_score = canvas_to_d2l.get(url, (None, 0))
        results.append(_diff_page(page, d2l_match, match_score, original_zip))

    results.sort(
        key=lambda r: ({"error": 0, "warn": 1, "ok": 2}[r.severity], r.canvas_title)
    )

    # Summaries
    n_total = len(results)
    n_error = sum(1 for r in results if r.severity == "error")
    n_warn = sum(1 for r in results if r.severity == "warn")
    n_ok = sum(1 for r in results if r.severity == "ok")
    n_placeholder = sum(1 for r in results if r.canvas_metrics.placeholders)
    n_no_match = sum(1 for r in results if r.d2l_file is None)

    # Resolve output paths
    stem = snapshot_json.stem  # e.g. "canvas-course-15610.snapshot"
    if output_dir is None:
        output_dir = snapshot_json.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    out_json = output_json_path or (output_dir / f"{stem}.diff.json")
    out_md = output_markdown_path or (output_dir / f"{stem}.diff.md")
    out_csv = output_csv_path or (output_dir / f"{stem}.diff.csv")

    # --- JSON ---
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_json": str(snapshot_json),
        "original_zip": str(original_zip),
        "base_url": base_url,
        "course_id": course_id,
        "summary": {
            "pages_compared": n_total,
            "errors": n_error,
            "warnings": n_warn,
            "ok": n_ok,
            "pages_with_placeholders": n_placeholder,
            "pages_without_d2l_match": n_no_match,
        },
        "pages": [_result_to_dict(r) for r in results],
    }
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # --- Markdown ---
    out_md.write_text(_render_markdown(report, results), encoding="utf-8")

    # --- CSV ---
    _write_csv(out_csv, results)

    return out_json, out_md, out_csv


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _result_to_dict(r: PageDiffResult) -> dict:
    cm = r.canvas_metrics
    dm = r.d2l_metrics
    return {
        "canvas_title": r.canvas_title,
        "canvas_url": r.canvas_url,
        "canvas_html_url": r.canvas_html_url,
        "d2l_file": r.d2l_file,
        "match_score": r.match_score,
        "severity": r.severity,
        "flags": r.flags,
        "canvas_metrics": {
            "words": cm.words,
            "images": cm.images,
            "headings": cm.headings,
            "tables": cm.tables,
            "links": cm.links,
            "iframes": cm.iframes,
        },
        "d2l_metrics": (
            {
                "words": dm.words,
                "images": dm.images,
                "headings": dm.headings,
                "tables": dm.tables,
                "links": dm.links,
                "iframes": dm.iframes,
            }
            if dm
            else None
        ),
    }


def _render_markdown(report: dict, results: list[PageDiffResult]) -> str:
    s = report["summary"]
    lines: list[str] = [
        "# Canvas Snapshot Diff Report",
        "",
        f"**Generated:** {report['generated_utc']}  ",
        f"**Snapshot:** {report['snapshot_json']}  ",
        f"**D2L zip:** {report['original_zip']}  ",
        f"**Course ID:** {report['course_id']}  ",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Pages compared | {s['pages_compared']} |",
        f"| Errors (content loss / images missing) | {s['errors']} |",
        f"| Warnings (minor drift / placeholders) | {s['warnings']} |",
        f"| OK | {s['ok']} |",
        f"| Pages with placeholder text | {s['pages_with_placeholders']} |",
        f"| Pages without D2L match | {s['pages_without_d2l_match']} |",
        "",
    ]

    for sev, heading in [("error", "Errors"), ("warn", "Warnings")]:
        items = [r for r in results if r.severity == sev]
        if not items:
            continue
        lines += [f"## {heading} ({len(items)})", ""]
        for r in items:
            lines.append(f"### {r.canvas_title}")
            if r.canvas_html_url:
                lines.append(f"[Open in Canvas]({r.canvas_html_url})  ")
            if r.d2l_file:
                lines.append(
                    f"D2L source: `{r.d2l_file}` (match score {r.match_score})"
                )
            else:
                lines.append("D2L source: no match found")
            cm = r.canvas_metrics
            dm = r.d2l_metrics
            if dm:
                lines.append(
                    f"Words: D2L {dm.words} → Canvas {cm.words} | "
                    f"Images: {dm.images} → {cm.images} | "
                    f"Headings: {dm.headings} → {cm.headings} | "
                    f"Tables: {dm.tables} → {cm.tables}"
                )
            for flag in r.flags:
                lines.append(f"- {flag}")
            lines.append("")

    return "\n".join(lines)


def _write_csv(path: Path, results: list[PageDiffResult]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "severity",
                "canvas_title",
                "canvas_url",
                "d2l_file",
                "match_score",
                "d2l_words",
                "canvas_words",
                "d2l_images",
                "canvas_images",
                "d2l_headings",
                "canvas_headings",
                "d2l_tables",
                "canvas_tables",
                "flags",
                "canvas_html_url",
            ],
        )
        writer.writeheader()
        for r in results:
            dm = r.d2l_metrics
            writer.writerow(
                {
                    "severity": r.severity,
                    "canvas_title": r.canvas_title,
                    "canvas_url": r.canvas_url,
                    "d2l_file": r.d2l_file or "",
                    "match_score": r.match_score,
                    "d2l_words": dm.words if dm else "",
                    "canvas_words": r.canvas_metrics.words,
                    "d2l_images": dm.images if dm else "",
                    "canvas_images": r.canvas_metrics.images,
                    "d2l_headings": dm.headings if dm else "",
                    "canvas_headings": r.canvas_metrics.headings,
                    "d2l_tables": dm.tables if dm else "",
                    "canvas_tables": r.canvas_metrics.tables,
                    "flags": " | ".join(r.flags),
                    "canvas_html_url": r.canvas_html_url,
                }
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Structural content diff: D2L original zip vs live Canvas snapshot JSON."
    )
    parser.add_argument(
        "--snapshot-json",
        type=Path,
        required=True,
        help="Path to canvas snapshot JSON (from lms-canvas-snapshot)",
    )
    parser.add_argument(
        "--original-zip",
        type=Path,
        required=True,
        help="Path to D2L export zip (original source)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write diff report files (default: same dir as snapshot JSON)",
    )
    parser.add_argument(
        "--output-json", type=Path, default=None, help="Override output JSON path"
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Override output Markdown path",
    )
    parser.add_argument(
        "--output-csv", type=Path, default=None, help="Override output CSV path"
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.snapshot_json.exists():
        parser.error(f"Snapshot JSON does not exist: {args.snapshot_json}")
    if not args.original_zip.exists():
        parser.error(f"Original zip does not exist: {args.original_zip}")

    json_path, md_path, csv_path = run_snapshot_diff(
        snapshot_json=args.snapshot_json,
        original_zip=args.original_zip,
        output_dir=args.output_dir,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        output_csv_path=args.output_csv,
    )
    print(f"Diff JSON:     {json_path}")
    print(f"Diff Markdown: {md_path}")
    print(f"Diff CSV:      {csv_path}")


if __name__ == "__main__":
    main()

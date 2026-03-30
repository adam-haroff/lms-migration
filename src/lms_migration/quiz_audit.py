"""Quiz audit tool — parses D2L QTI quiz XML files from a D2L export zip.

Extracts per-quiz metadata:
  - title, id, question count, question types
  - time limit, enforce-time-limit flag, attempts allowed
  - availability window (start / end / due dates)
  - shuffle settings

Flags Canvas New Quizzes compatibility risks per question type and emits:
  - JSON report   (<basename>.quiz-audit.json)
  - Markdown report (<basename>.quiz-audit.md)

CLI: lms-quiz-audit
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# New Quizzes compatibility reference
# ---------------------------------------------------------------------------

# Question types that migrate cleanly to New Quizzes without manual work.
_CLEAN_TYPES: frozenset[str] = frozenset(
    {
        "Multiple Choice",
        "True/False",
        "Multi-Select",
        "Written Response",
        "Short Answer",
        "Fill in the Blanks",
        "Matching",
    }
)

# Types that need instructor attention or manual rebuild.
# Format: type → (risk_level, note)
_RISK_TYPES: dict[str, tuple[str, str]] = {
    "Ordering": (
        "P1",
        "Ordering questions are not natively supported in Canvas New Quizzes; "
        "rebuild as Matching or Written Response.",
    ),
    "Arithmetic": (
        "P1",
        "Arithmetic/Calculated questions require manual recreation in New Quizzes "
        "using the Formula/Calculated Numeric question type.",
    ),
    "Calculated": (
        "P1",
        "Calculated questions require manual recreation in New Quizzes "
        "using the Formula/Calculated Numeric question type.",
    ),
    "Significant Figures": (
        "P1",
        "Significant Figures questions are not directly supported; "
        "rebuild as Numeric/Formula in New Quizzes.",
    ),
    "Likert": (
        "P2",
        "Likert questions are not supported in New Quizzes; "
        "convert to survey or Written Response.",
    ),
    "Multi-Short-Answer": (
        "P2",
        "Multi-Short-Answer questions may not migrate correctly; "
        "verify each question after import.",
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class QuizInfo:
    quiz_file: str
    quiz_id: str
    title: str
    question_count: int
    question_types: dict[str, int]  # type → count
    time_limit_minutes: int | None  # None = not set
    enforce_time_limit: bool
    attempts_allowed: int  # 0 = unlimited
    has_availability_window: bool
    date_start: str
    date_end: str
    date_due: str
    shuffle_type: str  # "none", "answers", "questions", "both", "unknown"
    grade_item_resource_code: str
    questiondb_item_count: int
    random_question_order: bool
    compatibility_flags: list[dict] = field(default_factory=list)


@dataclass
class QuizAuditReport:
    source_zip: str
    quiz_count: int
    quizzes: list[QuizInfo]
    summary_flags: list[dict]  # aggregated P1/P2 compatibility flags


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_D2L_NS = "http://desire2learn.com/xsd/d2lcp_v2p0"
_D2L_PREFIX = "d2l_2p0"


def _d2l_tag(local: str) -> str:
    return f"{{{_D2L_NS}}}{local}"


def _text_of(element, tag: str) -> str:
    """Return text content of a direct child element, or empty string."""
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _d2l_text(element, local: str) -> str:
    """Return text content of a D2L-namespaced child element."""
    child = element.find(_d2l_tag(local))
    return (child.text or "").strip() if child is not None else ""


def _parse_quiz_xml(filename: str, content: str) -> QuizInfo:
    """Parse a single D2L QTI quiz XML string and return a QuizInfo."""
    # Register the D2L namespace so find() works with prefix
    # We re-parse with regex for some fields because D2L's XML mixes
    # namespace-prefixed attributes with body elements.

    import xml.etree.ElementTree as ET

    # Strip the XML declaration if present (can have encoding="UTF-8" issues)
    if content.startswith("<?xml"):
        content = re.sub(r"<\?xml[^>]*\?>", "", content, count=1)

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Fallback: return a minimal stub so we don't crash the whole audit
        return QuizInfo(
            quiz_file=filename,
            quiz_id="PARSE_ERROR",
            title=filename,
            question_count=0,
            question_types={},
            time_limit_minutes=None,
            enforce_time_limit=False,
            attempts_allowed=0,
            has_availability_window=False,
            date_start="",
            date_end="",
            date_due="",
            shuffle_type="unknown",
            grade_item_resource_code="",
            questiondb_item_count=0,
            random_question_order=False,
            compatibility_flags=[
                {
                    "level": "P1",
                    "type": "PARSE_ERROR",
                    "note": f"Failed to parse {filename} as valid XML.",
                }
            ],
        )

    # Locate the <assessment> element (immediate child of questestinterop or root)
    assessment = root if root.tag.endswith("assessment") else root.find(".//assessment")
    if assessment is None:
        assessment = root  # fallback

    quiz_id = assessment.get(f"{{{_D2L_NS}}}id") or assessment.get("ident") or ""
    title = assessment.get("title") or filename

    # --- assess_procextension (D2L-specific settings) ---
    proc_ext = assessment.find(".//assess_procextension")

    time_limit_minutes: int | None = None
    enforce_time_limit = False
    attempts_allowed = 0
    date_start = ""
    date_end = ""
    date_due = ""
    grade_item_rc = ""

    if proc_ext is not None:
        tl = _d2l_text(proc_ext, "time_limit")
        if tl and tl.isdigit():
            # D2L stores time_limit in minutes
            time_limit_minutes = int(tl)

        etl = _d2l_text(proc_ext, "enforce_time_limit")
        enforce_time_limit = etl.lower() == "yes"

        att = _d2l_text(proc_ext, "attempts_allowed")
        if att.lstrip("-").isdigit():
            attempts_allowed = int(att)

        ds = _d2l_text(proc_ext, "date_start")
        date_start = ds if ds else ""
        de = _d2l_text(proc_ext, "date_end")
        date_end = de if de else ""
        dd = _d2l_text(proc_ext, "date_due")
        date_due = dd if dd else ""

        # Grade item resource code (links quiz to gradebook)
        gi = proc_ext.find("grade_item")
        if gi is not None:
            grade_item_rc = (
                gi.get(f"{{{_D2L_NS}}}is_autoexport")
                and gi.get("resource_code")
                or gi.get("resource_code")
                or ""
            )

    has_availability_window = bool(date_start or date_end or date_due)

    # --- Question types ---
    question_types: Counter[str] = Counter()
    for field_label in assessment.findall(".//fieldlabel"):
        if (field_label.text or "").strip() == "qmd_questiontype":
            # Sibling fieldentry
            parent = field_label  # ElementTree doesn't expose parent directly
            # Walk up via regex on raw content instead
            pass

    # Regex fallback for question types (more reliable than ElementTree for this)
    qt_matches = re.findall(
        r"<fieldlabel>qmd_questiontype</fieldlabel>\s*<fieldentry>(.*?)</fieldentry>",
        content,
    )
    for qt in qt_matches:
        question_types[qt.strip()] += 1

    question_count = len(question_types) and sum(question_types.values())

    # --- Shuffle settings ---
    # D2L uses d2l_2p0:shuffle on <section> or within assess_procextension
    shuffle_raw = ""
    section = assessment.find(".//section")
    if section is not None:
        shuffle_raw = section.get(f"{{{_D2L_NS}}}shuffle") or ""
    if not shuffle_raw:
        # Try regex on raw content for shuffle attribute
        m = re.search(r'd2l_2p0:shuffle="([^"]*)"', content)
        if m:
            shuffle_raw = m.group(1)

    shuffle_map = {
        "0": "none",
        "1": "answers",
        "2": "questions",
        "3": "both",
        "no": "none",
        "yes": "questions",  # D2L "yes" typically means shuffle questions
    }
    shuffle_type = shuffle_map.get(
        shuffle_raw.lower(), "none" if not shuffle_raw else "unknown"
    )

    questiondb_item_count = len(
        re.findall(
            r"<itemref\b[^>]*>.*?<[^>:\s]*:file\b[^>]*href=\"questiondb\.xml\"",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    random_question_order = bool(
        re.search(
            r"<selection_ordering>\s*<order\b[^>]*order_type=\"random\"",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    # --- Compatibility flags ---
    flags: list[dict] = []
    for qtype, count in sorted(question_types.items()):
        if qtype in _RISK_TYPES:
            level, note = _RISK_TYPES[qtype]
            flags.append(
                {
                    "level": level,
                    "type": qtype,
                    "count": count,
                    "note": note,
                }
            )

    return QuizInfo(
        quiz_file=filename,
        quiz_id=quiz_id,
        title=title,
        question_count=question_count,
        question_types=dict(question_types),
        time_limit_minutes=time_limit_minutes,
        enforce_time_limit=enforce_time_limit,
        attempts_allowed=attempts_allowed,
        has_availability_window=has_availability_window,
        date_start=date_start,
        date_end=date_end,
        date_due=date_due,
        shuffle_type=shuffle_type,
        grade_item_resource_code=grade_item_rc,
        questiondb_item_count=questiondb_item_count,
        random_question_order=random_question_order,
        compatibility_flags=flags,
    )


# ---------------------------------------------------------------------------
# Main audit function
# ---------------------------------------------------------------------------


def audit_quizzes(zip_path: Path) -> QuizAuditReport:
    """Parse all quiz_d2l_*.xml files from a D2L export zip and return an audit report."""
    quizzes: list[QuizInfo] = []

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        quiz_files = [
            n for n in names if re.match(r"quiz_d2l_\d+\.xml$", n.rsplit("/", 1)[-1])
        ]

        for fname in sorted(quiz_files):
            content = zf.read(fname).decode("utf-8", errors="replace")
            quiz_info = _parse_quiz_xml(fname, content)
            quizzes.append(quiz_info)

    # Aggregate compatibility flags across all quizzes
    flag_counter: Counter[tuple[str, str]] = Counter()
    for q in quizzes:
        for f in q.compatibility_flags:
            flag_counter[(f["level"], f["type"])] += f.get("count", 1)

    summary_flags = [
        {
            "level": level,
            "type": qtype,
            "total_questions": count,
            "note": _RISK_TYPES.get(qtype, ("", "UNKNOWN"))[1],
        }
        for (level, qtype), count in sorted(flag_counter.items())
    ]

    return QuizAuditReport(
        source_zip=str(zip_path),
        quiz_count=len(quizzes),
        quizzes=quizzes,
        summary_flags=summary_flags,
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _attempts_label(attempts: int) -> str:
    return "Unlimited" if attempts == 0 else str(attempts)


def _time_label(minutes: int | None, enforced: bool) -> str:
    if minutes is None:
        return "No time limit"
    suffix = " (enforced)" if enforced else " (not enforced)"
    return f"{minutes} min{suffix}"


def write_json_report(report: QuizAuditReport, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_markdown_report(report: QuizAuditReport, output_path: Path) -> None:
    lines = [
        "# Quiz Audit Report",
        "",
        f"- Source: `{report.source_zip}`",
        f"- Quizzes found: **{report.quiz_count}**",
        "",
    ]

    # Summary compatibility flags
    if report.summary_flags:
        lines += [
            "## Compatibility Flags (New Quizzes)",
            "",
            "| Level | Question Type | Count | Action |",
            "| ----- | ------------- | ----- | ------ |",
        ]
        for f in report.summary_flags:
            lines.append(
                f"| {f['level']} | {f['type']} | {f['total_questions']} | {f['note']} |"
            )
        lines.append("")
    else:
        lines += [
            "## Compatibility Flags",
            "",
            "No compatibility issues detected — all question types are supported in Canvas New Quizzes.",
            "",
        ]

    # Per-quiz detail
    lines += ["## Quiz Details", ""]
    for q in report.quizzes:
        lines.append(f"### {q.title}")
        lines.append("")
        lines.append(f"- **File:** `{q.quiz_file}`")
        lines.append(f"- **ID:** {q.quiz_id}")
        lines.append(f"- **Questions:** {q.question_count}")
        lines.append(
            f"- **Time limit:** {_time_label(q.time_limit_minutes, q.enforce_time_limit)}"
        )
        lines.append(f"- **Attempts allowed:** {_attempts_label(q.attempts_allowed)}")
        lines.append(f"- **Shuffle:** {q.shuffle_type}")

        if q.has_availability_window:
            parts = []
            if q.date_start:
                parts.append(f"start: {q.date_start}")
            if q.date_end:
                parts.append(f"end: {q.date_end}")
            if q.date_due:
                parts.append(f"due: {q.date_due}")
            lines.append(f"- **Availability window:** {', '.join(parts)}")
        else:
            lines.append("- **Availability window:** Not set in export")

        if q.question_types:
            type_parts = ", ".join(
                f"{t} ×{c}" for t, c in sorted(q.question_types.items())
            )
            lines.append(f"- **Question types:** {type_parts}")

        if q.questiondb_item_count:
            lines.append(
                "- **Question bank references:** "
                f"{q.questiondb_item_count} question(s) sourced from `questiondb.xml`"
            )
        if q.random_question_order:
            lines.append("- **Random question order in D2L:** Yes")

        if q.compatibility_flags:
            lines.append("- **Compatibility flags:**")
            for f in q.compatibility_flags:
                lines.append(
                    f"  - ({f['level']}) **{f['type']}** ×{f['count']}: {f['note']}"
                )

        lines.append("")

    # New Quizzes migration notes
    lines += [
        "## New Quizzes Migration Notes",
        "",
        "All quizzes in this course must be migrated to **Canvas New Quizzes** (institution mandate).",
        "The following steps are required for every quiz:",
        "",
        "1. After Canvas import, open each quiz and confirm it was imported as New Quizzes.",
        "   If it shows as Classic Quiz, convert it via **Quiz Settings → Migrate**.",
        "2. Verify question count, answer keys, feedback, and point values match D2L.",
        "3. Reconfigure time limit and attempt settings (these do not always transfer).",
        "4. For quizzes with availability dates: re-enter open/close/until dates in Canvas.",
        "5. For quizzes with question banks/random draws: verify draw counts and bank sharing.",
        "6. For quizzes sourcing questions from `questiondb.xml`: verify each imported question, point value, and any shared media. Rebuild as Canvas Item Banks if editing or coordinator sharing is needed.",
        "7. If D2L randomized question order, re-enable the equivalent shuffle behavior in Canvas New Quizzes and verify order-sensitive questions still work correctly.",
        "",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-quiz-audit",
        description=(
            "Audit D2L quiz XML files from a D2L export zip. "
            "Extracts quiz settings, question types, and flags New Quizzes compatibility issues."
        ),
    )
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Path to D2L export .zip file containing quiz_d2l_*.xml files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for reports. Defaults to the same directory as the zip.",
    )
    parser.add_argument(
        "--basename",
        type=str,
        default=None,
        help="Base name for output files. Defaults to the zip stem.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    zip_path: Path = args.zip_path
    if not zip_path.exists():
        parser.error(f"Zip file not found: {zip_path}")

    output_dir: Path = args.output_dir or zip_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    basename: str = args.basename or zip_path.stem

    report = audit_quizzes(zip_path)

    json_path = output_dir / f"{basename}.quiz-audit.json"
    md_path = output_dir / f"{basename}.quiz-audit.md"

    write_json_report(report, json_path)
    write_markdown_report(report, md_path)

    print(f"Quizzes found: {report.quiz_count}")
    if report.summary_flags:
        p1 = sum(1 for f in report.summary_flags if f["level"] == "P1")
        p2 = sum(1 for f in report.summary_flags if f["level"] == "P2")
        print(f"Compatibility flags: {p1} P1, {p2} P2")
    else:
        print("Compatibility flags: none")
    print(f"JSON report:     {json_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    main()

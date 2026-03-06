from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile


DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class Signal:
    id: str
    phrase: str
    rationale: str


@dataclass(frozen=True)
class CoverageSignal:
    id: str
    label: str
    keywords: tuple[str, ...]


INSTRUCTION_SIGNALS = (
    Signal(
        id="abbreviations",
        phrase="Abbreviations to use in workflows",
        rationale="Useful for consistent shorthand and downstream parser hints.",
    ),
    Signal(
        id="mc_closer",
        phrase="All Module Checklists (MC) must end with the following bullet",
        rationale="Can become an automated template compliance check.",
    ),
    Signal(
        id="syllabus_migration",
        phrase="D2L to Canvas Syllabus Information",
        rationale="Provides deterministic migration guidance for syllabus sections.",
    ),
    Signal(
        id="no_retroactive_changes",
        phrase="Do not retroactively revise previously approved content unless explicitly instructed.",
        rationale="Important guardrail for trust and revision discipline.",
    ),
    Signal(
        id="module_checklist_alignment",
        phrase="Module Checklist",
        rationale="Should be included in alignment checks when content changes.",
    ),
)


BEST_PRACTICE_SIGNALS = (
    CoverageSignal(
        id="quiz_no_time_limit",
        label="Quiz time-limit migration caveat",
        keywords=("time limit", "timing & display", "quiz migration"),
    ),
    CoverageSignal(
        id="scorm_handling",
        label="SCORM special handling",
        keywords=("scorm", "add \"scorm\" to the navigation toolbar"),
    ),
    CoverageSignal(
        id="h5p_handling",
        label="H5P manual handling",
        keywords=("h5p",),
    ),
    CoverageSignal(
        id="announcement_import_quirks",
        label="Announcement import behavior caveat",
        keywords=("announcement", "notifications", "generic user"),
    ),
    CoverageSignal(
        id="detect_multiple_sessions",
        label="Avoid Detect Multiple Sessions",
        keywords=("detect multiple", "multiple sessions"),
    ),
    CoverageSignal(
        id="panopto_permissions",
        label="Panopto permissions/folder workflow",
        keywords=("panopto", "anyone with the link", "personal folder"),
    ),
    CoverageSignal(
        id="youtube_ads",
        label="YouTube ads / Studio hosting guidance",
        keywords=("youtube", "ads", "canvas studio"),
    ),
    CoverageSignal(
        id="undelete",
        label="Restore deleted content via /undelete",
        keywords=("undelete", "restore deleted items"),
    ),
)


def _normalize(text: str) -> str:
    lowered = text.lower().strip()
    lowered = lowered.replace("“", "\"").replace("”", "\"").replace("’", "'")
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _read_docx_text(path: Path) -> str:
    with ZipFile(path, "r") as zf:
        with zf.open("word/document.xml", "r") as fh:
            xml_data = fh.read()
    root = ET.fromstring(xml_data)
    paragraphs: list[str] = []
    for p in root.findall(".//w:p", DOCX_NS):
        runs = []
        for t in p.findall(".//w:t", DOCX_NS):
            runs.append(t.text or "")
        paragraph = "".join(runs).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n".join(paragraphs)


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx_text(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _line_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized = _normalize(stripped)
        if normalized and normalized not in mapping:
            mapping[normalized] = stripped
    return mapping


def _instruction_gap_analysis(source_text: str, draft_text: str) -> dict:
    source_map = _line_map(source_text)
    draft_map = _line_map(draft_text)
    source_set = set(source_map.keys())
    draft_set = set(draft_map.keys())

    missing_lines = [
        source_map[key]
        for key in sorted(source_set - draft_set)
        if len(source_map[key]) >= 25
    ][:40]

    extra_lines = [
        draft_map[key]
        for key in sorted(draft_set - source_set)
        if len(draft_map[key]) >= 25
    ][:40]

    source_norm = _normalize(source_text)
    draft_norm = _normalize(draft_text)
    critical_gaps = []
    for signal in INSTRUCTION_SIGNALS:
        in_source = _normalize(signal.phrase) in source_norm
        in_draft = _normalize(signal.phrase) in draft_norm
        if in_source and not in_draft:
            critical_gaps.append(
                {
                    "id": signal.id,
                    "phrase": signal.phrase,
                    "rationale": signal.rationale,
                }
            )

    return {
        "source_line_count": len(source_map),
        "draft_line_count": len(draft_map),
        "missing_lines_sample": missing_lines,
        "extra_lines_sample": extra_lines,
        "critical_gaps": critical_gaps,
    }


def _coverage(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = _normalize(text)
    return any(_normalize(keyword) in lowered for keyword in keywords)


def _best_practice_coverage_analysis(
    best_practices_text: str,
    rules_text: str,
    findings_text: str,
) -> dict:
    rows = []
    for signal in BEST_PRACTICE_SIGNALS:
        in_best_practices = _coverage(best_practices_text, signal.keywords)
        in_rules = _coverage(rules_text, signal.keywords)
        in_findings = _coverage(findings_text, signal.keywords)
        rows.append(
            {
                "id": signal.id,
                "label": signal.label,
                "covered_in_best_practices_docx": in_best_practices,
                "covered_in_rules": in_rules,
                "covered_in_existing_findings": in_findings,
                "action_needed": bool(in_best_practices and (not in_rules and not in_findings)),
            }
        )
    return {
        "coverage_rows": rows,
        "action_needed_count": sum(1 for row in rows if row["action_needed"]),
    }


def _template_placeholder_analysis(page_template_text: str, syllabus_template_text: str) -> dict:
    placeholder_patterns = (
        r"\[instructor note:.*?\]",
        r"fill in text here",
        r"\[title here\]",
        r"\[activities or learning topics\]",
        r"\[assignment titles\]",
    )
    full_text = "\n".join((page_template_text, syllabus_template_text))
    normalized = _normalize(full_text)

    matched = []
    for pattern in placeholder_patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            matched.append(pattern)

    has_required_mc_closer = (
        _normalize("Contact your instructor with any questions or post in the Course Q&A.")
        in normalized
    )

    return {
        "placeholder_patterns_detected": matched,
        "module_checklist_required_closer_present": has_required_mc_closer,
    }


def _render_markdown(report: dict) -> str:
    instruction = report["instruction_comparison"]
    coverage = report["best_practices_coverage"]
    template = report["template_analysis"]

    lines = [
        "# Reference Audit Report",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Inputs",
        "",
        f"- Instructions docx: `{report['inputs']['instructions_docx']}`",
        f"- Existing draft markdown: `{report['inputs']['draft_markdown']}`",
        f"- Best practices docx: `{report['inputs']['best_practices_docx']}`",
        f"- Page templates docx: `{report['inputs']['page_templates_docx']}`",
        f"- Syllabus template docx: `{report['inputs']['syllabus_template_docx']}`",
        f"- Rules JSON: `{report['inputs']['rules_json']}`",
        f"- Existing findings markdown: `{report['inputs']['findings_markdown']}`",
        "",
        "## Instruction Comparison",
        "",
        f"- Source lines: {instruction['source_line_count']}",
        f"- Draft lines: {instruction['draft_line_count']}",
        f"- Critical gaps: {len(instruction['critical_gaps'])}",
        "",
    ]

    if instruction["critical_gaps"]:
        lines.append("### Critical Gaps")
        lines.append("")
        for gap in instruction["critical_gaps"]:
            lines.append(f"- `{gap['id']}`: {gap['phrase']}")
            lines.append(f"  - Why it matters: {gap['rationale']}")
        lines.append("")

    if instruction["missing_lines_sample"]:
        lines.append("### Missing Line Sample (From Source)")
        lines.append("")
        for item in instruction["missing_lines_sample"][:15]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(
        [
            "## Best Practices Coverage",
            "",
            f"- Topics requiring new coverage: {coverage['action_needed_count']}",
            "",
        ]
    )

    for row in coverage["coverage_rows"]:
        lines.append(
            f"- `{row['id']}` | docx:{row['covered_in_best_practices_docx']} "
            f"| rules:{row['covered_in_rules']} | findings:{row['covered_in_existing_findings']} "
            f"| action_needed:{row['action_needed']} | {row['label']}"
        )
    lines.append("")

    lines.extend(
        [
            "## Template Analysis",
            "",
            f"- Required MC closing bullet present in template docs: {template['module_checklist_required_closer_present']}",
            "- Placeholder patterns detected:",
        ]
    )
    for pattern in template["placeholder_patterns_detected"]:
        lines.append(f"- `{pattern}`")
    lines.append("")

    lines.extend(
        [
            "## Recommended App/Process Improvements",
            "",
            "- Add template QA checks for unresolved placeholders and missing required checklist closer.",
            "- Add instruction-profile support (abbreviations, module gating prompts, syllabus migration guidance) as an optional, non-default mode.",
            "- Expand best-practice audit to include process caveats not currently encoded in rules (for example Detect Multiple Sessions and /undelete runbook).",
            "- Keep these as explicit reports/checks first; do not auto-rewrite policy-sensitive content.",
            "",
        ]
    )

    return "\n".join(lines)


def run_reference_audit(
    instructions_docx: Path,
    draft_markdown: Path,
    best_practices_docx: Path,
    page_templates_docx: Path,
    syllabus_template_docx: Path,
    rules_json: Path,
    findings_markdown: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    instructions_text = _read_text(instructions_docx)
    draft_text = _read_text(draft_markdown)
    best_practices_text = _read_text(best_practices_docx)
    page_templates_text = _read_text(page_templates_docx)
    syllabus_template_text = _read_text(syllabus_template_docx)
    rules_text = _read_text(rules_json)
    findings_text = _read_text(findings_markdown)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "instructions_docx": str(instructions_docx),
            "draft_markdown": str(draft_markdown),
            "best_practices_docx": str(best_practices_docx),
            "page_templates_docx": str(page_templates_docx),
            "syllabus_template_docx": str(syllabus_template_docx),
            "rules_json": str(rules_json),
            "findings_markdown": str(findings_markdown),
        },
        "instruction_comparison": _instruction_gap_analysis(instructions_text, draft_text),
        "best_practices_coverage": _best_practice_coverage_analysis(
            best_practices_text=best_practices_text,
            rules_text=rules_text,
            findings_text=findings_text,
        ),
        "template_analysis": _template_placeholder_analysis(
            page_template_text=page_templates_text,
            syllabus_template_text=syllabus_template_text,
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "reference-audit.json"
    md_path = output_dir / "reference-audit.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-reference-audit",
        description="Compare reference docs and produce migration-app improvement report",
    )
    parser.add_argument("--instructions-docx", type=Path, required=True)
    parser.add_argument("--best-practices-docx", type=Path, required=True)
    parser.add_argument("--page-templates-docx", type=Path, required=True)
    parser.add_argument("--syllabus-template-docx", type=Path, required=True)
    parser.add_argument(
        "--draft-markdown",
        type=Path,
        default=Path("docs/lms-migration-custom-instructions-draft.md"),
    )
    parser.add_argument(
        "--rules-json",
        type=Path,
        default=Path("rules/sinclair_pilot_rules.json"),
    )
    parser.add_argument(
        "--findings-markdown",
        type=Path,
        default=Path("docs/pdf-best-practices-initial-findings.md"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/reference_audit"),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    required_paths = (
        args.instructions_docx,
        args.best_practices_docx,
        args.page_templates_docx,
        args.syllabus_template_docx,
        args.draft_markdown,
        args.rules_json,
        args.findings_markdown,
    )
    for path in required_paths:
        if not path.exists():
            parser.error(f"Required path does not exist: {path}")

    json_path, md_path = run_reference_audit(
        instructions_docx=args.instructions_docx,
        draft_markdown=args.draft_markdown,
        best_practices_docx=args.best_practices_docx,
        page_templates_docx=args.page_templates_docx,
        syllabus_template_docx=args.syllabus_template_docx,
        rules_json=args.rules_json,
        findings_markdown=args.findings_markdown,
        output_dir=args.output_dir,
    )

    print(f"Reference audit JSON: {json_path}")
    print(f"Reference audit Markdown: {md_path}")


if __name__ == "__main__":
    main()

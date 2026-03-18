from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_NORMALIZE_SPACE_RE = re.compile(r"\s+")


def read_reference_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    if path.suffix.lower() == ".docx":
        with ZipFile(path, "r") as zf:
            with zf.open("word/document.xml", "r") as fh:
                xml_data = fh.read()
        root = ET.fromstring(xml_data)
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", _DOCX_NS):
            runs = []
            for token in paragraph.findall(".//w:t", _DOCX_NS):
                runs.append(token.text or "")
            text = "".join(runs).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


def default_reference_doc_paths(workspace_root: Path) -> dict[str, Path | None]:
    helpers_dir = workspace_root / "resources" / "helpers"

    def first_existing(*names: str) -> Path | None:
        for name in names:
            candidate = helpers_dir / name
            if candidate.exists():
                return candidate
        return None

    return {
        "instructions_docx": first_existing("Customize ChatGPT for D2L to Canvas Migrations.docx"),
        "best_practices_docx": first_existing(
            "Canvas Blueprints - Best Practices-20260316.docx",
            "Canvas Blueprints - Best Practices.docx",
        ),
        "setup_checklist_docx": first_existing("Templated Course Set-Up Essentials Checklist.docx"),
        "page_templates_docx": first_existing("Canvas Page Templates.docx"),
        "syllabus_template_docx": first_existing("Canvas Syllabus Page Template.docx"),
    }


def _normalize(text: str) -> str:
    lowered = text.lower().strip()
    lowered = lowered.replace("“", "\"").replace("”", "\"").replace("’", "'")
    return _NORMALIZE_SPACE_RE.sub(" ", lowered)


def _first_matching_line(text: str, *keywords: str) -> str:
    normalized_keywords = tuple(_normalize(keyword) for keyword in keywords if keyword)
    if not normalized_keywords:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized_line = _normalize(stripped)
        if any(keyword in normalized_line for keyword in normalized_keywords):
            return stripped
    return ""


def parse_best_practice_policy(best_practices_docx: Path | None) -> dict:
    text = read_reference_text(best_practices_docx)
    normalized = _normalize(text)

    def contains(*phrases: str) -> bool:
        return any(_normalize(phrase) in normalized for phrase in phrases if phrase)

    pipes_deprecated = contains(
        "we are no longer using the bar",
        "no longer using the bar",
    )
    accessible_accordion_allowed = contains(
        "accessible accordion code",
        "you can also now use the accessible accordion code",
    )
    module_prefix_expected = contains(
        "module # before the name",
        "module 1:",
    )

    return {
        "source_path": str(best_practices_docx) if best_practices_docx is not None else "",
        "pipes_deprecated": pipes_deprecated,
        "accessible_accordion_allowed": accessible_accordion_allowed,
        "module_prefix_expected": module_prefix_expected,
        "title_policy_excerpt": _first_matching_line(
            text,
            "no longer using the bar",
            "naming pattern is now module 1:",
        ),
        "accordion_policy_excerpt": _first_matching_line(
            text,
            "accessible accordion code",
            "recreate content in a simplified manner",
        ),
    }

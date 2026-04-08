from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .canvas_api import (
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_files,
    fetch_course_folders,
    normalize_base_url,
    update_course_assignment_description,
    update_discussion_topic_message,
)
from .canvas_post_import import _build_file_index, _build_folder_path_index


_TOP_HEADING_STYLE = (
    "color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;"
)
_SECTION_HEADING_STYLE = "color: #ac1a2f;"
_SUPPORT_BLOCK_STYLE = "background-color: #f8f8f8; padding: 15px;"


def _normalize_html_body(value: str) -> str:
    return (value or "").strip()


def _first_paragraph_and_remainder(html_text: str) -> tuple[str, str]:
    match = re.search(r"\A\s*(<p\b[^>]*>.*?</p>)", html_text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return "", html_text.strip()
    first = match.group(1).strip()
    remainder = (html_text[: match.start()] + html_text[match.end() :]).strip()
    return first, remainder


def _looks_templated(html_text: str, *, overview_label: str) -> bool:
    lowered = html_text.lower()
    return overview_label.lower() in lowered or "technical support" in lowered


def _icon_url(file_index: dict[str, list], course_id: str, basename: str) -> str:
    matches = file_index.get(basename.lower(), [])
    if len(matches) != 1:
        return ""
    return f"/courses/{course_id}/files/{matches[0].file_id}/preview"


def _heading(title: str, *, icon_url: str, top: bool) -> str:
    style = _TOP_HEADING_STYLE if top else _SECTION_HEADING_STYLE
    icon_html = (
        f'<img role="presentation" src="{icon_url}" alt="" width="45" height="45"> '
        if icon_url
        else ""
    )
    return f'<h2 style="{style}">{icon_html}<strong>{title}</strong></h2>'


def _support_block(*, icon_url: str, tool_name: str, help_href: str) -> str:
    icon_html = (
        f'<img role="presentation" src="{icon_url}" alt="" width="45" height="45"> '
        if icon_url
        else ""
    )
    return (
        f'<div style="{_SUPPORT_BLOCK_STYLE}">\n'
        f"<h2>{icon_html}<strong>Technical Support</strong></h2>\n"
        f'<p>Need help using Canvas {tool_name}? If so, please review the following page: '
        f'<a href="{help_href}" target="_blank" rel="noopener">Canvas Resources for Students - {tool_name}.</a></p>\n'
        "</div>"
    )


def wrap_assignment_description(
    *,
    body_html: str,
    course_id: str,
    file_index: dict[str, list],
) -> str:
    body = _normalize_html_body(body_html)
    if not body or _looks_templated(body, overview_label="Assignment Overview"):
        return body

    first, remainder = _first_paragraph_and_remainder(body)
    overview = first or "<p>Complete the assignment described below and submit your work in Canvas.</p>"
    instructions = remainder or body

    parts = [
        _heading(
            "Assignment Overview",
            icon_url=_icon_url(file_index, course_id, "pencil.png"),
            top=True,
        ),
        overview,
        "<hr />",
        _heading(
            "Instructions",
            icon_url=_icon_url(file_index, course_id, "flag.png"),
            top=False,
        ),
        instructions,
        "<hr />",
        _support_block(
            icon_url=_icon_url(file_index, course_id, "gear.png"),
            tool_name="Assignments",
            help_href="https://design.instructure.com/courses/178/pages/assignments",
        ),
    ]
    return "\n".join(part for part in parts if part.strip())


def wrap_discussion_message(
    *,
    body_html: str,
    course_id: str,
    file_index: dict[str, list],
) -> str:
    body = _normalize_html_body(body_html)
    if not body or _looks_templated(body, overview_label="Discussion Overview"):
        return body

    first, remainder = _first_paragraph_and_remainder(body)
    overview = (
        first
        or "<p>Review the discussion instructions below and post your response in Canvas.</p>"
    )
    prompt = remainder or body

    parts = [
        _heading(
            "Discussion Overview",
            icon_url=_icon_url(file_index, course_id, "discussion.png"),
            top=True,
        ),
        overview,
    ]
    if prompt.strip():
        parts.extend(["<h3>Prompt</h3>", prompt, "<hr />"])
    parts.append(
        _support_block(
            icon_url=_icon_url(file_index, course_id, "gear.png"),
            tool_name="Discussions",
            help_href="https://design.instructure.com/courses/178/pages/discussions",
        )
    )
    return "\n".join(part for part in parts if part.strip())


def wrap_quiz_description(
    *,
    body_html: str,
    course_id: str,
    file_index: dict[str, list],
    title: str,
) -> str:
    body = _normalize_html_body(body_html)
    heading_label = "Exam Overview" if re.search(r"\bexam\b", title, flags=re.IGNORECASE) else "Quiz Overview"
    if not body or _looks_templated(body, overview_label=heading_label):
        return body

    first, remainder = _first_paragraph_and_remainder(body)
    overview = (
        first
        or (
            "<p>In this assessment, you will demonstrate your understanding of the material "
            "covered in this course.</p>"
            if heading_label == "Exam Overview"
            else "<p>In this quiz, you will check your understanding of the material presented in this module.</p>"
        )
    )
    details = remainder or body

    parts = [
        _heading(
            heading_label,
            icon_url="",
            top=True,
        ),
        overview,
    ]
    if details.strip():
        parts.append(details)
    parts.extend(
        [
            "<hr />",
            _support_block(
                icon_url=_icon_url(file_index, course_id, "gear.png"),
                tool_name="Quizzes",
                help_href="https://design.instructure.com/courses/178/pages/quizzes",
            ),
        ]
    )
    return "\n".join(part for part in parts if part.strip())


def auto_wrap_assessment_descriptions(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    normalized_base = normalize_base_url(base_url)
    files = fetch_course_files(base_url=normalized_base, course_id=course_id, token=token)
    folders = fetch_course_folders(base_url=normalized_base, course_id=course_id, token=token)
    folder_paths = _build_folder_path_index(folders)
    file_index, collisions = _build_file_index(files, folder_paths=folder_paths)

    assignments = fetch_course_assignments(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    discussions = fetch_course_discussion_topics(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    assignment_updates: list[dict] = []
    discussion_updates: list[dict] = []
    assignments_updated = 0
    discussions_updated = 0

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        assignment_id = assignment.get("id")
        if not assignment_id:
            continue
        title = str(assignment.get("name", "")).strip()
        body = str(assignment.get("description") or "").strip()
        if not body:
            continue

        if assignment.get("is_quiz_assignment"):
            wrapped = wrap_quiz_description(
                body_html=body,
                course_id=str(course_id),
                file_index=file_index,
                title=title,
            )
            kind = "quiz"
        else:
            wrapped = wrap_assignment_description(
                body_html=body,
                course_id=str(course_id),
                file_index=file_index,
            )
            kind = "assignment"
        changed = wrapped != body
        if changed and not dry_run:
            update_course_assignment_description(
                base_url=normalized_base,
                course_id=course_id,
                assignment_id=assignment_id,
                description_html=wrapped,
                token=token,
            )
        if changed:
            assignments_updated += 1
        assignment_updates.append(
            {
                "id": assignment_id,
                "title": title,
                "kind": kind,
                "changed": changed,
            }
        )

    for discussion in discussions:
        if not isinstance(discussion, dict):
            continue
        topic_id = discussion.get("id")
        if not topic_id:
            continue
        title = str(discussion.get("title", "")).strip()
        body = str(discussion.get("message") or "").strip()
        if not body:
            continue
        wrapped = wrap_discussion_message(
            body_html=body,
            course_id=str(course_id),
            file_index=file_index,
        )
        changed = wrapped != body
        if changed and not dry_run:
            update_discussion_topic_message(
                base_url=normalized_base,
                course_id=course_id,
                topic_id=topic_id,
                message_html=wrapped,
                token=token,
            )
        if changed:
            discussions_updated += 1
        discussion_updates.append(
            {
                "id": topic_id,
                "title": title,
                "changed": changed,
            }
        )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "base_url": normalized_base,
        "course_id": str(course_id),
        "dry_run": bool(dry_run),
        "summary": {
            "assignments_scanned": len(assignment_updates),
            "assignments_updated": assignments_updated,
            "discussions_scanned": len(discussion_updates),
            "discussions_updated": discussions_updated,
            "colliding_file_basenames": len(collisions),
        },
        "assignments": assignment_updates,
        "discussions": discussion_updates,
    }
    output_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-wrap-assessments",
        description="Apply standard template wrappers to Canvas assignment, discussion, and quiz descriptions post-import.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("output/canvas-assessment-wrap-report.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    report_path = auto_wrap_assessment_descriptions(
        base_url=args.base_url,
        course_id=args.course_id,
        token=args.token,
        output_json_path=args.output_json,
        dry_run=bool(args.dry_run),
    )
    print(f"Assessment wrap report JSON: {report_path}")

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .canvas_api import (
    fetch_course_assignments,
    fetch_course_discussion_topics,
    fetch_course_page,
    fetch_course_pages,
    fetch_new_quiz_items,
    fetch_new_quizzes,
    normalize_base_url,
    update_course_assignment_description,
    update_course_page_body,
    update_discussion_topic_message,
    update_new_quiz_item_body,
)
from .math_tools import count_orphaned_wiris_payloads, repair_orphaned_wiris_payloads


def repair_mathml_json_in_html(html_text: str) -> tuple[str, int]:
    return repair_orphaned_wiris_payloads(html_text)


def auto_fix_canvas_mathml(
    *,
    base_url: str,
    course_id: str,
    token: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    normalized_base = normalize_base_url(base_url)

    page_summaries = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
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
    new_quizzes = fetch_new_quizzes(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )

    page_results: list[dict[str, object]] = []
    assignment_results: list[dict[str, object]] = []
    discussion_results: list[dict[str, object]] = []
    quiz_item_results: list[dict[str, object]] = []

    pages_updated = 0
    assignments_updated = 0
    discussions_updated = 0
    quiz_items_updated = 0
    bank_entries_skipped = 0
    total_payloads_repaired = 0

    for page_summary in page_summaries:
        if not isinstance(page_summary, dict):
            continue
        page_url = str(page_summary.get("url") or "").strip()
        if not page_url:
            continue
        page = fetch_course_page(
            base_url=normalized_base,
            course_id=course_id,
            page_url=page_url,
            token=token,
        )
        body = str(page.get("body") or "")
        if not body:
            continue
        detected = count_orphaned_wiris_payloads(body)
        fixed_body, repaired = repair_mathml_json_in_html(body)
        changed = fixed_body != body
        if changed and not dry_run:
            update_course_page_body(
                base_url=normalized_base,
                course_id=course_id,
                page_url=page_url,
                body_html=fixed_body,
                token=token,
            )
        if changed:
            pages_updated += 1
        total_payloads_repaired += repaired
        page_results.append(
            {
                "page_url": page_url,
                "title": str(page.get("title") or "").strip(),
                "detected_payloads": detected,
                "repaired_payloads": repaired,
                "changed": changed,
            }
        )

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        assignment_id = assignment.get("id")
        if assignment_id in (None, ""):
            continue
        body = str(assignment.get("description") or "")
        if not body:
            continue
        detected = count_orphaned_wiris_payloads(body)
        fixed_body, repaired = repair_mathml_json_in_html(body)
        changed = fixed_body != body
        if changed and not dry_run:
            update_course_assignment_description(
                base_url=normalized_base,
                course_id=course_id,
                assignment_id=assignment_id,
                description_html=fixed_body,
                token=token,
            )
        if changed:
            assignments_updated += 1
        total_payloads_repaired += repaired
        assignment_results.append(
            {
                "assignment_id": str(assignment_id),
                "title": str(assignment.get("name") or "").strip(),
                "detected_payloads": detected,
                "repaired_payloads": repaired,
                "changed": changed,
            }
        )

    for discussion in discussions:
        if not isinstance(discussion, dict):
            continue
        topic_id = discussion.get("id")
        if topic_id in (None, ""):
            continue
        body = str(discussion.get("message") or "")
        if not body:
            continue
        detected = count_orphaned_wiris_payloads(body)
        fixed_body, repaired = repair_mathml_json_in_html(body)
        changed = fixed_body != body
        if changed and not dry_run:
            update_discussion_topic_message(
                base_url=normalized_base,
                course_id=course_id,
                topic_id=topic_id,
                message_html=fixed_body,
                token=token,
            )
        if changed:
            discussions_updated += 1
        total_payloads_repaired += repaired
        discussion_results.append(
            {
                "topic_id": str(topic_id),
                "title": str(discussion.get("title") or "").strip(),
                "detected_payloads": detected,
                "repaired_payloads": repaired,
                "changed": changed,
            }
        )

    for quiz in new_quizzes:
        if not isinstance(quiz, dict):
            continue
        assignment_id = str(quiz.get("id") or "").strip()
        if not assignment_id:
            continue
        quiz_title = str(quiz.get("title") or "").strip()
        items = fetch_new_quiz_items(
            base_url=normalized_base,
            course_id=course_id,
            assignment_id=assignment_id,
            token=token,
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            entry_type = str(item.get("entry_type") or "").strip()
            entry = item.get("entry") or {}
            if not item_id or not isinstance(entry, dict):
                continue

            result: dict[str, object] = {
                "quiz_title": quiz_title,
                "assignment_id": assignment_id,
                "item_id": item_id,
                "entry_type": entry_type,
            }

            if entry_type.lower() in {"bankentry", "bank"}:
                bank_entries_skipped += 1
                result.update(
                    {
                        "changed": False,
                        "skipped_reason": "bank-backed item is not editable through the supported New Quizzes API",
                        "bank_id": str(entry.get("id") or "").strip(),
                        "bank_title": str(entry.get("title") or "").strip(),
                    }
                )
                quiz_item_results.append(result)
                continue

            item_body = str(entry.get("item_body") or "")
            detected = count_orphaned_wiris_payloads(item_body)
            if not item_body and detected == 0:
                continue
            fixed_body, repaired = repair_mathml_json_in_html(item_body)
            changed = fixed_body != item_body
            if changed and not dry_run:
                update_new_quiz_item_body(
                    base_url=normalized_base,
                    course_id=course_id,
                    assignment_id=assignment_id,
                    item_id=item_id,
                    item_body_html=fixed_body,
                    token=token,
                    entry_type=entry_type or None,
                )
            if changed:
                quiz_items_updated += 1
            total_payloads_repaired += repaired
            result.update(
                {
                    "detected_payloads": detected,
                    "repaired_payloads": repaired,
                    "changed": changed,
                    "title": str(entry.get("title") or "").strip(),
                }
            )
            quiz_item_results.append(result)

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "course_id": str(course_id),
        "dry_run": dry_run,
        "summary": {
            "pages_updated": pages_updated,
            "assignments_updated": assignments_updated,
            "discussions_updated": discussions_updated,
            "quiz_items_updated": quiz_items_updated,
            "bank_entries_skipped": bank_entries_skipped,
            "total_updates": pages_updated
            + assignments_updated
            + discussions_updated
            + quiz_items_updated,
            "total_payloads_repaired": total_payloads_repaired,
        },
        "pages": page_results,
        "assignments": assignment_results,
        "discussions": discussion_results,
        "new_quiz_items": quiz_item_results,
    }
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair orphaned WIRIS/MathML JSON payloads in live Canvas pages, assignments, discussions, and direct New Quiz items."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", required=True, dest="output_json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    auto_fix_canvas_mathml(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        output_json_path=Path(args.output_json),
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChecklistItem:
    priority: str
    source: str
    category: str
    owner: str
    description: str
    action: str
    count: int = 1
    reference: str = ""


def _priority_rank(priority: str) -> int:
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(priority.strip().upper(), 9)


def _map_canvas_issue(issue: dict) -> ChecklistItem:
    description = str(issue.get("description", "")).strip()
    reference = str(issue.get("fix_issue_html_url", "")).strip()
    lowered = description.lower()

    if "couldn't determine the correct answers" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="quiz_answer_key",
            owner="Faculty/ID",
            description=description,
            action="Open the question and set/verify correct answer(s), points, and feedback in Canvas.",
            reference=reference,
        )

    if "missing links found in imported content - wiki page body" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_page_link",
            owner="ID",
            description=description,
            action="Open page, relink missing resources to Canvas destinations, save, and republish.",
            reference=reference,
        )

    if "missing links found in imported content - announcement message" in lowered:
        return ChecklistItem(
            priority="P2",
            source="canvas_import",
            category="missing_announcement_link",
            owner="ID",
            description=description,
            action="Edit announcement links to Canvas resources or remove broken legacy links.",
            reference=reference,
        )

    if "missing links found in imported content - assignment description" in lowered:
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_assignment_link",
            owner="ID",
            description=description,
            action="Open assignment instructions, relink missing resources to Canvas files/pages, and resave.",
            reference=reference,
        )

    if (
        "missing links found in imported content - assessment question question_text"
        in lowered
    ):
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="missing_assessment_question_link",
            owner="Faculty/ID",
            description=description,
            action="Open quiz question text and relink or remove unresolved references.",
            reference=reference,
        )

    if description.startswith("Import Error: Module Item - "):
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="module_item_import_error",
            owner="ID",
            description=description,
            action="Open module and resolve missing/failed item import, replacing with the correct Canvas item.",
            reference=reference,
        )

    if description.startswith("Import Error: Quiz - "):
        return ChecklistItem(
            priority="P1",
            source="canvas_import",
            category="quiz_import_error",
            owner="Faculty/ID",
            description=description,
            action="Open quiz settings/questions and rebuild missing items as needed.",
            reference=reference,
        )

    return ChecklistItem(
        priority="P2",
        source="canvas_import",
        category="canvas_import_warning",
        owner="ID",
        description=description or "Canvas import warning",
        action="Review warning and resolve in Canvas.",
        reference=reference,
    )


def _map_manual_review_group(issue_type: str, reason: str) -> tuple[str, str, str, str]:
    lowered = reason.lower()

    if "legacy script blocks" in lowered:
        return (
            "P1",
            "html_script_cleanup",
            "ID",
            "Remove legacy script behavior and verify page rendering in Canvas.",
        )
    if "d2l media library content detected" in lowered:
        return (
            "P1",
            "d2l_media_library_migration",
            "ID/Faculty",
            "Upload D2L-hosted media files to Canvas Studio or Canvas course Files, "
            "then update each embed/link to point to the new Canvas location. "
            "D2L ouFileId and /d2l/lp/media/ URLs will not resolve after migration.",
        )
    if "email-based submission workflow detected" in lowered:
        return (
            "P1",
            "email_submission_workflow",
            "Faculty/ID",
            "Replace the email submission instruction with a Canvas Assignment. "
            "In Canvas: Assignments > + Assignment > set Submission Type = Online > "
            "check File Uploads and/or Text Entry. Remove or replace the mailto: link "
            "in the page content so students are directed to the Canvas assignment "
            "instead of emailing their work. Set the correct point value and due date.",
        )
    if "graded discussion detected" in lowered:
        return (
            "P1",
            "graded_discussion_setup",
            "ID/Faculty",
            "Open the Canvas discussion imported from D2L, enable grading (Graded type), "
            "set the point value, and attach the appropriate assignment group. "
            "D2L graded discussion scoring does not transfer automatically.",
        )
    if "d2l dropbox assignment detected" in lowered:
        return (
            "P1",
            "dropbox_assignment_setup",
            "ID/Faculty",
            "Canvas does not import D2L Dropbox folders automatically (proprietary resource type). "
            "For each assignment: (1) create a new Canvas Assignment; (2) set the submission type "
            "(File Upload, Text Entry, etc.) and point value from the evidence column; (3) re-enter "
            "due date and availability window; (4) attach any rubric listed in the evidence; "
            "(5) publish when ready. Verify that the assignment appears in the correct assignment group.",
        )
    if "unresolvable grade item" in lowered:
        return (
            "P1",
            "unresolvable_grade_item_setup",
            "ID/Faculty",
            "This grade item has no D2L submission object (quiz, dropbox, or discussion) "
            "that Canvas can import automatically. After migration, this item will appear "
            "as an orphaned grade column in Canvas with no student submission mechanism. "
            "To fix: (1) determine the correct submission type (external tool, file upload, "
            "online text entry, etc.) from the original D2L course; (2) create a Canvas "
            "Assignment (or configure the relevant external tool integration) with the "
            "point value shown in the evidence column; (3) place it in the correct Canvas "
            "assignment group; (4) if the submission is via an external LTI tool (e.g., "
            "Cengage, MyOpenMath, Pearson), verify that tool is installed in Canvas "
            "Settings > Apps and that grade passback is enabled.",
        )
    if "availability window detected in gradebook item" in lowered:
        return (
            "P2",
            "assignment_availability_window",
            "Faculty/Course Coordinator",
            "Re-enter the availability window (available from / until dates) on each "
            "affected Canvas assignment or quiz. D2L gradebook availability dates are "
            "not imported by the standard Canvas migration and must be set manually.",
        )
    if "gradebook category with drop rule" in lowered:
        return (
            "P1",
            "gradebook_drop_rule_setup",
            "ID",
            "In Canvas Grades, open Assignment Groups and set the 'Rules' for this group: "
            "enter the number of lowest (and/or highest) scores to drop, matching the D2L "
            "category configuration shown in the evidence column. "
            "Canvas does not import D2L drop rules automatically.",
        )
    if "gradebook category weight" in lowered:
        return (
            "P1",
            "gradebook_group_weights",
            "ID",
            "Verify the Canvas assignment group weight matches the D2L category weight "
            "shown in the evidence column (Grades > Assignment Groups > edit group > weight). "
            "Enable 'Weight final grade' in Assignments if not already on. "
            "Incorrect weights directly affect final grade calculations.",
        )
    if "bonus/extra-credit grade item detected" in lowered:
        return (
            "P1",
            "extra_credit_setup",
            "ID/Faculty",
            "Configure this item as extra credit in Canvas: set the assignment to 0 points "
            "and check 'Display grade as: Points', or mark the assignment group as extra "
            "credit via Assignment Groups > Edit Group > Extra Credit. "
            "D2L bonus items are not automatically flagged as extra credit after migration.",
        )
    if "course start date not in d2l export" in lowered:
        return (
            "P1",
            "canvas_date_shift_setup",
            "Faculty/Course Coordinator",
            "After importing the course package, open Course Settings > 'Adjust Events and "
            "Due Dates' and enter the new course start date. This triggers Canvas's bulk "
            "date-shift so all assignment due dates, availability windows, and module "
            "unlock dates are moved proportionally. "
            "The D2L IMSCC export does not include a course offering start date.",
        )
    if "quiz availability window detected" in lowered:
        return (
            "P1",
            "quiz_date_window_verification",
            "Faculty/Course Coordinator",
            "Set the quiz availability window (available from / until / due dates) in Canvas. "
            "Go to Quizzes > Edit for each affected quiz and enter the correct dates for the "
            "new course schedule. These dates are not imported from D2L automatically. "
            "See `d2l-export.quiz-audit.md` for the complete per-quiz settings inventory "
            "(time limits, attempts, shuffle settings).",
        )
    if "quiz settings inventory" in lowered:
        return (
            "P1",
            "quiz_settings_inventory",
            "Faculty/Course Coordinator",
            "Re-enter the quiz settings shown in the evidence column inside Canvas New Quizzes. "
            "Open each quiz in Canvas > Quizzes > Build/Edit and set: time limit and enforcement, "
            "allowed attempts, and shuffle settings (questions and/or answers). "
            "These settings are not preserved through the D2L QTI import. "
            "See `d2l-export.quiz-audit.md` for the full per-quiz inventory.",
        )
    if "new quizzes question-type compatibility risk" in lowered:
        # Determine priority from the flagged level in the reason string
        priority = "P1" if "(p1)" in lowered else "P2"
        return (
            priority,
            "new_quizzes_question_type_rebuild",
            "Faculty/Course Coordinator",
            "One or more question types in this quiz are not natively supported by Canvas "
            "New Quizzes and must be manually rebuilt before the course goes live. "
            "See the evidence column for the specific types and counts. "
            "Recommended substitutions: Ordering → Matching or Written Response; "
            "Arithmetic/Calculated → Formula/Calculated Numeric; "
            "Significant Figures → Numeric/Formula; "
            "Likert → survey tool or Written Response. "
            "After rebuilding, verify point values, feedback, and correct-answer keys match D2L.",
        )
    if "layout css may render differently" in lowered:
        return (
            "P2",
            "layout_css_rendering_review",
            "ID",
            "Open each affected page in Canvas and compare the visual layout. "
            "Common issues include fixed-width tables overflowing on narrow screens, "
            "multi-column grids collapsing to single-column, and custom font sizes "
            "being overridden by Canvas styles. Where possible, replace fixed pixel "
            "widths with percentage widths or the Canvas responsive table pattern. "
            "Refer to the evidence column for the specific element and width value.",
        )
    if "embedded youtube video" in lowered or "embedded vimeo video" in lowered:
        platform = "YouTube" if "youtube" in lowered else "Vimeo"
        return (
            "P2",
            "a11y_video_captions",
            "ID/Faculty",
            f"Verify that all {platform} videos include closed captions or a linked "
            f"transcript. On {platform}, check the video's CC settings (auto-generated "
            "captions may need editing for accuracy). Add a transcript link directly "
            "below the embed if captions are unavailable. This is required for ADA "
            "compliance (WCAG 2.1 SC 1.2.2).",
        )
    if "embedded iframe" in lowered:
        return (
            "P1",
            "embedded_iframe_review",
            "ID",
            "Review each iframe for accessibility, security, and responsive behavior.",
        )
    if "d2l quicklink" in lowered and "lti tool embed" in lowered:
        # Extract rCode and title from the reason string for a specific action.
        rcode_match = re.search(r"\[rcode:\s*([^\]]+)\]", lowered)
        rcode_note = (
            f" (D2L rCode: {rcode_match.group(1).strip()})" if rcode_match else ""
        )
        return (
            "P1",
            "lti_quicklink_reconfiguration",
            "Faculty/Course Coordinator",
            f"This D2L LTI quick-link{rcode_note} will NOT resolve after migration. "
            "To fix: (1) confirm with your Canvas admin that the LTI tool is configured "
            "in Canvas (Settings \u2192 Apps); (2) open the Canvas page, delete this broken "
            "embed, and re-insert the tool using the Rich Content Editor \u2192 Apps picker. "
            "The original D2L rCode URL is institution-specific and cannot be reused in Canvas.",
        )
    if "lti tool embed" in lowered:
        # Pattern from detect_lti_embed_issues: "LTI tool embed (ToolName) — verify launch URL after migration"
        tool_match = re.search(r"lti tool embed \(([^)]+)\)", lowered)
        tool_name = tool_match.group(1).title() if tool_match else "LTI tool"
        return (
            "P1",
            "lti_embed_reconfiguration",
            "Faculty/Course Coordinator",
            f"Coordinate with your instructional designer or Canvas admin to verify {tool_name} "
            f"is configured in Canvas (Settings → Apps → {tool_name}), then replace the D2L "
            "embed with a Canvas LTI embed using the Rich Content Editor. "
            "The original D2L src URL will not resolve after migration.",
        )
    if "d2l rubric detected" in lowered:
        return (
            "P1",
            "rubric_import_setup",
            "ID/Faculty",
            "D2L rubrics are not automatically migrated by Canvas import. For each rubric: "
            "(1) recreate it in Canvas via Outcomes > Manage Rubrics > Add Rubric, matching "
            "the criteria names, level names, and point values shown in the evidence column; "
            "(2) attach it to the corresponding assignment or discussion via Edit Assignment → "
            "Add Rubric; (3) check 'Use this rubric for grading' and (4) verify 'Free-form "
            "comment' and 'Hide score total' settings match faculty expectations.",
        )
    if "instructor note placeholder remains" in lowered:
        return (
            "P1",
            "instructor_note_cleanup",
            "Faculty/Course Coordinator",
            "Replace [Instructor Note: ...] placeholders with finalized, course-specific "
            "content before publishing. These are instructor-variable sections that the "
            "migration tool cannot fill in automatically.",
        )
    if "template placeholder text remains" in lowered:
        return (
            "P1",
            "template_placeholder_cleanup",
            "Faculty/Course Coordinator",
            "Replace unresolved template placeholders (such as 'Fill in text here') with "
            "final, course-specific content before publishing.",
        )
    if "template asset reference not mapped to canvas template package" in lowered:
        return (
            "P1",
            "template_asset_mapping_review",
            "ID",
            "Map unresolved Brightspace template assets to approved Canvas template assets and re-run migration.",
        )
    if "legacy d2l links were neutralized" in lowered:
        return (
            "P1",
            "relink_neutralized_d2l_links",
            "ID",
            "Replace neutralized D2L links with valid Canvas links.",
        )
    if "question bank migration requires manual verification" in lowered:
        return (
            "P1",
            "question_bank_logic_review",
            "Faculty/ID",
            "Rebuild question-pool logic in Canvas, verify draw counts/randomization, and share any required item banks with the course so coordinators can edit them.",
        )
    if "youtube embeds may violate ad-free requirement" in lowered:
        return (
            "P2",
            "youtube_hosting_review",
            "Faculty/ID",
            "Confirm hosting approach (for example Canvas Studio or approved platform).",
        )
    if "announcement migration behavior is non-standard" in lowered:
        return (
            "P2",
            "announcement_settings_review",
            "ID",
            "Verify announcement posting state and notification behavior in Canvas.",
        )
    if "panopto embed requires permissions" in lowered:
        return (
            "P1",
            "panopto_permissions_review",
            "Faculty/Course Coordinator",
            "Verify your Panopto folder permissions allow enrolled students to view the video. "
            "Re-embed the video using the Canvas Panopto LTI picker (via Rich Content Editor) "
            "rather than the original D2L src URL, which will not resolve after migration.",
        )
    if "h5p content requires manual conversion" in lowered:
        return (
            "P2",
            "h5p_conversion_decision",
            "ID",
            "Replace or rebuild H5P content using approved Canvas-compatible workflow.",
        )
    if "scorm packages require upload" in lowered:
        return (
            "P1",
            "scorm_upload_workflow",
            "Faculty/Course Coordinator",
            "Re-upload the SCORM package to Canvas via Files > Upload, then embed it using "
            "the Canvas SCORM player. Verify completion tracking and gradebook sync after "
            "upload. Coordinate with your instructional designer if the SCORM package "
            "needs to be rebuilt or re-authored.",
        )

    if "module checklist is missing an instructor contact reminder" in lowered:
        return (
            "P2",
            "module_checklist_closer_missing",
            "Faculty/ID",
            "Add the required closing reminder as the final item in the Module Checklist. "
            "Open the page in Canvas, scroll to the Module Checklist section, and add: "
            "\u2018Contact your instructor with any questions or post in the Course Q&A.\u2019 "
            "as the last list entry before publishing.",
        )
    if issue_type == "accessibility":
        if (
            "image missing alt attribute" in lowered
            or "image alt attribute is empty" in lowered
        ):
            return (
                "P1",
                "a11y_alt_text",
                "Faculty/ID",
                "Add meaningful alt text or mark decorative images as presentational. "
                "Check the evidence column — a suggested alt text is included when one "
                "could be inferred from the surrounding heading, caption, or filename. "
                "Verify the suggestion with the faculty member before publishing. "
                "In Canvas: edit the page > click the image > Image Options > "
                "enter alt text or check 'Decorative Image'.",
            )
        if "heading level jump detected" in lowered:
            return (
                "P2",
                "a11y_heading_order",
                "Faculty/ID",
                "Fix heading hierarchy to avoid level jumps.",
            )
        if "table missing caption" in lowered:
            return (
                "P2",
                "a11y_table_caption",
                "Faculty/ID",
                "Add table captions and header associations as needed.",
            )
        if "non-descriptive link text" in lowered:
            return (
                "P2",
                "a11y_link_text",
                "Faculty/ID",
                "Replace vague link text with descriptive labels.",
            )

    return (
        "P2",
        "manual_review_item",
        "ID",
        "Review and resolve this migration finding.",
    )


def _map_reference_best_practice_gap(row_id: str, label: str) -> tuple[str, str, str]:
    normalized = row_id.strip().lower()
    if normalized == "item_bank_sharing":
        return (
            "P1",
            "ID/Faculty",
            "After rebuilding or copying quiz banks, open Item Banks, share each required bank with the course, and verify the course coordinator can edit the questions.",
        )
    if normalized == "question_library_rebuild":
        return (
            "P1",
            "ID/Faculty",
            "For quizzes that used D2L question libraries, rebuild question pools from Canvas item banks and verify scoring, settings, and random draw behavior.",
        )
    if normalized == "rubric_use_for_grading":
        return (
            "P1",
            "Faculty/ID",
            "Open each migrated rubric, fix formatting/points/outcomes as needed, reconnect it to the assessment, and enable Use this rubric for grading where required.",
        )
    if normalized == "studio_video_workflow":
        return (
            "P1",
            "ID",
            "If a course used D2L media-library videos, move them into course-owned Canvas Studio or another approved course-owned location, replace embeds, and verify student playback.",
        )
    if normalized == "mobile_view_review":
        return (
            "P2",
            "Faculty/ID",
            "Use browser device emulation or Canvas mobile tooling to verify the final course pages and assessments in a mobile-sized view before release.",
        )
    return (
        "P2",
        "ID Lead",
        "Add explicit rule, trigger, or preflight check to cover this best-practice topic.",
    )


def _generate_standard_postmigration_tasks() -> list[ChecklistItem]:
    """Return standard post-migration QA tasks based on known D2L→Canvas migration pain-points.

    These items represent manual checks that are required (or likely needed) for every
    course migration.  They cannot be fully auto-detected from HTML or Canvas import logs
    because they depend on D2L gradebook/quiz XML that is not yet parsed by the pipeline.
    They are included as P2 reminders so nothing falls through the cracks.
    """
    return [
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="gradebook_structure",
            owner="ID",
            description="Verify Canvas Gradebook assignment groups, weights, and drop rules",
            action=(
                "Open Gradebook → Assignment Groups.  Recreate D2L category weights, confirm "
                "assignment group totals match D2L, and configure drop-lowest/drop-highest rules "
                "for any activity with student-drop policies."
            ),
        ),
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="bonus_extra_credit",
            owner="ID/Faculty",
            description="Verify bonus / extra-credit assignments are configured correctly",
            action=(
                "In Canvas, extra-credit assignments must be worth 0 points and have an attached "
                "rubric (or a manual points entry).  Find any D2L 'bonus' items and set them up "
                "in Canvas accordingly.  Confirm they do not count against the total points denominator."
            ),
        ),
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="syllabus_quiz_gate",
            owner="ID/Faculty",
            description="Verify Syllabus Quiz / prerequisite-gating is re-implemented in Canvas",
            action=(
                "If the D2L course gated module access with a Syllabus Quiz (quiz set to "
                "'Not in Gradebook'), recreate the prerequisite using Canvas Module Requirements "
                "(complete the quiz before proceeding) and verify the quiz is excluded from "
                "grade calculations."
            ),
        ),
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="faculty_only_content",
            owner="ID",
            description="Verify instructor-only / faculty-only pages are unpublished in Canvas",
            action=(
                "Identify D2L pages restricted to Staff/Instructor roles (release conditions).  "
                "In Canvas, set those pages to Unpublished and add '(Instructor Only)' to the "
                "page title per naming convention so they are excluded from student view."
            ),
        ),
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="rubric_configuration",
            owner="ID/Faculty",
            description="Verify rubric point ranges, criteria, and grading connections in Canvas",
            action=(
                "Open each migrated rubric in Canvas.  Check that criterion point ranges are "
                "correct (D2L and Canvas range differently), attach the rubric to its assignment, "
                "and enable 'Use this rubric for grading' on graded assessments."
            ),
        ),
        ChecklistItem(
            priority="P2",
            source="standard_migration",
            category="item_bank_sharing",
            owner="ID",
            description="Share item banks with course coordinator after quiz migration",
            action=(
                "After rebuilding quiz question banks, open Canvas Item Banks, share each required "
                "bank at the course level, and confirm the course coordinator can view and edit "
                "questions.  This is required before the course goes live for multi-section courses."
            ),
        ),
    ]


def _load_canvas_items(path: Path) -> list[ChecklistItem]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [_map_canvas_issue(item) for item in payload if isinstance(item, dict)]


def _load_manual_review_items(path: Path | None) -> list[ChecklistItem]:
    if path is None or not path.exists():
        return []

    grouped: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "files": []}
    )
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issue_type = str(row.get("type", "")).strip() or "manual_review"
            reason = str(row.get("reason", "")).strip()
            file_path = str(row.get("file", "")).strip()
            if not reason:
                continue
            key = (issue_type, reason)
            grouped[key]["count"] += 1
            if file_path and file_path not in grouped[key]["files"]:
                grouped[key]["files"].append(file_path)

    items: list[ChecklistItem] = []
    for (issue_type, reason), meta in grouped.items():
        priority, category, owner, action = _map_manual_review_group(issue_type, reason)
        sample_files = ", ".join(meta["files"][:3])
        reference = (
            f"{path.name} | sample files: {sample_files}" if sample_files else path.name
        )
        items.append(
            ChecklistItem(
                priority=priority,
                source="manual_review",
                category=category,
                owner=owner,
                description=reason,
                action=action,
                count=int(meta["count"]),
                reference=reference,
            )
        )
    return items


def _load_reference_items(path: Path | None) -> list[ChecklistItem]:
    if path is None or not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    items: list[ChecklistItem] = []

    instruction = payload.get("instruction_comparison", {})
    for gap in instruction.get("critical_gaps", []):
        phrase = str(gap.get("phrase", "")).strip()
        rationale = str(gap.get("rationale", "")).strip()
        if not phrase:
            continue
        items.append(
            ChecklistItem(
                priority="P2",
                source="reference_audit",
                category=f"reference_instruction_gap:{gap.get('id', 'gap')}",
                owner="ID Lead",
                description=phrase,
                action="Decide whether to encode this guidance into migration rules, checklist, or process docs.",
                reference=rationale,
            )
        )

    coverage = payload.get("best_practices_coverage", {})
    for row in coverage.get("coverage_rows", []):
        if not bool(row.get("action_needed")):
            continue
        label = str(row.get("label", "")).strip()
        if not label:
            continue
        priority, owner, action = _map_reference_best_practice_gap(
            str(row.get("id", "")), label
        )
        items.append(
            ChecklistItem(
                priority=priority,
                source="reference_audit",
                category=f"reference_best_practice_gap:{row.get('id', 'coverage')}",
                owner=owner,
                description=label,
                action=action,
                reference=path.name,
            )
        )

    template = payload.get("template_analysis", {})
    if not bool(template.get("module_checklist_required_closer_present", True)):
        items.append(
            ChecklistItem(
                priority="P1",
                source="reference_audit",
                category="reference_template_mc_closer",
                owner="ID Lead",
                description="Template missing required Module Checklist closing reminder.",
                action="Update template and migration validation to enforce required checklist closer.",
                reference=path.name,
            )
        )

    placeholder_patterns = template.get("placeholder_patterns_detected", [])
    if isinstance(placeholder_patterns, list) and placeholder_patterns:
        items.append(
            ChecklistItem(
                priority="P2",
                source="reference_audit",
                category="reference_template_placeholders",
                owner="ID Lead",
                description="Template documents contain placeholder patterns.",
                action="Confirm placeholders are intentional template tokens and enforce final cleanup rules.",
                reference=", ".join(str(x) for x in placeholder_patterns[:5]),
            )
        )

    return items


def build_fix_checklist(
    *,
    canvas_issues_json: Path,
    output_dir: Path,
    manual_review_csv: Path | None = None,
    reference_audit_json: Path | None = None,
    basename: str = "migration-fix-checklist",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{basename}.csv"
    md_path = output_dir / f"{basename}.md"

    items = []
    items.extend(_load_canvas_items(canvas_issues_json))
    items.extend(_load_manual_review_items(manual_review_csv))
    items.extend(_load_reference_items(reference_audit_json))
    items.extend(_generate_standard_postmigration_tasks())
    items.sort(
        key=lambda item: (
            _priority_rank(item.priority),
            item.source,
            item.category,
            item.description,
        )
    )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "priority",
                "source",
                "category",
                "owner",
                "description",
                "action",
                "count",
                "reference",
                "status",
                "notes",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "priority": item.priority,
                    "source": item.source,
                    "category": item.category,
                    "owner": item.owner,
                    "description": item.description,
                    "action": item.action,
                    "count": item.count,
                    "reference": item.reference,
                    "status": "todo",
                    "notes": "",
                }
            )

    by_priority = Counter(item.priority for item in items)
    by_source = Counter(item.source for item in items)
    by_category = Counter(item.category for item in items)

    lines = [
        "# Migration Fix Checklist",
        "",
        f"- Canvas issues input: `{canvas_issues_json}`",
        (
            f"- Manual review input: `{manual_review_csv}`"
            if manual_review_csv
            else "- Manual review input: none"
        ),
        (
            f"- Reference audit input: `{reference_audit_json}`"
            if reference_audit_json
            else "- Reference audit input: none"
        ),
        f"- Total checklist items: {len(items)}",
        f"- CSV output: `{csv_path}`",
        "",
        "## Summary",
        "",
    ]
    for priority, count in sorted(
        by_priority.items(), key=lambda x: _priority_rank(x[0])
    ):
        lines.append(f"- {priority}: {count}")
    lines.append("")
    for source, count in by_source.items():
        lines.append(f"- {source}: {count}")

    lines.extend(["", "## Category Counts", ""])
    for category, count in by_category.most_common():
        lines.append(f"- {category}: {count}")

    lines.extend(["", "## Action Items", ""])
    for item in items:
        lines.append(
            f"- [ ] ({item.priority}) [{item.source}] {item.category} | {item.description} | owner: {item.owner} | count: {item.count}"
        )
        lines.append(f"  - Action: {item.action}")
        if item.reference:
            lines.append(f"  - Reference: {item.reference}")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-build-fix-checklist",
        description="Build migration fix checklist from Canvas import issues and optional audit files.",
    )
    parser.add_argument(
        "canvas_issues_json", type=Path, help="Path to canvas-migration-issues.json"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output"), help="Output directory"
    )
    parser.add_argument(
        "--manual-review-csv",
        type=Path,
        default=None,
        help="Optional manual-review CSV",
    )
    parser.add_argument(
        "--reference-audit-json",
        type=Path,
        default=None,
        help="Optional reference-audit JSON",
    )
    parser.add_argument(
        "--basename",
        type=str,
        default="migration-fix-checklist",
        help="Output base name",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.canvas_issues_json.exists():
        parser.error(f"Canvas issues JSON not found: {args.canvas_issues_json}")
    if args.manual_review_csv is not None and not args.manual_review_csv.exists():
        parser.error(f"Manual review CSV not found: {args.manual_review_csv}")
    if args.reference_audit_json is not None and not args.reference_audit_json.exists():
        parser.error(f"Reference audit JSON not found: {args.reference_audit_json}")

    csv_path, md_path = build_fix_checklist(
        canvas_issues_json=args.canvas_issues_json,
        output_dir=args.output_dir,
        manual_review_csv=args.manual_review_csv,
        reference_audit_json=args.reference_audit_json,
        basename=args.basename,
    )
    print(f"Checklist CSV: {csv_path}")
    print(f"Checklist Markdown: {md_path}")


if __name__ == "__main__":
    main()

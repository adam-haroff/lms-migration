from __future__ import annotations

from lms_migration.canvas_content_reference_sync import (
    Candidate,
    ModuleContext,
    _normalize_inline_reference_markup,
    _normalize_template_icon_headings,
    _rewrite_anchor_labels,
    _rewrite_textual_references,
)


def test_rewrite_textual_references_maps_dropbox_to_current_assignment_name() -> None:
    module = ModuleContext(
        module_number=7,
        name="Module 7: The Gendered Classroom",
        assignments=[
            Candidate(
                kind="assignment",
                full_title="Module 7: Assignment: Gendered Classroom",
                short_title="Assignment: Gendered Classroom",
                core_label="Gendered Classroom",
                module_number=7,
            )
        ],
    )
    html = "<p>Complete: upload your work to the Dropbox | Gendered Classroom.</p>"
    updated, rewrites = _rewrite_textual_references(
        html,
        module_context=module,
        course_candidates={"discussion": [], "assignment": [], "quiz": [], "page": []},
    )
    assert rewrites == 1
    assert "Assignment: Gendered Classroom" in updated
    assert "Dropbox |" not in updated


def test_rewrite_textual_references_updates_practice_games_page_name() -> None:
    module = ModuleContext(
        module_number=2,
        name="Module 2: Biological Basis of Gender",
        pages=[
            Candidate(
                kind="page",
                full_title="Module 2: Practice Activity for Quiz",
                short_title="Practice Activity for Quiz",
                core_label="Practice Activity for Quiz",
                module_number=2,
            )
        ],
    )
    html = "<p>Review using Study Mates Activities on the Practice Games page.</p>"
    updated, rewrites = _rewrite_textual_references(
        html,
        module_context=module,
        course_candidates={"discussion": [], "assignment": [], "quiz": [], "page": []},
    )
    assert rewrites == 0
    assert "Practice Activity for Quiz page" in updated


def test_rewrite_textual_references_single_module_candidate_can_replace_full_phrase() -> None:
    module = ModuleContext(
        module_number=14,
        name="Module 14: The Gender of Violence",
        discussions=[
            Candidate(
                kind="discussion",
                full_title="Module 14: Discussion: Making Improvements",
                short_title="Discussion: Making Improvements",
                core_label="Making Improvements",
                module_number=14,
            )
        ],
    )
    html = "<p>Post your response to the Discussion | Gender and Violence:</p>"
    updated, rewrites = _rewrite_textual_references(
        html,
        module_context=module,
        course_candidates={"discussion": [], "assignment": [], "quiz": [], "page": []},
    )
    assert rewrites == 1
    assert "Discussion: Making Improvements" in updated
    assert "Gender and Violence" not in updated


def test_rewrite_textual_references_removes_duplicate_assignment_suffix() -> None:
    module = ModuleContext(
        module_number=14,
        name="Module 14: The Gender of Violence",
        assignments=[
            Candidate(
                kind="assignment",
                full_title="Module 14: Assignment: Gender and Violence",
                short_title="Assignment: Gender and Violence",
                core_label="Gender and Violence",
                module_number=14,
            )
        ],
    )
    html = "<p>The Dropbox | Gender and Violence Assignment is based on this video.</p>"
    updated, rewrites = _rewrite_textual_references(
        html,
        module_context=module,
        course_candidates={"discussion": [], "assignment": [], "quiz": [], "page": []},
    )
    assert rewrites == 1
    assert "Assignment: Gender and Violence is based" in updated
    assert "Assignment: Gender and Violence Assignment" not in updated


def test_normalize_inline_reference_markup_unwraps_split_reference_tags() -> None:
    html = (
        "<li><strong>Participate</strong>: in&nbsp;<strong>Discussion</strong>"
        "<strong>| KWL Mental Illness</strong></li>"
    )
    updated = _normalize_inline_reference_markup(html)
    assert "<strong>Discussion</strong><strong>|" not in updated
    assert "Discussion | KWL Mental Illness" in updated


def test_rewrite_textual_references_replaces_generic_quiz_phrase_with_single_module_quiz() -> None:
    module = ModuleContext(
        module_number=7,
        name="Module 7: The Gendered Classroom",
        quizzes=[
            Candidate(
                kind="quiz",
                full_title="Module 7: Quiz: Chapter 7",
                short_title="Quiz: Chapter 7",
                core_label="Chapter 7",
                module_number=7,
            )
        ],
    )
    html = "<p><strong>Complete</strong>: <strong>Quiz |</strong>This quiz is on the chapter reading and the videos in the lesson</p>"
    updated, rewrites = _rewrite_textual_references(
        html,
        module_context=module,
        course_candidates={"discussion": [], "assignment": [], "quiz": [], "page": []},
    )
    assert rewrites == 1
    assert "Quiz: Chapter 7" in updated
    assert "This quiz is on the chapter reading" not in updated
    assert "<strong>Quiz |</strong>" not in updated


def test_rewrite_anchor_labels_uses_current_discussion_title() -> None:
    html = (
        '<p><a href="https://sinclair.instructure.com/courses/21825/discussion_topics/56095">'
        "Discussion | Family</a></p>"
    )
    updated, rewrites = _rewrite_anchor_labels(
        html,
        discussion_title_by_id={"56095": "Module 6: Discussion: Family"},
        assignment_title_by_id={},
        page_title_by_slug={},
    )
    assert rewrites == 1
    assert "Module 6: Discussion: Family" in updated
    assert "Discussion | Family" not in updated


def test_normalize_template_icon_headings_promotes_h3_to_h2_and_trims_nbsp() -> None:
    html = (
        '<h3 style="color: #ac1a2f;"><img src="https://sinclair.instructure.com/courses/21825/files/1191087/preview" '
        'style="width: 45px; height: auto; vertical-align: middle; margin-right: 8px;" alt="" role="presentation">'
        '&nbsp;<span style="color: #ac1a2f;"><strong>Read</strong></span></h3>'
    )
    updated, level_fixes, spacing_fixes = _normalize_template_icon_headings(
        html,
        template_icon_ids={"1191087"},
    )
    assert level_fixes == 1
    assert spacing_fixes >= 1
    assert updated.startswith("<h2")
    assert "&nbsp;" not in updated

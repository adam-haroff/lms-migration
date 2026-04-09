from __future__ import annotations

from lms_migration.canvas_assessment_templates import (
    wrap_assignment_description,
    wrap_discussion_message,
    wrap_quiz_description,
)
from lms_migration.canvas_post_import import _build_file_index, _build_folder_path_index


def _file_index() -> dict[str, list]:
    folder_paths = _build_folder_path_index(
        [
            {"id": 10, "full_name": "course files/template-images/icons"},
        ]
    )
    file_index, _ = _build_file_index(
        [
            {"id": 101, "display_name": "discussion.png", "folder_id": 10},
            {"id": 102, "display_name": "pencil.png", "folder_id": 10},
            {"id": 103, "display_name": "flag.png", "folder_id": 10},
            {"id": 104, "display_name": "gear.png", "folder_id": 10},
        ],
        folder_paths=folder_paths,
    )
    return file_index


def test_wrap_assignment_description_adds_template_sections() -> None:
    wrapped = wrap_assignment_description(
        body_html="<p>Write a short paper.</p><ul><li>Use APA format.</li></ul>",
        course_id="17038",
        file_index=_file_index(),
    )

    assert "Assignment Overview" in wrapped
    assert "Instructions" in wrapped
    assert "Technical Support" in wrapped
    assert '/courses/17038/files/102/preview' in wrapped
    assert '/courses/17038/files/103/preview' in wrapped
    assert '/courses/17038/files/104/preview' in wrapped
    assert "<ul><li>Use APA format.</li></ul>" in wrapped


def test_wrap_discussion_message_adds_overview_prompt_and_support() -> None:
    wrapped = wrap_discussion_message(
        body_html="<p>Read the article first.</p><ol><li>Respond to the prompt.</li></ol>",
        title="Communication Theory - Discussion",
        course_id="17038",
        file_index=_file_index(),
    )

    assert "Discussion Overview" in wrapped
    assert "<h3>Guidelines</h3>" in wrapped
    assert "<h3>Prompt</h3>" in wrapped
    assert "Technical Support" in wrapped
    assert '/courses/17038/files/101/preview' in wrapped
    assert '/courses/17038/files/104/preview' in wrapped


def test_wrap_discussion_message_moves_due_and_reply_text_into_guidelines() -> None:
    wrapped = wrap_discussion_message(
        body_html=(
            "<p>Post your answer to the question below.</p>"
            "<ul><li>Explain the theory.</li></ul>"
            "<p>This assignment is worth 10 points.</p>"
            "<p>Remember your Post First is due by 5:00 PM on Tuesday.</p>"
        ),
        title="Symbolic Interaction Theory - Discussion",
        course_id="17038",
        file_index=_file_index(),
    )

    assert "<h3>Guidelines</h3>" in wrapped
    assert "<h3>Prompt</h3>" in wrapped
    guidelines_section = wrapped.split("<h3>Guidelines</h3>", 1)[1].split("<h3>Prompt</h3>", 1)[0]
    prompt_section = wrapped.split("<h3>Prompt</h3>", 1)[1]
    assert "This assignment is worth 10 points." in guidelines_section
    assert "Post First is due" in guidelines_section
    assert "<li>Explain the theory.</li>" in prompt_section


def test_wrap_discussion_message_skips_support_discussions() -> None:
    body = (
        "<p>This is an ungraded discussion area that you can use to post general class questions.</p>"
        "<p>If you know the answer to a classmate's question, please feel free to post the answer.</p>"
    )
    assert (
        wrap_discussion_message(
            body_html=body,
            title="Course Q&A",
            course_id="17038",
            file_index=_file_index(),
        )
        == body
    )


def test_wrap_quiz_description_uses_quiz_heading() -> None:
    wrapped = wrap_quiz_description(
        body_html="<p>You will have 20 minutes.</p><p>Click Begin when ready.</p>",
        course_id="17038",
        file_index=_file_index(),
        title="Module 1: Quiz: Chapter 1",
    )

    assert "Quiz Overview" in wrapped
    assert "Technical Support" in wrapped
    assert "You will have 20 minutes." in wrapped
    assert '/courses/17038/files/104/preview' in wrapped


def test_wrap_quiz_description_uses_exam_heading_for_exam_titles() -> None:
    wrapped = wrap_quiz_description(
        body_html="<p>This is the final assessment.</p>",
        course_id="17038",
        file_index=_file_index(),
        title="Module 13: Final Exam",
    )

    assert "Exam Overview" in wrapped
    assert "Quiz Overview" not in wrapped


def test_wrappers_skip_already_templated_content() -> None:
    body = "<h2><strong>Assignment Overview</strong></h2><p>Already wrapped.</p>"
    assert wrap_assignment_description(
        body_html=body,
        course_id="17038",
        file_index=_file_index(),
    ) == body

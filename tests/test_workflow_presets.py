from __future__ import annotations

from lms_migration.workflow_presets import (
    default_workflow_preset_id,
    get_workflow_preset,
    list_workflow_presets,
)


def test_default_workflow_preset_is_clean_course() -> None:
    assert default_workflow_preset_id() == "clean-course-template-first"


def test_clean_course_preset_uses_template_first_flow() -> None:
    preset = get_workflow_preset("clean-course-template-first")
    assert preset.include_starter_template_shell is False
    assert preset.course_already_has_starter_template is True
    assert preset.import_starter_template_first is True
    assert preset.image_layout_mode == "preserve-wrap"
    assert preset.math_handling == "preserve-semantic"
    assert preset.intro_checklist_handling == "rebuild-when-confident"


def test_existing_template_preset_skips_template_import() -> None:
    preset = get_workflow_preset("template-already-present")
    assert preset.include_starter_template_shell is False
    assert preset.course_already_has_starter_template is True
    assert preset.import_starter_template_first is False


def test_full_template_preset_disables_seeded_template_mode() -> None:
    preset = get_workflow_preset("full-template-in-generated-package")
    assert preset.include_starter_template_shell is True
    assert preset.course_already_has_starter_template is False
    assert preset.import_starter_template_first is False


def test_preset_labels_are_unique() -> None:
    labels = [preset.label for preset in list_workflow_presets()]
    assert len(labels) == len(set(labels))

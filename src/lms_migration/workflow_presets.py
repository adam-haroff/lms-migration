from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowPreset:
    preset_id: str
    label: str
    summary: str
    conversion_policy: str = "strict"
    apply_template_overlay: bool = True
    use_template_asset_alias_map: bool = True
    apply_template_page_merge: bool = True
    include_starter_template_shell: bool = False
    course_already_has_starter_template: bool = True
    import_starter_template_first: bool = True
    accordion_handling: str = "smart"
    accordion_title_align: str = "left"
    image_layout_mode: str = "preserve-wrap"
    math_handling: str = "preserve-semantic"
    intro_checklist_handling: str = "rebuild-when-confident"
    learning_activities_handling: str = "preserve"


_WORKFLOW_PRESETS: tuple[WorkflowPreset, ...] = (
    WorkflowPreset(
        preset_id="clean-course-template-first",
        label="Clean Canvas course (recommended)",
        summary=(
            "Use this when the Canvas course is blank. The Upload step should import "
            "the starter template first, then import the generated Canvas-ready package."
        ),
    ),
    WorkflowPreset(
        preset_id="template-already-present",
        label="Canvas course already has template",
        summary=(
            "Use this when the Canvas course already contains the Sinclair starter "
            "template. Do not import the template again during Upload."
        ),
        import_starter_template_first=False,
    ),
    WorkflowPreset(
        preset_id="full-template-in-generated-package",
        label="Put full starter template in generated package",
        summary=(
            "Use this only when you intentionally want the generated package itself to "
            "carry the full starter-template shell instead of reusing a live Canvas "
            "template course."
        ),
        include_starter_template_shell=True,
        course_already_has_starter_template=False,
        import_starter_template_first=False,
    ),
)


def list_workflow_presets() -> tuple[WorkflowPreset, ...]:
    return _WORKFLOW_PRESETS


def default_workflow_preset_id() -> str:
    return _WORKFLOW_PRESETS[0].preset_id


def get_workflow_preset(preset_id: str) -> WorkflowPreset:
    normalized = preset_id.strip().lower()
    for preset in _WORKFLOW_PRESETS:
        if preset.preset_id == normalized:
            return preset
    raise KeyError(f"Unknown workflow preset: {preset_id}")

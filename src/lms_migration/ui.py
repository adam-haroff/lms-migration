from __future__ import annotations

import json
import re
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Tkinter is required for the local UI. Install a Python build that includes Tk."
    ) from exc

from .approval_report import build_approval_report
from .best_practices import run_audit
from .canvas_api import (
    CanvasAPIError,
    fetch_content_migrations,
    fetch_migration_issues,
    normalize_base_url,
)
from .canvas_preview import CanvasPreviewError, run_preview
from .canvas_post_import import auto_relink_missing_links
from .canvas_live_audit import run_live_link_audit
from .canvas_snapshot import snapshot_canvas_course
from .fix_checklist import build_fix_checklist
from .math_audit import build_math_audit
from .pattern_report import build_pattern_report
from .pipeline import MigrationOutput, run_migration
from .policy_profiles import list_policy_profiles
from .reference_audit import run_reference_audit
from .reference_docs import default_reference_doc_paths
from .review_pack import build_review_pack
from .review_writeback import apply_review_draft
from .safe_summary import build_safe_summary_from_path
from .template_standards import resolve_default_template_package
from .visual_audit import build_visual_audit


def _default_safe_summary_path(report_path: Path) -> Path:
    if report_path.name.endswith(".migration-report.json"):
        return report_path.with_name(
            report_path.name.replace(".migration-report.json", ".safe-summary.txt")
        )
    return report_path.with_suffix(".safe-summary.txt")


def _default_visual_audit_json_path(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.visual-audit.json")


def _default_math_audit_json_path(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.math-audit.json")


def _default_page_review_json_path(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.page-review.json")


def _default_review_draft_json_path(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.review-draft.json")


def _default_reviewed_zip_path(converted_zip: Path) -> Path:
    name = converted_zip.name
    if name.endswith(".canvas-ready.zip"):
        return converted_zip.with_name(
            name.replace(".canvas-ready.zip", ".canvas-reviewed.zip")
        )
    if name.endswith(".zip"):
        return converted_zip.with_name(name[:-4] + ".reviewed.zip")
    return converted_zip.with_name(name + ".reviewed.zip")


def _default_pattern_report_json_path(converted_zip: Path) -> Path:
    stem = converted_zip.name
    if stem.endswith(".canvas-ready.zip"):
        stem = stem[: -len(".canvas-ready.zip")]
    elif stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return converted_zip.with_name(f"{stem}.pattern-report.json")


class LMSMigrationUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Canvas Migration Workbench")
        self.root.geometry("1320x900")
        self.root.minsize(1080, 760)

        self.is_busy = False
        self.latest_safe_summary = ""
        self.ui_state_path = (
            self._resolve_workspace_root() / ".lms-migrate-ui-state.json"
        )
        self.course_code_history = self._load_history("sinclair_course_code_history")
        self.input_zip_history = self._load_history("input_zip_history")

        workspace_root = self._resolve_workspace_root()
        default_output = workspace_root / "output"
        reference_doc_defaults = default_reference_doc_paths(workspace_root)
        default_template_package = resolve_default_template_package(workspace_root)
        self.policy_profiles_path = workspace_root / "rules" / "policy_profiles.json"
        self.available_policy_profiles = self._load_available_policy_profiles()
        self.input_zip_var = tk.StringVar(value="")
        self.rules_var = tk.StringVar(value=str(self._resolve_default_rules()))
        self.output_dir_var = tk.StringVar(value=str(default_output))
        self.enable_best_practice_enforcer_var = tk.BooleanVar(value=True)
        self.enable_template_overlay_var = tk.BooleanVar(value=True)
        self.template_package_var = tk.StringVar(
            value=(
                str(default_template_package)
                if default_template_package is not None
                and default_template_package.exists()
                else ""
            )
        )
        self.template_overlay_use_alias_map_var = tk.BooleanVar(value=True)
        self.math_handling_var = tk.StringVar(value="preserve-semantic")
        self.accordion_handling_var = tk.StringVar(value="smart")
        self.accordion_alignment_var = tk.StringVar(value="left")
        self.accordion_flatten_hints_var = tk.StringVar(value="")
        self.accordion_details_hints_var = tk.StringVar(value="")
        self.template_module_structure_var = tk.BooleanVar(value=True)
        self.template_visual_standards_var = tk.BooleanVar(value=True)
        self.template_color_standards_var = tk.BooleanVar(value=True)
        self.template_divider_standards_var = tk.BooleanVar(value=True)
        self.image_layout_mode_var = tk.StringVar(value="safe-block")
        default_policy = (
            "strict"
            if "strict" in self.available_policy_profiles
            else self.available_policy_profiles[0]
        )
        self.policy_profile_var = tk.StringVar(value=default_policy)
        self.report_json_var = tk.StringVar(value="")
        self.safe_summary_path_var = tk.StringVar(value="")

        self.best_practices_file_var = tk.StringVar(value="")
        self.best_practices_sheet_var = tk.StringVar(value="")

        self.ref_instructions_docx_var = tk.StringVar(
            value=str(reference_doc_defaults.get("instructions_docx") or "")
        )
        self.ref_best_practices_docx_var = tk.StringVar(
            value=str(reference_doc_defaults.get("best_practices_docx") or "")
        )
        self.ref_setup_checklist_docx_var = tk.StringVar(
            value=str(reference_doc_defaults.get("setup_checklist_docx") or "")
        )
        self.ref_page_templates_docx_var = tk.StringVar(
            value=str(reference_doc_defaults.get("page_templates_docx") or "")
        )
        self.ref_syllabus_template_docx_var = tk.StringVar(
            value=str(reference_doc_defaults.get("syllabus_template_docx") or "")
        )
        self.visual_original_zip_var = tk.StringVar(value="")
        self.visual_converted_zip_var = tk.StringVar(value="")
        self.visual_audit_output_var = tk.StringVar(value="")
        self.math_audit_output_var = tk.StringVar(value="")
        self.review_draft_json_var = tk.StringVar(value="")
        self.reviewed_zip_output_var = tk.StringVar(value="")
        self.pattern_report_output_var = tk.StringVar(value="")

        self.canvas_base_url_var = tk.StringVar(
            value="https://sinclair.instructure.com"
        )
        self.canvas_course_id_var = tk.StringVar(value="")
        self.sinclair_course_code_var = tk.StringVar(value="")
        self.canvas_token_var = tk.StringVar(value="")
        self.canvas_migration_id_var = tk.StringVar(value="")
        self.canvas_issues_output_var = tk.StringVar(
            value=str(default_output / "canvas-migration-issues.json")
        )
        self.template_alias_map_var = tk.StringVar(
            value=str(
                self._resolve_workspace_root() / "rules" / "template_asset_aliases.json"
            )
        )
        self.use_template_alias_map_var = tk.BooleanVar(value=True)
        self.live_audit_apply_safe_fixes_var = tk.BooleanVar(value=True)
        self.ab_variant_var = tk.StringVar(value="A")
        self.ab_include_auto_relink_var = tk.BooleanVar(value=True)
        self.show_canvas_advanced_var = tk.BooleanVar(value=False)
        self.show_optional_tools_var = tk.BooleanVar(value=False)
        self.canvas_upload_zip_var = tk.StringVar(value="")
        self.canvas_upload_template_zip_var = tk.StringVar(value="")
        self.canvas_upload_include_template_var = tk.BooleanVar(value=True)
        self.canvas_preview_output_var = tk.StringVar(value="")
        self._active_scroll_canvas: tk.Canvas | None = None
        self._tab_canvases: list[tk.Canvas] = []
        self._upload_page_urls: list[str] = []
        self.page_review_html_var = tk.StringVar(value="")
        self.auto_open_page_review_var = tk.BooleanVar(value=True)
        self.status_text_var = tk.StringVar(value="Status: Idle")
        self.readiness_local_var = tk.StringVar(
            value="Local package: waiting for conversion."
        )
        self.readiness_review_var = tk.StringVar(value="Upload review: not run.")
        self.readiness_canvas_var = tk.StringVar(value="Canvas post-import: not run.")
        self.readiness_next_step_var = tk.StringVar(
            value="Next step: choose a D2L zip and click Prepare Canvas Package."
        )

        self.sinclair_course_code_var.trace_add(
            "write", lambda *_: self._maybe_apply_course_folder_defaults()
        )
        self._last_canvas_course_id = self.canvas_course_id_var.get().strip()
        self.canvas_course_id_var.trace_add(
            "write", lambda *_: self._on_canvas_course_id_changed()
        )
        self.ab_variant_var.trace_add(
            "write", lambda *_: self._sync_issues_output_for_ab_variant()
        )
        self.template_visual_standards_var.trace_add(
            "write", lambda *_: self._sync_template_visual_subcontrols_state()
        )

        self._build_layout()

    def _load_ui_state_payload(self) -> dict:
        if not self.ui_state_path.exists():
            return {}
        try:
            payload = json.loads(self.ui_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _load_history(self, key: str) -> tuple[str, ...]:
        payload = self._load_ui_state_payload()
        raw_history = payload.get(key, [])
        if not isinstance(raw_history, list):
            return ()

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in raw_history:
            text = str(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(text)
        return tuple(cleaned[:25])

    def _save_ui_state(self) -> None:
        payload = {
            "sinclair_course_code_history": list(self.course_code_history),
            "input_zip_history": list(self.input_zip_history),
        }
        try:
            self.ui_state_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except Exception:
            # Best effort only: avoid blocking migration work on local state write failures.
            pass

    def _remember_sinclair_course_code(self, value: str | None = None) -> None:
        code = (
            value if value is not None else self.sinclair_course_code_var.get()
        ).strip()
        if not code:
            return

        deduped = [
            existing
            for existing in self.course_code_history
            if existing.lower() != code.lower()
        ]
        self.course_code_history = tuple(([code] + deduped)[:25])
        if hasattr(self, "sinclair_course_code_combo"):
            self.sinclair_course_code_combo.configure(values=self.course_code_history)
        self._save_ui_state()

    def _remember_input_zip_path(self, value: str | None = None) -> None:
        zip_path = (value if value is not None else self.input_zip_var.get()).strip()
        if not zip_path:
            return

        deduped = [
            existing
            for existing in self.input_zip_history
            if existing.lower() != zip_path.lower()
        ]
        self.input_zip_history = tuple(([zip_path] + deduped)[:25])
        if hasattr(self, "input_zip_combo"):
            self.input_zip_combo.configure(values=self.input_zip_history)
        self._save_ui_state()

    def _apply_input_zip_inference(self) -> None:
        input_zip_text = self.input_zip_var.get().strip()
        if not input_zip_text:
            return
        inferred = self._infer_course_code_from_input_zip(Path(input_zip_text))
        if inferred and not self.sinclair_course_code_var.get().strip():
            self.sinclair_course_code_var.set(inferred)
        if not self.visual_original_zip_var.get().strip():
            self.visual_original_zip_var.set(input_zip_text)
        self._maybe_apply_course_folder_defaults()

    def _remember_and_apply_input_zip(self) -> None:
        self._remember_input_zip_path()
        self._apply_input_zip_inference()

    def _resolve_workspace_root(self) -> Path:
        cwd = Path.cwd()
        if (cwd / "rules").exists():
            return cwd
        return Path(__file__).resolve().parents[2]

    def _resolve_default_rules(self) -> Path:
        root = self._resolve_workspace_root()
        preferred = root / "rules" / "sinclair_pilot_rules.json"
        fallback = root / "rules" / "default_rules.json"
        if preferred.exists():
            return preferred
        return fallback

    def _load_available_policy_profiles(self) -> tuple[str, ...]:
        if not self.policy_profiles_path.exists():
            return ("standard",)
        try:
            profiles = list_policy_profiles(self.policy_profiles_path)
        except Exception:
            return ("standard",)
        if not profiles:
            return ("standard",)
        return profiles

    def _default_canvas_issues_output_path(self, course_id: str) -> Path:
        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )

        course_folder = self.sinclair_course_code_var.get().strip()
        if not course_folder:
            input_zip_text = self.input_zip_var.get().strip()
            if input_zip_text:
                input_parent_name = Path(input_zip_text).parent.name.strip()
                if input_parent_name and input_parent_name.lower() not in {"resources"}:
                    course_folder = input_parent_name

        if not course_folder:
            safe_course_id = course_id.strip() or "unknown"
            course_folder = f"course-{safe_course_id}"

        if output_root.name == course_folder:
            target_dir = output_root
        elif output_root.name.lower() == "output":
            target_dir = output_root / course_folder
        else:
            target_dir = output_root

        return target_dir / "canvas-migration-issues.json"

    def _resolve_ab_variant_dir(self, variant: str) -> Path:
        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )
        normalized_variant = variant.strip().upper() or "A"
        return output_root / "ab-test" / normalized_variant

    def _slugify_token(self, value: str) -> str:
        lowered = value.strip().lower()
        cleaned = re.sub(r"[^a-z0-9._-]+", "-", lowered)
        cleaned = cleaned.strip("-._")
        return cleaned or "course"

    def _split_hint_tokens(self, value: str) -> tuple[str, ...]:
        tokens: list[str] = []
        for part in re.split(r"[,;\n]+", value or ""):
            token = part.strip().lower()
            if not token or token in tokens:
                continue
            tokens.append(token)
        return tuple(tokens)

    def _ab_artifact_prefix(self, variant: str) -> str:
        code = self.sinclair_course_code_var.get().strip()
        if not code:
            code = self.canvas_course_id_var.get().strip() or "course"
        return f"{self._slugify_token(code)}-ab-{variant.strip().upper() or 'A'}"

    def _default_ab_issues_output_path(self, variant: str, stage: str = "pre") -> Path:
        ab_dir = self._resolve_ab_variant_dir(variant)
        prefix = self._ab_artifact_prefix(variant)
        normalized_stage = stage.strip().lower() or "pre"
        return ab_dir / f"{prefix}.canvas-migration-issues-{normalized_stage}.json"

    def _sync_issues_output_for_ab_variant(self) -> None:
        current_output = self.canvas_issues_output_var.get().strip()
        if not current_output:
            self.canvas_issues_output_var.set(
                str(
                    self._default_ab_issues_output_path(
                        self.ab_variant_var.get(), "pre"
                    )
                )
            )
            return

        normalized = current_output.replace("\\", "/").lower()
        if "/ab-test/" in normalized or self._should_auto_reset_canvas_issues_output(
            current_output
        ):
            self.canvas_issues_output_var.set(
                str(
                    self._default_ab_issues_output_path(
                        self.ab_variant_var.get(), "pre"
                    )
                )
            )

    def _toggle_canvas_advanced(self) -> None:
        self.show_canvas_advanced_var.set(not self.show_canvas_advanced_var.get())
        self._apply_canvas_advanced_visibility()

    def _apply_canvas_advanced_visibility(self) -> None:
        show = bool(self.show_canvas_advanced_var.get())
        if hasattr(self, "canvas_advanced_frame"):
            if show:
                self.canvas_advanced_frame.grid()
            else:
                self.canvas_advanced_frame.grid_remove()
        if hasattr(self, "canvas_advanced_toggle_btn"):
            self.canvas_advanced_toggle_btn.configure(
                text="Hide Advanced Options" if show else "Show Advanced Options"
            )

    def _toggle_optional_tools(self) -> None:
        self.show_optional_tools_var.set(not self.show_optional_tools_var.get())
        self._apply_optional_tools_visibility()

    def _apply_optional_tools_visibility(self) -> None:
        show = bool(self.show_optional_tools_var.get())
        if hasattr(self, "optional_tools_frame"):
            if show:
                self.optional_tools_frame.grid()
            else:
                self.optional_tools_frame.grid_remove()
        if hasattr(self, "optional_tools_toggle_btn"):
            self.optional_tools_toggle_btn.configure(
                text="Hide Advanced Tools" if show else "Show Advanced Tools"
            )

    def _on_canvas_course_id_changed(self) -> None:
        current_course_id = self.canvas_course_id_var.get().strip()
        if current_course_id != self._last_canvas_course_id:
            if self.canvas_migration_id_var.get().strip():
                self.canvas_migration_id_var.set("")
            self._last_canvas_course_id = current_course_id
        self._maybe_sync_canvas_issues_output_path()

    def _pick_latest_migration_id(self, migrations: list[dict]) -> str:
        candidates = [
            row
            for row in migrations
            if isinstance(row, dict) and str(row.get("id", "")).strip()
        ]
        if not candidates:
            return ""

        def sort_key(row: dict) -> tuple[str, int]:
            created_at = str(row.get("created_at", "")).strip()
            raw_id = str(row.get("id", "")).strip()
            try:
                id_number = int(raw_id)
            except ValueError:
                id_number = -1
            return (created_at, id_number)

        return str(max(candidates, key=sort_key).get("id", "")).strip()

    def _resolve_alias_map_path(self, *, show_warning: bool = True) -> Path | None:
        if not self.use_template_alias_map_var.get():
            return None

        alias_map_text = self.template_alias_map_var.get().strip()
        if not alias_map_text:
            if show_warning:
                messagebox.showwarning(
                    "Missing alias map",
                    "Template alias mapping is enabled, but no alias map JSON path is set.",
                )
            return None

        alias_path = Path(alias_map_text)
        if not alias_path.exists():
            if show_warning:
                messagebox.showwarning(
                    "Missing alias map",
                    f"Template alias map JSON does not exist: {alias_path}",
                )
            return None
        return alias_path

    def _infer_course_code_from_input_zip(self, input_zip_path: Path) -> str:
        input_parent_name = input_zip_path.parent.name.strip()
        if input_parent_name and input_parent_name.lower() not in {"resources"}:
            return input_parent_name

        stem = input_zip_path.stem.lower()
        match = re.search(r"(edu)[-_ ]?(\d{4,5})", stem)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        return ""

    def _maybe_apply_course_folder_defaults(self) -> None:
        course_code = self.sinclair_course_code_var.get().strip()
        if not course_code:
            return

        output_root = self._resolve_workspace_root() / "output"
        output_dir_text = self.output_dir_var.get().strip()
        output_dir = Path(output_dir_text) if output_dir_text else output_root
        if self._should_auto_reset_output_dir(output_dir):
            self.output_dir_var.set(str(output_root / course_code))

        self._maybe_sync_canvas_issues_output_path()
        self._maybe_sync_visual_audit_paths()
        if hasattr(self, "readiness_local_var"):
            self._refresh_readiness_snapshot()

    def _should_auto_reset_output_dir(self, output_dir: Path) -> bool:
        workspace_output = (self._resolve_workspace_root() / "output").resolve()
        try:
            relative = output_dir.resolve().relative_to(workspace_output)
        except ValueError:
            return False
        return len(relative.parts) <= 1

    def _maybe_sync_canvas_issues_output_path(self) -> None:
        current_output = self.canvas_issues_output_var.get().strip()
        if self._should_auto_reset_canvas_issues_output(current_output):
            self.canvas_issues_output_var.set(
                str(
                    self._default_canvas_issues_output_path(
                        self.canvas_course_id_var.get().strip()
                    )
                )
            )

    def _should_auto_reset_canvas_issues_output(self, current_output: str) -> bool:
        if not current_output:
            return True
        current_path = Path(current_output)
        if current_path.name != "canvas-migration-issues.json":
            return False
        workspace_output = (self._resolve_workspace_root() / "output").resolve()
        try:
            current_path.resolve().relative_to(workspace_output)
            return True
        except ValueError:
            return False

    def _default_visual_converted_zip_path(self) -> Path | None:
        input_zip_text = self.input_zip_var.get().strip()
        if not input_zip_text:
            return None
        input_zip = Path(input_zip_text)
        output_dir_text = self.output_dir_var.get().strip()
        output_dir = (
            Path(output_dir_text)
            if output_dir_text
            else (self._resolve_workspace_root() / "output")
        )
        return output_dir / f"{input_zip.stem}.canvas-ready.zip"

    def _should_auto_reset_visual_audit_path(
        self, current_output: str, expected_name_suffix: str
    ) -> bool:
        if not current_output:
            return True
        current = Path(current_output)
        if expected_name_suffix and not current.name.endswith(expected_name_suffix):
            return False
        workspace_output = (self._resolve_workspace_root() / "output").resolve()
        try:
            current.resolve().relative_to(workspace_output)
            return True
        except ValueError:
            return False

    def _maybe_sync_visual_audit_paths(self) -> None:
        converted_default = self._default_visual_converted_zip_path()
        if converted_default is None:
            return
        if self._should_auto_reset_visual_audit_path(
            self.visual_converted_zip_var.get().strip(),
            ".canvas-ready.zip",
        ):
            self.visual_converted_zip_var.set(str(converted_default))

        visual_output_default = _default_visual_audit_json_path(converted_default)
        if self._should_auto_reset_visual_audit_path(
            self.visual_audit_output_var.get().strip(),
            ".visual-audit.json",
        ):
            self.visual_audit_output_var.set(str(visual_output_default))

        math_output_default = _default_math_audit_json_path(converted_default)
        if self._should_auto_reset_visual_audit_path(
            self.math_audit_output_var.get().strip(),
            ".math-audit.json",
        ):
            self.math_audit_output_var.set(str(math_output_default))

    def _build_layout(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
        style.configure("Primary.TButton", padding=(12, 6))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=4)
        self.root.rowconfigure(2, weight=1)

        # ── Top bar (always visible) ─────────────────────────────────────
        top_bar = ttk.Frame(self.root, padding=(12, 8, 12, 4))
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.columnconfigure(0, weight=1)
        top_bar.columnconfigure(1, weight=1)

        course_lf = ttk.LabelFrame(top_bar, text="Course", padding=(10, 4))
        course_lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        course_lf.columnconfigure(1, weight=1)
        ttk.Label(course_lf, text="Sinclair code").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.sinclair_course_code_combo = ttk.Combobox(
            course_lf,
            textvariable=self.sinclair_course_code_var,
            values=self.course_code_history,
        )
        self.sinclair_course_code_combo.grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=2
        )
        self.sinclair_course_code_combo.bind(
            "<<ComboboxSelected>>", lambda *_: self._remember_sinclair_course_code()
        )
        self.sinclair_course_code_combo.bind(
            "<FocusOut>", lambda *_: self._remember_sinclair_course_code()
        )
        ttk.Label(course_lf, text="Canvas course ID").grid(
            row=1, column=0, sticky="w", pady=2
        )
        ttk.Entry(course_lf, textvariable=self.canvas_course_id_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=2
        )

        api_lf = ttk.LabelFrame(top_bar, text="Canvas API", padding=(10, 4))
        api_lf.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        api_lf.columnconfigure(1, weight=1)
        ttk.Label(api_lf, text="Base URL").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(api_lf, textvariable=self.canvas_base_url_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=2
        )
        ttk.Label(api_lf, text="API token").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(api_lf, textvariable=self.canvas_token_var, show="*").grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=2
        )

        ttk.Label(
            top_bar,
            text="Workflow:  1 Convert  ›  2 Review  ›  3 Upload to Canvas  ›  4 Post-Import",
            font=("TkDefaultFont", 9),
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ── Notebook ─────────────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=(6, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        convert_inner = self._make_scrollable_tab("  1 · Convert  ")
        review_inner = self._make_scrollable_tab("  2 · Review  ")
        upload_inner = self._make_scrollable_tab("  3 · Upload to Canvas  ")
        postimport_inner = self._make_scrollable_tab("  4 · Post-Import  ")
        tools_inner = self._make_scrollable_tab("  Tools  ")

        self._build_convert_tab(convert_inner)
        self._build_review_tab(review_inner)
        self._build_upload_tab(upload_inner)
        self._build_postimport_tab(postimport_inner)
        self._build_tools_tab(tools_inner)

        # ── Log frame (always visible) ────────────────────────────────────
        log_frame = ttk.LabelFrame(self.root, text="Run Log", padding=(8, 4))
        log_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        log_toolbar.columnconfigure(0, weight=1)
        ttk.Label(
            log_toolbar, textvariable=self.status_text_var, font=("TkDefaultFont", 9)
        ).grid(row=0, column=0, sticky="w")
        self.clear_log_btn = ttk.Button(
            log_toolbar, text="Clear Log", command=self._clear_log_clicked
        )
        self.clear_log_btn.grid(row=0, column=1, sticky="e")

        self.log_text = tk.Text(log_frame, wrap="word", height=10)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self._on_tab_changed(None)
        self._apply_canvas_advanced_visibility()
        self._bind_mousewheel()
        self._log("Ready. Select a D2L zip and click Prepare Canvas Package.")
        self._refresh_readiness_snapshot()

    # ── Layout helpers ────────────────────────────────────────────────────

    def _make_scrollable_tab(self, title: str) -> ttk.Frame:
        """Add a scrollable tab to self.notebook; return the inner content frame."""
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text=title)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas, padding=(12, 8))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width)
        )

        self._tab_canvases.append(canvas)
        return inner

    def _on_tab_changed(self, event) -> None:
        """Rebind mousewheel to the newly active tab's scroll canvas."""
        try:
            idx = self.notebook.index("current")
        except Exception:
            idx = 0
        if 0 <= idx < len(self._tab_canvases):
            self._active_scroll_canvas = self._tab_canvases[idx]
        self._bind_mousewheel()

    # ── Tab content builders ──────────────────────────────────────────────

    def _build_convert_tab(self, parent: ttk.Frame) -> None:
        """Convert tab: D2L → Canvas pipeline settings and buttons."""
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)

        # Input sources
        src = ttk.LabelFrame(parent, text="Input", padding=10)
        src.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        src.columnconfigure(1, weight=1)

        ttk.Label(src, text="D2L export ZIP").grid(row=0, column=0, sticky="w", pady=3)
        self.input_zip_combo = ttk.Combobox(
            src, textvariable=self.input_zip_var, values=self.input_zip_history
        )
        self.input_zip_combo.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        self.input_zip_combo.bind(
            "<<ComboboxSelected>>", lambda *_: self._remember_and_apply_input_zip()
        )
        self.input_zip_combo.bind(
            "<FocusOut>", lambda *_: self._remember_and_apply_input_zip()
        )
        ttk.Button(
            src,
            text="Browse ZIP",
            command=lambda: self._browse_file(
                self.input_zip_var, [("ZIP files", "*.zip"), ("All files", "*.*")]
            ),
        ).grid(row=0, column=2, sticky="e", pady=3)
        self._add_file_row(
            src,
            1,
            "Rules JSON",
            self.rules_var,
            "Browse Rules",
            [("JSON files", "*.json"), ("All files", "*.*")],
        )
        self._add_dir_row(
            src, 2, "Output directory", self.output_dir_var, "Browse Output"
        )
        self._add_file_row(
            src,
            3,
            "Template package (.imscc, optional)",
            self.template_package_var,
            "Browse Template",
            [("Canvas Package", "*.imscc"), ("All files", "*.*")],
        )
        self._add_file_row(
            src,
            4,
            "Template alias map JSON (optional)",
            self.template_alias_map_var,
            "Browse Alias Map",
            [("JSON files", "*.json"), ("All files", "*.*")],
        )
        tmpl_row = ttk.Frame(src)
        tmpl_row.grid(row=5, column=1, columnspan=2, sticky="w", pady=(2, 4))
        self.enable_template_overlay_check = ttk.Checkbutton(
            tmpl_row,
            text="Apply Template Overlay",
            variable=self.enable_template_overlay_var,
        )
        self.enable_template_overlay_check.grid(
            row=0, column=0, sticky="w", padx=(0, 16)
        )
        self.template_overlay_use_alias_map_check = ttk.Checkbutton(
            tmpl_row,
            text="Use alias map for Template Overlay",
            variable=self.template_overlay_use_alias_map_var,
        )
        self.template_overlay_use_alias_map_check.grid(row=0, column=1, sticky="w")

        # Conversion options
        opts = ttk.LabelFrame(parent, text="Conversion Options", padding=10)
        opts.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(2, weight=2)

        ttk.Label(opts, text="Policy profile").grid(row=0, column=0, sticky="w", pady=3)
        self.policy_profile_combo = ttk.Combobox(
            opts,
            textvariable=self.policy_profile_var,
            values=self.available_policy_profiles,
            state="readonly",
        )
        self.policy_profile_combo.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        self.policy_profile_combo.current(
            self.available_policy_profiles.index(self.policy_profile_var.get())
        )

        ttk.Label(opts, text="Accordion handling").grid(
            row=1, column=0, sticky="w", pady=3
        )
        self.accordion_handling_combo = ttk.Combobox(
            opts,
            textvariable=self.accordion_handling_var,
            values=("smart", "details", "flatten", "none"),
            state="readonly",
        )
        self.accordion_handling_combo.grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(
            opts,
            text="Smart: content pages → details blocks; syllabus/policy → flatten.",
            wraplength=480,
            justify="left",
            foreground="#555555",
        ).grid(row=1, column=2, sticky="w", pady=3)

        ttk.Label(opts, text="Accordion title align").grid(
            row=2, column=0, sticky="w", pady=3
        )
        self.accordion_alignment_combo = ttk.Combobox(
            opts,
            textvariable=self.accordion_alignment_var,
            values=("left", "center"),
            state="readonly",
        )
        self.accordion_alignment_combo.grid(row=2, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(opts, text="Always flatten on pages containing").grid(
            row=3, column=0, sticky="w", pady=3
        )
        ttk.Entry(opts, textvariable=self.accordion_flatten_hints_var).grid(
            row=3, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(
            opts,
            text="Comma-separated hints, e.g. syllabus, policy",
            foreground="#888888",
        ).grid(row=3, column=2, sticky="w", pady=3)

        ttk.Label(opts, text="Always keep details on pages containing").grid(
            row=4, column=0, sticky="w", pady=3
        )
        ttk.Entry(opts, textvariable=self.accordion_details_hints_var).grid(
            row=4, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(
            opts,
            text="Comma-separated hints, e.g. lesson, faq, student resources",
            foreground="#888888",
        ).grid(row=4, column=2, sticky="w", pady=3)

        ttk.Label(opts, text="Image layout mode").grid(
            row=5, column=0, sticky="w", pady=3
        )
        self.image_layout_mode_combo = ttk.Combobox(
            opts,
            textvariable=self.image_layout_mode_var,
            values=("safe-block", "preserve-wrap"),
            state="readonly",
        )
        self.image_layout_mode_combo.grid(row=5, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(
            opts,
            text="safe-block avoids text overlap; preserve-wrap keeps left/right wraps.",
            foreground="#555555",
        ).grid(row=5, column=2, sticky="w", pady=3)

        ttk.Label(opts, text="Math handling").grid(row=6, column=0, sticky="w", pady=3)
        self.math_handling_combo = ttk.Combobox(
            opts,
            textvariable=self.math_handling_var,
            values=("preserve-semantic", "canvas-equation-compatible", "audit-only"),
            state="readonly",
        )
        self.math_handling_combo.grid(row=6, column=1, sticky="w", padx=6, pady=3)

        # Template standards
        tpl = ttk.LabelFrame(parent, text="Template Standards", padding=10)
        tpl.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.template_module_structure_check = ttk.Checkbutton(
            tpl,
            text="Apply Module Structure  (Topic → Module, Overview / Learning Activities / Review layout)",
            variable=self.template_module_structure_var,
        )
        self.template_module_structure_check.grid(row=0, column=0, sticky="w", pady=2)
        self.template_visual_standards_check = ttk.Checkbutton(
            tpl,
            text="Apply Visual Standards  (icons, dividers, image presentation, heading fidelity)",
            variable=self.template_visual_standards_var,
        )
        self.template_visual_standards_check.grid(row=1, column=0, sticky="w", pady=2)
        self.template_color_standards_check = ttk.Checkbutton(
            tpl,
            text="\u21b3  Apply Color Standards  (Sinclair red accents, heading colors)",
            variable=self.template_color_standards_var,
        )
        self.template_color_standards_check.grid(
            row=2, column=0, sticky="w", pady=2, padx=(24, 0)
        )
        self.template_divider_standards_check = ttk.Checkbutton(
            tpl,
            text="\u21b3  Apply Divider Standards  (10 px red page-heading underlines, 8 px closing rules)",
            variable=self.template_divider_standards_var,
        )
        self.template_divider_standards_check.grid(
            row=3, column=0, sticky="w", pady=2, padx=(24, 0)
        )
        ttk.Checkbutton(
            tpl,
            text="Apply Best-Practice Enforcer (safe subset)",
            variable=self.enable_best_practice_enforcer_var,
        ).grid(row=4, column=0, sticky="w", pady=(8, 2))

        # Action buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 4))
        btn_frame.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            btn_frame,
            text="Open page review in browser when done",
            variable=self.auto_open_page_review_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.run_migration_btn = ttk.Button(
            btn_frame,
            text="Advanced: Convert Only",
            command=self._run_migration_clicked,
        )
        self.run_migration_btn.grid(row=0, column=1, padx=(0, 8))
        self.run_full_pipeline_btn = ttk.Button(
            btn_frame,
            text="Prepare Canvas Package",
            command=self._run_pre_import_pipeline_clicked,
            style="Primary.TButton",
        )
        self.run_full_pipeline_btn.grid(row=0, column=2)

        self._sync_template_visual_subcontrols_state()

    def _build_review_tab(self, parent: ttk.Frame) -> None:
        """Review tab: readiness snapshot and ZIP quality review tools."""
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=0)

        # Readiness snapshot
        snap = ttk.LabelFrame(parent, text="Readiness Snapshot", padding=10)
        snap.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        snap.columnconfigure(0, weight=1)
        ttk.Label(
            snap, text="Current pipeline status for this course:", foreground="#555555"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            snap, textvariable=self.readiness_local_var, justify="left", wraplength=1080
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            snap,
            textvariable=self.readiness_review_var,
            justify="left",
            wraplength=1080,
        ).grid(row=2, column=0, sticky="w", pady=(3, 0))
        ttk.Label(
            snap,
            textvariable=self.readiness_canvas_var,
            justify="left",
            wraplength=1080,
        ).grid(row=3, column=0, sticky="w", pady=(3, 0))
        ttk.Label(
            snap,
            textvariable=self.readiness_next_step_var,
            justify="left",
            wraplength=1080,
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        snap_action_row = ttk.Frame(snap)
        snap_action_row.grid(row=5, column=0, sticky="ew", pady=(10, 2))
        snap_action_row.columnconfigure(0, weight=1)
        self.build_approval_report_btn = ttk.Button(
            snap_action_row,
            text="Run Review Readiness",
            command=self._build_approval_report_clicked,
            style="Primary.TButton",
        )
        self.build_approval_report_btn.grid(row=0, column=0, sticky="w")
        self.open_page_review_btn = ttk.Button(
            snap_action_row,
            text="Open Page Review in Browser ↗",
            command=self._open_page_review_in_browser,
        )
        self.open_page_review_btn.grid(row=0, column=1, sticky="e", padx=(8, 0))

        # ZIP review tools
        visual = ttk.LabelFrame(parent, text="ZIP Review Tools", padding=10)
        visual.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        visual.columnconfigure(1, weight=1)

        self._add_file_row(
            visual,
            0,
            "Original D2L ZIP",
            self.visual_original_zip_var,
            "Browse ZIP",
            [("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        self._add_file_row(
            visual,
            1,
            "Converted canvas-ready ZIP",
            self.visual_converted_zip_var,
            "Browse ZIP",
            [("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        self._add_file_row(
            visual,
            2,
            "Visual audit JSON output",
            self.visual_audit_output_var,
            "Save As",
            [("JSON files", "*.json"), ("All files", "*.*")],
            save_mode=True,
        )
        self._add_file_row(
            visual,
            3,
            "Math audit JSON output",
            self.math_audit_output_var,
            "Save As",
            [("JSON files", "*.json"), ("All files", "*.*")],
            save_mode=True,
        )
        self._add_file_row(
            visual,
            4,
            "Review draft JSON",
            self.review_draft_json_var,
            "Browse Draft",
            [("JSON files", "*.json"), ("All files", "*.*")],
        )
        self._add_file_row(
            visual,
            5,
            "Reviewed ZIP output",
            self.reviewed_zip_output_var,
            "Save As",
            [("ZIP files", "*.zip"), ("All files", "*.*")],
            save_mode=True,
        )
        self._add_file_row(
            visual,
            6,
            "Pattern report JSON output",
            self.pattern_report_output_var,
            "Save As",
            [("JSON files", "*.json"), ("All files", "*.*")],
            save_mode=True,
        )

        visual_actions = ttk.Frame(visual)
        visual_actions.grid(row=7, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.run_visual_audit_btn = ttk.Button(
            visual_actions, text="Visual Audit", command=self._run_visual_audit_clicked
        )
        self.run_visual_audit_btn.grid(row=0, column=0, padx=(0, 6))
        self.run_math_audit_btn = ttk.Button(
            visual_actions, text="Math Audit", command=self._run_math_audit_clicked
        )
        self.run_math_audit_btn.grid(row=0, column=1, padx=(0, 6))
        self.build_page_review_btn = ttk.Button(
            visual_actions,
            text="Page Review Workbench",
            command=self._build_page_review_clicked,
        )
        self.build_page_review_btn.grid(row=0, column=2, padx=(0, 6))
        self.apply_review_draft_btn = ttk.Button(
            visual_actions,
            text="Apply Review Draft",
            command=self._apply_review_draft_clicked,
        )
        self.apply_review_draft_btn.grid(row=0, column=3, padx=(0, 6))
        self.build_pattern_report_btn = ttk.Button(
            visual_actions,
            text="Pattern Report",
            command=self._build_pattern_report_clicked,
        )
        self.build_pattern_report_btn.grid(row=0, column=4)

    def _build_upload_tab(self, parent: ttk.Frame) -> None:
        """Upload to Canvas tab: sandbox upload and template page injection."""
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=0)

        intro_lf = ttk.LabelFrame(parent, text="About This Step", padding=10)
        intro_lf.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        intro_lf.columnconfigure(0, weight=1)
        ttk.Label(
            intro_lf,
            text=(
                "Upload the converted Canvas-ready ZIP to your Canvas sandbox for visual verification "
                "before handing off to the instructor.  The package is imported via the Canvas migration "
                "API and page preview URLs are returned for spot-checking.  Canvas course ID and API "
                "token are read from the top bar."
            ),
            wraplength=1080,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        # Package paths
        pkg = ttk.LabelFrame(parent, text="Package", padding=10)
        pkg.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        pkg.columnconfigure(1, weight=1)
        ttk.Label(pkg, text="Canvas-ready ZIP").grid(
            row=0, column=0, sticky="w", pady=3
        )
        ttk.Entry(pkg, textvariable=self.canvas_upload_zip_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Button(
            pkg,
            text="Browse ZIP",
            command=lambda: self._browse_file(
                self.canvas_upload_zip_var,
                [("ZIP files", "*.zip"), ("All files", "*.*")],
            ),
        ).grid(row=0, column=2, sticky="e", pady=3)
        ttk.Label(pkg, text="Template package (.imscc)").grid(
            row=1, column=0, sticky="w", pady=3
        )
        _tmpl_entry = ttk.Entry(pkg, textvariable=self.canvas_upload_template_zip_var)
        _tmpl_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        _tmpl_browse = ttk.Button(
            pkg,
            text="Browse",
            command=lambda: self._browse_file(
                self.canvas_upload_template_zip_var,
                [("Canvas packages", "*.imscc *.zip"), ("All files", "*.*")],
            ),
        )
        _tmpl_browse.grid(row=1, column=2, sticky="e", pady=3)

        def _toggle_template_widgets(*_):
            state = (
                "normal"
                if self.canvas_upload_include_template_var.get()
                else "disabled"
            )
            _tmpl_entry.configure(state=state)
            _tmpl_browse.configure(state=state)

        self.canvas_upload_include_template_var.trace_add(
            "write", _toggle_template_widgets
        )
        ttk.Checkbutton(
            pkg,
            text="Import template first (uncheck if the course already has the template)",
            variable=self.canvas_upload_include_template_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._add_file_row(
            pkg,
            3,
            "Preview result JSON (output)",
            self.canvas_preview_output_var,
            "Save As",
            [("JSON files", "*.json"), ("All files", "*.*")],
            save_mode=True,
        )

        # Upload button
        upload_btn_frame = ttk.Frame(parent)
        upload_btn_frame.grid(row=2, column=0, columnspan=3, sticky="e", pady=(4, 4))
        self.run_canvas_upload_btn = ttk.Button(
            upload_btn_frame,
            text="Upload to Canvas Sandbox",
            command=self._run_canvas_upload_clicked,
            style="Primary.TButton",
        )
        self.run_canvas_upload_btn.grid(row=0, column=0)

        # Results
        results_lf = ttk.LabelFrame(
            parent,
            text="Upload Results \u2014 click a URL to open in browser",
            padding=10,
        )
        results_lf.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(0, 0))
        parent.rowconfigure(3, weight=1)
        results_lf.columnconfigure(0, weight=1)
        results_lf.rowconfigure(0, weight=1)

        self.upload_results_text = tk.Text(
            results_lf, wrap="word", height=12, state="disabled", cursor="arrow"
        )
        self.upload_results_text.grid(row=0, column=0, sticky="nsew")
        results_scroll = ttk.Scrollbar(
            results_lf, command=self.upload_results_text.yview
        )
        results_scroll.grid(row=0, column=1, sticky="ns")
        self.upload_results_text.configure(yscrollcommand=results_scroll.set)
        self.upload_results_text.tag_configure(
            "url", foreground="#0066cc", underline=True
        )
        self.upload_results_text.tag_bind(
            "url", "<Button-1>", self._on_upload_url_clicked
        )
        self.upload_results_text.tag_bind(
            "url",
            "<Enter>",
            lambda e: self.upload_results_text.configure(cursor="hand2"),
        )
        self.upload_results_text.tag_bind(
            "url",
            "<Leave>",
            lambda e: self.upload_results_text.configure(cursor="arrow"),
        )

    def _build_postimport_tab(self, parent: ttk.Frame) -> None:
        """Post-Import tab: Canvas cleanup, issues, snapshot, fix checklist."""
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=0)

        intro_lf = ttk.LabelFrame(parent, text="About This Step", padding=10)
        intro_lf.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(
            intro_lf,
            text=(
                "After importing into Canvas, run Canvas Cleanup + Audit to fix broken links and "
                "capture a course snapshot.  Export the migration issue log, build a fix checklist, "
                "and re-run Review Readiness to score the final migration quality."
            ),
            wraplength=1080,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        # Primary actions
        primary = ttk.LabelFrame(parent, text="Actions", padding=10)
        primary.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        primary.columnconfigure(0, weight=1)

        row1 = ttk.Frame(primary)
        row1.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.run_full_post_import_btn = ttk.Button(
            row1,
            text="Run Canvas Cleanup + Audit",
            command=self._run_full_post_import_clicked,
            style="Primary.TButton",
        )
        self.run_full_post_import_btn.grid(row=0, column=0, padx=(0, 8))
        self.snapshot_canvas_course_btn = ttk.Button(
            row1,
            text="Capture Course Snapshot",
            command=self._snapshot_canvas_course_clicked,
        )
        self.snapshot_canvas_course_btn.grid(row=0, column=1, padx=(0, 8))
        self.run_post_import_pipeline_btn = ttk.Button(
            row1,
            text="Export Issues + Checklist",
            command=self._run_post_import_pipeline_clicked,
        )
        self.run_post_import_pipeline_btn.grid(row=0, column=2)

        row2 = ttk.Frame(primary)
        row2.grid(row=1, column=0, sticky="w")
        self.auto_relink_btn = ttk.Button(
            row2,
            text="Auto-Relink Missing Links",
            command=self._auto_relink_missing_links_clicked,
        )
        self.auto_relink_btn.grid(row=0, column=0, padx=(0, 8))
        self.live_link_audit_btn = ttk.Button(
            row2, text="Live Link Audit", command=self._run_live_link_audit_clicked
        )
        self.live_link_audit_btn.grid(row=0, column=1, padx=(0, 8))
        ttk.Checkbutton(
            row2, text="Apply Safe Fixes", variable=self.live_audit_apply_safe_fixes_var
        ).grid(row=0, column=2, padx=(4, 0))

        # Advanced toggle
        self.canvas_advanced_toggle_btn = ttk.Button(
            parent, text="Show Advanced Options", command=self._toggle_canvas_advanced
        )
        self.canvas_advanced_toggle_btn.grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )

        self.canvas_advanced_frame = ttk.LabelFrame(
            parent, text="Advanced Options", padding=10
        )
        self.canvas_advanced_frame.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )
        self.canvas_advanced_frame.columnconfigure(1, weight=1)

        ttk.Label(self.canvas_advanced_frame, text="Migration ID").grid(
            row=0, column=0, sticky="w", pady=3
        )
        ttk.Entry(
            self.canvas_advanced_frame, textvariable=self.canvas_migration_id_var
        ).grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        ttk.Label(self.canvas_advanced_frame, text="Issues JSON output").grid(
            row=1, column=0, sticky="w", pady=3
        )
        ttk.Entry(
            self.canvas_advanced_frame, textvariable=self.canvas_issues_output_var
        ).grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(
            self.canvas_advanced_frame,
            text="Save As",
            command=lambda: self._browse_file(
                self.canvas_issues_output_var,
                [("JSON files", "*.json"), ("All files", "*.*")],
                save_mode=True,
            ),
        ).grid(row=1, column=2, sticky="e", pady=3)
        ttk.Label(self.canvas_advanced_frame, text="Template alias map").grid(
            row=2, column=0, sticky="w", pady=3
        )
        ttk.Entry(
            self.canvas_advanced_frame, textvariable=self.template_alias_map_var
        ).grid(row=2, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(
            self.canvas_advanced_frame,
            text="Browse",
            command=lambda: self._browse_file(
                self.template_alias_map_var,
                [("JSON files", "*.json"), ("All files", "*.*")],
            ),
        ).grid(row=2, column=2, sticky="e", pady=3)
        ttk.Checkbutton(
            self.canvas_advanced_frame,
            text="Use template alias map during auto-relink",
            variable=self.use_template_alias_map_var,
        ).grid(row=3, column=1, sticky="w", pady=(0, 3))

        ab_row = ttk.Frame(self.canvas_advanced_frame)
        ab_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        ttk.Label(ab_row, text="A/B variant").grid(row=0, column=0, sticky="w")
        self.ab_variant_combo = ttk.Combobox(
            ab_row,
            textvariable=self.ab_variant_var,
            values=("A", "B"),
            width=6,
            state="readonly",
        )
        self.ab_variant_combo.grid(row=0, column=1, sticky="w", padx=(6, 12))
        ttk.Checkbutton(
            ab_row,
            text="Include auto-relink in A/B cycle",
            variable=self.ab_include_auto_relink_var,
        ).grid(row=0, column=2, sticky="w")
        self.run_ab_variant_cycle_btn = ttk.Button(
            ab_row, text="Run A/B Cycle", command=self._run_ab_variant_cycle_clicked
        )
        self.run_ab_variant_cycle_btn.grid(row=0, column=3, sticky="e", padx=(12, 0))

        sec_btns = ttk.Frame(self.canvas_advanced_frame)
        sec_btns.grid(row=5, column=0, columnspan=3, sticky="e", pady=(4, 0))
        self.fetch_canvas_imports_btn = ttk.Button(
            sec_btns,
            text="Find Latest Import",
            command=self._fetch_canvas_imports_clicked,
        )
        self.fetch_canvas_imports_btn.grid(row=0, column=0, padx=(0, 6))
        self.export_canvas_issues_btn = ttk.Button(
            sec_btns,
            text="Save Import Issues",
            command=self._export_canvas_issues_clicked,
        )
        self.export_canvas_issues_btn.grid(row=0, column=1, padx=(0, 6))
        self.build_fix_checklist_btn = ttk.Button(
            sec_btns,
            text="Build Fix Checklist",
            command=self._build_fix_checklist_clicked,
        )
        self.build_fix_checklist_btn.grid(row=0, column=2)

        self._apply_canvas_advanced_visibility()

    def _build_tools_tab(self, parent: ttk.Frame) -> None:
        """Tools tab: summary/clipboard, spreadsheet audit, reference docs audit."""
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=0)

        # This button satisfies _set_busy's reference; in the tab layout tools are always visible.
        self.optional_tools_toggle_btn = ttk.Button(
            parent, text="Refresh Readiness", command=self._refresh_readiness_snapshot
        )
        self.optional_tools_toggle_btn.grid(row=0, column=0, sticky="w", pady=(0, 8))

        # Summary / clipboard
        summary = ttk.LabelFrame(parent, text="Summary / Clipboard", padding=10)
        summary.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        summary.columnconfigure(1, weight=1)
        self._add_file_row(
            summary,
            0,
            "Migration report JSON",
            self.report_json_var,
            "Browse Report",
            [("JSON files", "*.json"), ("All files", "*.*")],
        )
        self._add_file_row(
            summary,
            1,
            "Safe summary output",
            self.safe_summary_path_var,
            "Save As",
            [("Text files", "*.txt"), ("All files", "*.*")],
            save_mode=True,
        )
        summary_btns = ttk.Frame(summary)
        summary_btns.grid(row=2, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.build_summary_btn = ttk.Button(
            summary_btns,
            text="Generate Safe Summary",
            command=self._generate_safe_summary_clicked,
        )
        self.build_summary_btn.grid(row=0, column=0, padx=(0, 8))
        self.copy_summary_btn = ttk.Button(
            summary_btns,
            text="Copy Summary to Clipboard",
            command=self._copy_summary_clicked,
        )
        self.copy_summary_btn.grid(row=0, column=1)

        # Spreadsheet audit
        audit = ttk.LabelFrame(parent, text="Spreadsheet Audit", padding=10)
        audit.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        audit.columnconfigure(1, weight=1)
        self._add_file_row(
            audit,
            0,
            "Best-practices file (.xlsx/.csv)",
            self.best_practices_file_var,
            "Browse File",
            [("Spreadsheet", "*.xlsx *.csv"), ("All files", "*.*")],
        )
        ttk.Label(audit, text="Excel tab name (optional, .xlsx only)").grid(
            row=1, column=0, sticky="w", pady=3
        )
        ttk.Entry(audit, textvariable=self.best_practices_sheet_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )
        self.run_audit_btn = ttk.Button(
            audit, text="Run Audit", command=self._run_best_practices_audit_clicked
        )
        self.run_audit_btn.grid(row=1, column=2, sticky="e", pady=3)

        # Reference docs audit
        reference = ttk.LabelFrame(parent, text="Reference Docs Audit", padding=10)
        reference.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        reference.columnconfigure(1, weight=1)
        self._add_file_row(
            reference,
            0,
            "Instructions docx",
            self.ref_instructions_docx_var,
            "Browse File",
            [("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            reference,
            1,
            "Best practices docx",
            self.ref_best_practices_docx_var,
            "Browse File",
            [("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            reference,
            2,
            "Set-up checklist docx",
            self.ref_setup_checklist_docx_var,
            "Browse File",
            [("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            reference,
            3,
            "Page templates docx",
            self.ref_page_templates_docx_var,
            "Browse File",
            [("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            reference,
            4,
            "Syllabus template docx",
            self.ref_syllabus_template_docx_var,
            "Browse File",
            [("Word Document", "*.docx"), ("All files", "*.*")],
        )
        ttk.Label(
            reference,
            text=(
                "The March 16, 2026 Canvas Blueprints file and the templated course set-up checklist "
                "are the default governance sources for naming, accordion, quiz, rubric, video, and "
                "release-readiness checks."
            ),
            wraplength=980,
            justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.run_reference_audit_btn = ttk.Button(
            reference,
            text="Run Reference Audit",
            command=self._run_reference_audit_clicked,
        )
        self.run_reference_audit_btn.grid(row=6, column=2, sticky="e", pady=(6, 0))

    def _add_file_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        button_text: str,
        filetypes: list[tuple[str, str]],
        save_mode: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Button(
            parent,
            text=button_text,
            command=lambda: self._browse_file(variable, filetypes, save_mode=save_mode),
        ).grid(row=row, column=2, sticky="e", pady=3)

    def _add_dir_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        button_text: str,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Button(
            parent,
            text=button_text,
            command=lambda: self._browse_directory(variable),
        ).grid(row=row, column=2, sticky="e", pady=3)

    def _browse_file(
        self,
        variable: tk.StringVar,
        filetypes: list[tuple[str, str]],
        save_mode: bool = False,
    ) -> None:
        if save_mode:
            path = filedialog.asksaveasfilename(filetypes=filetypes)
        else:
            path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            variable.set(path)
            if variable is self.input_zip_var:
                self._remember_input_zip_path(path)
                self._apply_input_zip_inference()
            elif variable is self.visual_converted_zip_var:
                self._sync_review_outputs_from_converted_zip(Path(path))

    def _browse_directory(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory()
        if path:
            variable.set(path)

    def _sync_review_outputs_from_converted_zip(self, converted_zip: Path) -> None:
        if not converted_zip.exists():
            return
        if not self.visual_converted_zip_var.get().strip():
            self.visual_converted_zip_var.set(str(converted_zip))
        if not self.visual_audit_output_var.get().strip():
            self.visual_audit_output_var.set(
                str(_default_visual_audit_json_path(converted_zip))
            )
        if not self.review_draft_json_var.get().strip():
            self.review_draft_json_var.set(
                str(_default_review_draft_json_path(converted_zip))
            )
        if not self.reviewed_zip_output_var.get().strip():
            self.reviewed_zip_output_var.set(
                str(_default_reviewed_zip_path(converted_zip))
            )
        if not self.pattern_report_output_var.get().strip():
            self.pattern_report_output_var.set(
                str(_default_pattern_report_json_path(converted_zip))
            )

    def _on_main_configure(self, event: tk.Event) -> None:
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.scroll_canvas.itemconfigure(self.scroll_window, width=event.width)

    def _bind_mousewheel(self, event: tk.Event | None = None) -> None:
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, event: tk.Event | None = None) -> None:
        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-4>")
        self.root.unbind_all("<Button-5>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        canvas = getattr(self, "_active_scroll_canvas", None)
        if canvas is None:
            return
        delta = 0
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            raw_delta = int(getattr(event, "delta", 0))
            if raw_delta == 0:
                return
            if abs(raw_delta) >= 120:
                delta = int(-raw_delta / 120)
            else:
                delta = -1 if raw_delta > 0 else 1
        canvas.yview_scroll(delta, "units")

    def _log(self, text: str) -> None:
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")

    def _clear_log_clicked(self) -> None:
        self.log_text.delete("1.0", "end")
        self._log("Log cleared.")

    def _sync_template_visual_subcontrols_state(self) -> None:
        if (
            not hasattr(self, "template_color_standards_check")
            or not hasattr(self, "template_divider_standards_check")
            or not hasattr(self, "image_layout_mode_combo")
        ):
            return
        if self.is_busy:
            state = "disabled"
        else:
            state = "normal" if self.template_visual_standards_var.get() else "disabled"
        self.template_color_standards_check.configure(state=state)
        self.template_divider_standards_check.configure(state=state)
        self.image_layout_mode_combo.configure(
            state="readonly" if state == "normal" else "disabled"
        )

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        self.input_zip_combo.configure(state=state)
        self.run_migration_btn.configure(state=state)
        self.run_full_pipeline_btn.configure(state=state)
        self.run_full_post_import_btn.configure(state=state)
        self.policy_profile_combo.configure(state="disabled" if busy else "readonly")
        self.math_handling_combo.configure(state="disabled" if busy else "readonly")
        self.image_layout_mode_combo.configure(state="disabled" if busy else "readonly")
        self.accordion_handling_combo.configure(
            state="disabled" if busy else "readonly"
        )
        self.accordion_alignment_combo.configure(
            state="disabled" if busy else "readonly"
        )
        self.template_overlay_use_alias_map_check.configure(state=state)
        self.enable_template_overlay_check.configure(state=state)
        self.template_module_structure_check.configure(state=state)
        self.template_visual_standards_check.configure(state=state)
        self._sync_template_visual_subcontrols_state()
        self.build_summary_btn.configure(state=state)
        self.copy_summary_btn.configure(
            state=state if self.latest_safe_summary else "disabled"
        )
        self.run_audit_btn.configure(state=state)
        self.run_reference_audit_btn.configure(state=state)
        self.run_visual_audit_btn.configure(state=state)
        self.run_math_audit_btn.configure(state=state)
        self.build_page_review_btn.configure(state=state)
        self.apply_review_draft_btn.configure(state=state)
        self.build_pattern_report_btn.configure(state=state)
        self.run_post_import_pipeline_btn.configure(state=state)
        self.fetch_canvas_imports_btn.configure(state=state)
        self.export_canvas_issues_btn.configure(state=state)
        self.auto_relink_btn.configure(state=state)
        self.build_fix_checklist_btn.configure(state=state)
        self.snapshot_canvas_course_btn.configure(state=state)
        self.build_approval_report_btn.configure(state=state)
        self.open_page_review_btn.configure(state=state)
        self.live_link_audit_btn.configure(state=state)
        self.run_ab_variant_cycle_btn.configure(state=state)
        self.canvas_advanced_toggle_btn.configure(state=state)
        self.optional_tools_toggle_btn.configure(state=state)
        self.run_canvas_upload_btn.configure(state=state)
        self.clear_log_btn.configure(state=state)
        self.ab_variant_combo.configure(state="disabled" if busy else "readonly")

    def _run_background(self, task_name: str, target: Callable[[], None]) -> None:
        if self.is_busy:
            return
        self._set_busy(True)
        self.status_text_var.set(f"Status: Running - {task_name}")
        self._log(f"[START] {task_name}")

        def worker() -> None:
            try:
                target()
            except SystemExit:
                raise
            except BaseException as exc:
                tb = traceback.format_exc()
                self.root.after(0, lambda: self._task_failed(task_name, exc, tb))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _task_failed(self, task_name: str, exc: Exception, traceback_text: str) -> None:
        self._set_busy(False)
        self.status_text_var.set(f"Status: Failed - {task_name}")
        self._log(f"[ERROR] {task_name}: {exc}")
        self._log(traceback_text.strip())
        messagebox.showerror("Task failed", f"{task_name} failed:\n{exc}")

    def _task_succeeded(self, task_name: str) -> None:
        self._log(f"[DONE] {task_name}")
        try:
            self._refresh_readiness_snapshot()
        except Exception:
            pass
        self._set_busy(False)
        self.status_text_var.set(f"Status: Idle (last: {task_name})")

    def _collect_local_migration_request(self) -> dict | None:
        self._maybe_apply_course_folder_defaults()
        input_zip = Path(self.input_zip_var.get().strip())
        rules_path = Path(self.rules_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip())
        policy_profile_id = self.policy_profile_var.get().strip()
        best_practice_enforcer = bool(self.enable_best_practice_enforcer_var.get())
        math_handling = (
            self.math_handling_var.get().strip().lower() or "preserve-semantic"
        )
        accordion_handling = (
            self.accordion_handling_var.get().strip().lower() or "flatten"
        )
        accordion_alignment = (
            self.accordion_alignment_var.get().strip().lower() or "left"
        )
        accordion_flatten_hints = self._split_hint_tokens(
            self.accordion_flatten_hints_var.get()
        )
        accordion_details_hints = self._split_hint_tokens(
            self.accordion_details_hints_var.get()
        )
        apply_template_module_structure = bool(self.template_module_structure_var.get())
        apply_template_visual_standards = bool(self.template_visual_standards_var.get())
        apply_template_color_standards = bool(self.template_color_standards_var.get())
        apply_template_divider_standards = bool(
            self.template_divider_standards_var.get()
        )
        image_layout_mode = (
            self.image_layout_mode_var.get().strip().lower() or "safe-block"
        )
        template_overlay_enabled = bool(self.enable_template_overlay_var.get())
        template_package: Path | None = None
        template_alias_map_json: Path | None = None

        if not input_zip.exists():
            messagebox.showwarning("Missing input", "Select a valid D2L export ZIP.")
            return None
        if not rules_path.exists():
            messagebox.showwarning("Missing rules", "Select a valid rules JSON file.")
            return None
        if not self.policy_profiles_path.exists():
            messagebox.showwarning(
                "Missing profiles",
                f"Policy profiles file not found: {self.policy_profiles_path}",
            )
            return None
        if template_overlay_enabled:
            template_package_text = self.template_package_var.get().strip()
            if not template_package_text:
                messagebox.showwarning(
                    "Missing template package",
                    "Select a template package (.imscc) or disable Template Overlay.",
                )
                return None
            template_package = Path(template_package_text)
            if not template_package.exists():
                messagebox.showwarning(
                    "Missing template package",
                    f"Template package does not exist: {template_package}",
                )
                return None
            if self.template_overlay_use_alias_map_var.get():
                alias_map_text = self.template_alias_map_var.get().strip()
                if alias_map_text:
                    alias_path = Path(alias_map_text)
                    if not alias_path.exists():
                        messagebox.showwarning(
                            "Missing alias map",
                            f"Template alias map JSON does not exist: {alias_path}",
                        )
                        return None
                    template_alias_map_json = alias_path

        if not self.visual_original_zip_var.get().strip():
            self.visual_original_zip_var.set(str(input_zip))
        self._remember_sinclair_course_code()
        self._remember_input_zip_path(str(input_zip))

        return {
            "input_zip": input_zip,
            "rules_path": rules_path,
            "output_dir": output_dir,
            "policy_profile_id": policy_profile_id,
            "best_practice_enforcer": best_practice_enforcer,
            "math_handling": math_handling,
            "accordion_handling": accordion_handling,
            "accordion_alignment": accordion_alignment,
            "accordion_flatten_hints": accordion_flatten_hints,
            "accordion_details_hints": accordion_details_hints,
            "apply_template_module_structure": apply_template_module_structure,
            "apply_template_visual_standards": apply_template_visual_standards,
            "apply_template_color_standards": apply_template_color_standards,
            "apply_template_divider_standards": apply_template_divider_standards,
            "image_layout_mode": image_layout_mode,
            "template_package": template_package,
            "template_alias_map_json": template_alias_map_json,
            "reference_audit_json": self._find_reference_audit_json(),
        }

    def _run_migration_clicked(self) -> None:
        request = self._collect_local_migration_request()
        if request is None:
            return

        def task() -> None:
            result = run_migration(
                input_zip=request["input_zip"],
                output_dir=request["output_dir"],
                rules_path=request["rules_path"],
                policy_profile_id=request["policy_profile_id"],
                policy_profiles_path=self.policy_profiles_path,
                reference_audit_json=request["reference_audit_json"],
                best_practice_enforcer=request["best_practice_enforcer"],
                template_package=request["template_package"],
                template_alias_map_json=request["template_alias_map_json"],
                math_handling=request["math_handling"],
                accordion_handling=request["accordion_handling"],
                accordion_alignment=request["accordion_alignment"],
                accordion_flatten_hints=request["accordion_flatten_hints"],
                accordion_details_hints=request["accordion_details_hints"],
                apply_template_module_structure=request[
                    "apply_template_module_structure"
                ],
                apply_template_visual_standards=request[
                    "apply_template_visual_standards"
                ],
                apply_template_color_standards=request[
                    "apply_template_color_standards"
                ],
                apply_template_divider_standards=request[
                    "apply_template_divider_standards"
                ],
                image_layout_mode=request["image_layout_mode"],
            )
            self.root.after(
                0,
                lambda: self._handle_migration_result(
                    result, request["reference_audit_json"]
                ),
            )

        self._run_background("Run local migration", task)

    def _run_pre_import_pipeline_clicked(self) -> None:
        request = self._collect_local_migration_request()
        if request is None:
            return

        def task() -> None:
            result = run_migration(
                input_zip=request["input_zip"],
                output_dir=request["output_dir"],
                rules_path=request["rules_path"],
                policy_profile_id=request["policy_profile_id"],
                policy_profiles_path=self.policy_profiles_path,
                reference_audit_json=request["reference_audit_json"],
                best_practice_enforcer=request["best_practice_enforcer"],
                template_package=request["template_package"],
                template_alias_map_json=request["template_alias_map_json"],
                math_handling=request["math_handling"],
                accordion_handling=request["accordion_handling"],
                accordion_alignment=request["accordion_alignment"],
                accordion_flatten_hints=request["accordion_flatten_hints"],
                accordion_details_hints=request["accordion_details_hints"],
                apply_template_module_structure=request[
                    "apply_template_module_structure"
                ],
                apply_template_visual_standards=request[
                    "apply_template_visual_standards"
                ],
                apply_template_color_standards=request[
                    "apply_template_color_standards"
                ],
                apply_template_divider_standards=request[
                    "apply_template_divider_standards"
                ],
                image_layout_mode=request["image_layout_mode"],
            )

            safe_summary_path = _default_safe_summary_path(result.report_json)
            safe_summary = build_safe_summary_from_path(result.report_json)
            safe_summary_path.write_text(safe_summary, encoding="utf-8")

            visual_json = _default_visual_audit_json_path(result.output_zip)
            visual_markdown = visual_json.with_suffix(".md")
            visual_report = build_visual_audit(
                original_zip=request["input_zip"],
                converted_zip=result.output_zip,
            )
            visual_json.write_text(
                json.dumps(visual_report, indent=2), encoding="utf-8"
            )
            visual_summary = visual_report.get("summary", {})
            visual_markdown.write_text(
                "\n".join(
                    [
                        "# Visual Audit",
                        "",
                        "## Summary",
                        "",
                        f"- Files scanned: {visual_summary.get('files_scanned', 0)}",
                        f"- Duplicate title/first-block files: {visual_summary.get('files_with_duplicate_title_first_block', 0)}",
                        f"- Remaining shared template refs: {visual_summary.get('files_with_remaining_shared_template_refs', 0)}",
                        f"- Remaining title tags: {visual_summary.get('files_with_remaining_title_tags', 0)}",
                        f"- Nonstandard divider files: {visual_summary.get('files_with_nonstandard_hr', 0)}",
                        f"- Icon-size anomaly files: {visual_summary.get('files_with_icon_size_anomalies', 0)}",
                        f"- Accordion cards (original): {visual_summary.get('total_original_accordion_cards', 0)}",
                        f"- Details blocks (converted): {visual_summary.get('total_converted_details_blocks', 0)}",
                        f"- MathML expressions (original): {visual_summary.get('total_original_mathml', 0)}",
                        f"- MathML expressions (converted): {visual_summary.get('total_converted_mathml', 0)}",
                        f"- Remaining WIRIS annotations: {visual_summary.get('total_converted_wiris_annotations', 0)}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            math_json = _default_math_audit_json_path(result.output_zip)
            math_markdown = math_json.with_suffix(".md")
            math_report = build_math_audit(
                original_zip=request["input_zip"],
                converted_zip=result.output_zip,
            )
            math_json.write_text(json.dumps(math_report, indent=2), encoding="utf-8")
            math_summary = math_report.get("summary", {})
            math_markdown.write_text(
                "\n".join(
                    [
                        "# Math Audit",
                        "",
                        "## Summary",
                        "",
                        f"- Files with math: {math_summary.get('files_with_math', 0)}",
                        f"- Files with math review flags: {math_summary.get('files_with_math_review_flags', 0)}",
                        f"- Mixed math-mode files: {math_summary.get('files_with_mixed_math_modes', 0)}",
                        f"- Canvas equation images: {math_summary.get('total_converted_equation_images', 0)}",
                        f"- Raw TeX delimiters: {math_summary.get('total_converted_raw_tex_delimiters', 0)}",
                        f"- Empty MathML stubs: {math_summary.get('total_converted_empty_mathml_stubs', 0)}",
                        f"- Absolute equation-image URLs: {math_summary.get('total_absolute_equation_image_urls', 0)}",
                        f"- Equation images missing alt: {math_summary.get('total_equation_images_missing_alt', 0)}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            page_review_json = _default_page_review_json_path(result.output_zip)
            page_review_markdown = page_review_json.with_suffix(".md")
            page_review_html = page_review_json.with_suffix(".html")
            page_review_json, page_review_markdown, page_review_html = (
                build_review_pack(
                    original_zip=request["input_zip"],
                    converted_zip=result.output_zip,
                    migration_report_json=result.report_json,
                    visual_audit_json=visual_json,
                    output_json_path=page_review_json,
                    output_markdown_path=page_review_markdown,
                    output_html_path=page_review_html,
                )
            )
            page_review_report = json.loads(
                page_review_json.read_text(encoding="utf-8")
            )
            page_review_summary = page_review_report.get("summary", {})

            output_dir = result.output_zip.parent
            snapshot_json = self._find_latest_snapshot_json(
                output_dir, self.canvas_course_id_var.get().strip()
            )
            if snapshot_json is not None and not snapshot_json.exists():
                snapshot_json = None
            pre_issues_json = output_dir / "canvas-migration-issues-pre.json"
            if not pre_issues_json.exists():
                pre_issues_json = None
            post_issues_json = output_dir / "canvas-migration-issues-post.json"
            if not post_issues_json.exists():
                fallback_issues = output_dir / "canvas-migration-issues.json"
                post_issues_json = fallback_issues if fallback_issues.exists() else None
            live_audit_json = output_dir / "canvas-live-link-audit.json"
            if not live_audit_json.exists():
                live_audit_json = None
            if not self._artifact_is_current(snapshot_json, result.report_json):
                snapshot_json = None
            if not self._artifact_is_current(pre_issues_json, result.report_json):
                pre_issues_json = None
            if not self._artifact_is_current(post_issues_json, result.report_json):
                post_issues_json = None
            if not self._artifact_is_current(live_audit_json, result.report_json):
                live_audit_json = None
            approval_json = result.report_json.with_name(
                result.report_json.name.replace(
                    ".migration-report.json", ".approval-report.json"
                )
            )
            approval_markdown = approval_json.with_suffix(".md")
            approval_json, approval_markdown = build_approval_report(
                current_course_code=self.sinclair_course_code_var.get().strip()
                or output_dir.name,
                current_source_zip=request["input_zip"],
                current_converted_zip=result.output_zip,
                current_migration_report_json=result.report_json,
                current_visual_audit_json=visual_json,
                current_template_overlay_json=result.template_overlay_report_json,
                current_snapshot_json=snapshot_json,
                pre_issues_json=pre_issues_json,
                post_issues_json=post_issues_json,
                live_audit_json=live_audit_json,
                examples_dir=self._resolve_workspace_root() / "resources" / "examples",
                training_metadata_root=self._resolve_workspace_root()
                / "resources"
                / "training-corpus-v2"
                / "courses",
                output_root=self._resolve_workspace_root() / "output",
                output_json_path=approval_json,
                output_markdown_path=approval_markdown,
            )
            approval_report = json.loads(approval_json.read_text(encoding="utf-8"))
            pattern_json = _default_pattern_report_json_path(result.output_zip)
            pattern_markdown = pattern_json.with_suffix(".md")
            template_package_for_patterns = request["template_package"]
            if template_package_for_patterns is None:
                fallback_template = (
                    Path(self.template_package_var.get().strip())
                    if self.template_package_var.get().strip()
                    else None
                )
                template_package_for_patterns = (
                    fallback_template
                    if fallback_template is not None and fallback_template.exists()
                    else None
                )
            pattern_json, pattern_markdown = build_pattern_report(
                current_course_code=self.sinclair_course_code_var.get().strip()
                or output_dir.name,
                current_source_zip=request["input_zip"],
                current_converted_zip=result.output_zip,
                training_courses_root=self._resolve_workspace_root()
                / "resources"
                / "training-corpus-v2"
                / "courses",
                template_package=template_package_for_patterns,
                best_practices_docx=(
                    Path(self.ref_best_practices_docx_var.get().strip())
                    if self.ref_best_practices_docx_var.get().strip()
                    else None
                ),
                output_json_path=pattern_json,
                output_markdown_path=pattern_markdown,
            )
            pattern_report = json.loads(pattern_json.read_text(encoding="utf-8"))
            payload = {
                "result": result,
                "reference_audit_json": request["reference_audit_json"],
                "safe_summary_path": safe_summary_path,
                "safe_summary": safe_summary,
                "visual_json": visual_json,
                "visual_markdown": visual_markdown,
                "visual_summary": visual_summary,
                "math_json": math_json,
                "math_markdown": math_markdown,
                "math_summary": math_summary,
                "page_review_json": page_review_json,
                "page_review_markdown": page_review_markdown,
                "page_review_html": page_review_html,
                "page_review_summary": page_review_summary,
                "approval_json": approval_json,
                "approval_markdown": approval_markdown,
                "approval_report": approval_report,
                "pattern_json": pattern_json,
                "pattern_markdown": pattern_markdown,
                "pattern_report": pattern_report,
            }
            self.root.after(0, lambda: self._handle_pre_import_pipeline_result(payload))

        self._run_background("Prepare Canvas package", task)

    def _find_manual_review_source(self, folders: list[Path]) -> Path | None:
        for folder in folders:
            if not folder.exists():
                continue
            candidate = self._find_latest_manual_review_csv(folder)
            if candidate is not None:
                return candidate
        return None

    def _fetch_issues_and_build_checklist(
        self,
        *,
        base_url: str,
        course_id: str,
        token: str,
        issues_path: Path,
        selected_migration_id: str,
        force_latest_migration: bool = False,
        reference_audit_json: Path | None,
        manual_review_search_dirs: list[Path],
    ) -> dict:
        migration_id = "" if force_latest_migration else selected_migration_id
        if not migration_id:
            migrations = fetch_content_migrations(
                base_url=base_url,
                course_id=course_id,
                token=token,
            )
            if not migrations:
                raise RuntimeError(
                    "No content migrations found for this Canvas course."
                )
            migration_id = self._pick_latest_migration_id(migrations)
            if not migration_id:
                raise RuntimeError("Canvas returned a migration entry without an ID.")

        issues = fetch_migration_issues(
            base_url=base_url,
            course_id=course_id,
            migration_id=migration_id,
            token=token,
        )
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(json.dumps(issues, indent=2), encoding="utf-8")

        manual_review_csv = self._find_manual_review_source(manual_review_search_dirs)
        checklist_csv, checklist_md = build_fix_checklist(
            canvas_issues_json=issues_path,
            output_dir=issues_path.parent,
            manual_review_csv=(
                manual_review_csv
                if manual_review_csv and manual_review_csv.exists()
                else None
            ),
            reference_audit_json=(
                reference_audit_json
                if reference_audit_json and reference_audit_json.exists()
                else None
            ),
        )
        return {
            "issues_path": issues_path,
            "issues_count": len(issues),
            "migration_id": migration_id,
            "checklist_csv": checklist_csv,
            "checklist_md": checklist_md,
            "manual_review_csv": manual_review_csv,
            "reference_audit_json": reference_audit_json,
        }

    def _run_post_import_pipeline_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        self._remember_sinclair_course_code()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds
        reference_audit_json = self._find_reference_audit_json()

        output_text = self.canvas_issues_output_var.get().strip()
        if self._should_auto_reset_canvas_issues_output(output_text):
            issues_path = self._default_canvas_issues_output_path(course_id)
            self.canvas_issues_output_var.set(str(issues_path))
        else:
            issues_path = Path(output_text)

        selected_migration_id = self.canvas_migration_id_var.get().strip()
        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )
        manual_review_dirs = [issues_path.parent, output_root]

        def task() -> None:
            self.root.after(
                0,
                lambda: self._log(
                    "[Post-Import] Using latest migration ID for issues export."
                ),
            )
            payload = self._fetch_issues_and_build_checklist(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_path=issues_path,
                selected_migration_id=selected_migration_id,
                force_latest_migration=True,
                reference_audit_json=reference_audit_json,
                manual_review_search_dirs=manual_review_dirs,
            )
            self.root.after(
                0, lambda: self._handle_post_import_pipeline_result(payload)
            )

        self._run_background("Run post-import pipeline", task)

    def _handle_post_import_pipeline_result(self, payload: dict) -> None:
        issues_path = payload["issues_path"]
        migration_id = str(payload.get("migration_id", "")).strip()
        issues_count = int(payload.get("issues_count", 0))
        checklist_csv = payload["checklist_csv"]
        checklist_md = payload["checklist_md"]
        manual_review_csv = payload.get("manual_review_csv")
        reference_audit_json = payload.get("reference_audit_json")

        self.canvas_issues_output_var.set(str(issues_path))
        if migration_id:
            self.canvas_migration_id_var.set(migration_id)

        self._log(
            f"Canvas migration issues exported: {issues_count} (migration_id={migration_id})"
        )
        self._log(f"Issues JSON: {issues_path}")
        self._log(f"Fix checklist CSV: {checklist_csv}")
        self._log(f"Fix checklist Markdown: {checklist_md}")
        self._log(
            f"Manual review source: {manual_review_csv if manual_review_csv else 'none'}"
        )
        self._log(
            f"Reference audit source: {reference_audit_json if reference_audit_json else 'none'}"
        )
        self._task_succeeded("Run post-import pipeline")

    def _run_full_post_import_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        self._remember_sinclair_course_code()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        reference_audit_json = self._find_reference_audit_json()
        selected_migration_id = self.canvas_migration_id_var.get().strip()

        output_text = self.canvas_issues_output_var.get().strip()
        if self._should_auto_reset_canvas_issues_output(output_text):
            base_issues_path = self._default_canvas_issues_output_path(course_id)
            self.canvas_issues_output_var.set(str(base_issues_path))
        else:
            base_issues_path = Path(output_text)
        output_dir = base_issues_path.parent

        variant = self.ab_variant_var.get().strip().upper() or "A"
        artifact_prefix = self._ab_artifact_prefix(variant)
        normalized_output_dir = str(output_dir).replace("\\", "/").lower()
        if "/ab-test/" in normalized_output_dir:
            pre_issues_path = (
                output_dir / f"{artifact_prefix}.canvas-migration-issues-pre.json"
            )
            post_issues_path = (
                output_dir / f"{artifact_prefix}.canvas-migration-issues-post.json"
            )
            relink_report_path = (
                output_dir / f"{artifact_prefix}.canvas-auto-relink-report.json"
            )
            live_audit_json = (
                output_dir / f"{artifact_prefix}.canvas-live-link-audit.json"
            )
        else:
            pre_issues_path = output_dir / "canvas-migration-issues-pre.json"
            post_issues_path = output_dir / "canvas-migration-issues-post.json"
            relink_report_path = output_dir / "canvas-auto-relink-report.json"
            live_audit_json = output_dir / "canvas-live-link-audit.json"
        live_audit_md = live_audit_json.with_suffix(".md")
        live_audit_csv = live_audit_json.with_suffix(".csv")

        include_auto_relink = bool(self.ab_include_auto_relink_var.get())
        apply_safe_fixes = bool(self.live_audit_apply_safe_fixes_var.get())
        alias_map_path = self._resolve_alias_map_path(show_warning=True)
        if self.use_template_alias_map_var.get() and alias_map_path is None:
            return

        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )
        manual_review_dirs = [output_dir, output_root]

        update_actions: list[str] = []
        if include_auto_relink:
            update_actions.append("auto-relink missing page/file links")
        if apply_safe_fixes:
            update_actions.append("live-audit safe fixes")
        if update_actions:
            proceed = messagebox.askyesno(
                "Confirm Canvas Updates",
                "Full post-import will update Canvas content via: "
                + ", ".join(update_actions)
                + ".\n\nContinue?",
            )
            if not proceed:
                return

        def task() -> None:
            self.root.after(
                0, lambda: self._log("[Full Post-Import] Exporting pre issues...")
            )
            pre_payload = self._fetch_issues_and_build_checklist(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_path=pre_issues_path,
                selected_migration_id=selected_migration_id,
                force_latest_migration=True,
                reference_audit_json=reference_audit_json,
                manual_review_search_dirs=manual_review_dirs,
            )
            migration_id = str(pre_payload.get("migration_id", "")).strip()

            relink_report = None
            relink_error = ""
            if include_auto_relink:
                try:
                    self.root.after(
                        0,
                        lambda: self._log("[Full Post-Import] Running auto-relink..."),
                    )
                    report_path = auto_relink_missing_links(
                        base_url=base_url,
                        course_id=course_id,
                        token=token,
                        issues_json_path=pre_issues_path,
                        output_json_path=relink_report_path,
                        alias_map_json_path=alias_map_path,
                        dry_run=False,
                    )
                    relink_report = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception as exc:  # pragma: no cover - network/runtime dependent
                    relink_error = str(exc)
                    self.root.after(
                        0,
                        lambda: self._log(
                            f"[WARN] [Full Post-Import] Auto-relink failed; continuing with live audit + post export: {exc}"
                        ),
                    )
            else:
                self.root.after(
                    0,
                    lambda: self._log(
                        "[Full Post-Import] Auto-relink skipped by toggle."
                    ),
                )

            live_report: dict = {}
            live_json_path = live_audit_json
            live_md_path = live_audit_md
            live_csv_path = live_audit_csv
            live_error = ""
            try:
                self.root.after(
                    0,
                    lambda: self._log("[Full Post-Import] Running live link audit..."),
                )
                live_json_path, live_md_path, live_csv_path = run_live_link_audit(
                    base_url=base_url,
                    course_id=course_id,
                    token=token,
                    output_json_path=live_audit_json,
                    output_markdown_path=live_audit_md,
                    output_csv_path=live_audit_csv,
                    apply_safe_fixes=apply_safe_fixes,
                    alias_map_json_path=alias_map_path,
                )
                live_report = json.loads(live_json_path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                live_error = str(exc)
                self.root.after(
                    0,
                    lambda: self._log(
                        f"[WARN] [Full Post-Import] Live audit failed; continuing with post export: {exc}"
                    ),
                )

            self.root.after(
                0, lambda: self._log("[Full Post-Import] Exporting post issues...")
            )
            post_payload = self._fetch_issues_and_build_checklist(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_path=post_issues_path,
                selected_migration_id=migration_id,
                force_latest_migration=False,
                reference_audit_json=reference_audit_json,
                manual_review_search_dirs=manual_review_dirs,
            )
            canonical_issues_path: Path | None = None
            post_issues_value = post_payload.get("issues_path")
            if isinstance(post_issues_value, Path):
                normalized_post_parent = (
                    str(post_issues_value.parent).replace("\\", "/").lower()
                )
                if (
                    "/ab-test/" not in normalized_post_parent
                    and post_issues_value.name == "canvas-migration-issues-post.json"
                ):
                    canonical_issues_path = (
                        post_issues_value.parent / "canvas-migration-issues.json"
                    )
                    canonical_issues_path.write_text(
                        post_issues_value.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )

            payload = {
                "pre": pre_payload,
                "post": post_payload,
                "relink_report": relink_report,
                "relink_report_path": (
                    relink_report_path if include_auto_relink else None
                ),
                "relink_error": relink_error,
                "live_audit_json_path": live_json_path,
                "live_audit_md_path": live_md_path,
                "live_audit_csv_path": live_csv_path,
                "live_audit_report": live_report,
                "live_audit_error": live_error,
                "canonical_issues_path": canonical_issues_path,
            }
            self.root.after(0, lambda: self._handle_full_post_import_result(payload))

        self._run_background("Run full post-import pipeline", task)

    def _handle_full_post_import_result(self, payload: dict) -> None:
        pre = payload.get("pre", {})
        post = payload.get("post", {})
        relink_report = payload.get("relink_report")
        relink_report_path = payload.get("relink_report_path")
        relink_error = str(payload.get("relink_error", "")).strip()
        live_audit_report = payload.get("live_audit_report", {})
        live_audit_json_path = payload.get("live_audit_json_path")
        live_audit_md_path = payload.get("live_audit_md_path")
        live_audit_csv_path = payload.get("live_audit_csv_path")
        live_audit_error = str(payload.get("live_audit_error", "")).strip()
        canonical_issues_path = payload.get("canonical_issues_path")

        pre_count = int(pre.get("issues_count", 0))
        post_count = int(post.get("issues_count", 0))
        migration_id = str(
            post.get("migration_id", pre.get("migration_id", ""))
        ).strip()
        if migration_id:
            self.canvas_migration_id_var.set(migration_id)

        post_issues_path = post.get("issues_path")
        if isinstance(canonical_issues_path, Path):
            self.canvas_issues_output_var.set(str(canonical_issues_path))
        elif isinstance(post_issues_path, Path):
            self.canvas_issues_output_var.set(str(post_issues_path))

        self._log("Full post-import complete.")
        self._log(f"Pre issues JSON: {pre.get('issues_path')}")
        self._log(f"Post issues JSON: {post.get('issues_path')}")
        if isinstance(canonical_issues_path, Path):
            self._log(f"Canonical issues JSON: {canonical_issues_path}")
        self._log(f"Issue count delta: {pre_count} -> {post_count}")
        if relink_report_path:
            self._log(f"Auto-relink report JSON: {relink_report_path}")
        if isinstance(relink_report, dict):
            summary = relink_report.get("summary", {})
            self._log(
                "Auto-relink summary: "
                f"pages_updated={summary.get('pages_updated', 0)} | "
                f"total_rewrites={summary.get('total_rewrites', 0)} | "
                f"alias_rewrites={summary.get('total_alias_rewrites', 0)} | "
                f"unresolved_local_refs={summary.get('total_unresolved_local_refs', 0)}"
            )
        if relink_error:
            self._log(f"[WARN] Auto-relink error: {relink_error}")

        self._log(f"Live audit JSON: {live_audit_json_path}")
        self._log(f"Live audit Markdown: {live_audit_md_path}")
        self._log(f"Live audit CSV: {live_audit_csv_path}")
        if isinstance(live_audit_report, dict):
            counts = live_audit_report.get("counts", {})
            safe_fix = live_audit_report.get("safe_fix_summary", {})
            self._log(
                "Live audit summary: "
                f"findings={counts.get('findings_total', 0)} | "
                f"pages={counts.get('pages', 0)} | "
                f"assignments={counts.get('assignments', 0)} | "
                f"discussions={counts.get('discussions', 0)} | "
                f"announcements={counts.get('announcements', 0)}"
            )
            self._log(
                "Live audit fixes: "
                f"pages_updated={safe_fix.get('pages_updated', 0)} | "
                f"rewrites={safe_fix.get('total_rewrites', 0)} | "
                f"alias_rewrites={safe_fix.get('total_alias_rewrites', 0)} | "
                f"unresolved_local_refs={safe_fix.get('total_unresolved_local_refs', 0)}"
            )
        if live_audit_error:
            self._log(f"[WARN] Live audit error: {live_audit_error}")

        self._log(f"Fix checklist CSV: {post.get('checklist_csv')}")
        self._log(f"Fix checklist Markdown: {post.get('checklist_md')}")
        self._task_succeeded("Run full post-import pipeline")

    def _run_ab_variant_cycle_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        self._remember_sinclair_course_code()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds
        variant = self.ab_variant_var.get().strip().upper() or "A"
        ab_dir = self._resolve_ab_variant_dir(variant)
        artifact_prefix = self._ab_artifact_prefix(variant)
        pre_issues_path = ab_dir / f"{artifact_prefix}.canvas-migration-issues-pre.json"
        post_issues_path = (
            ab_dir / f"{artifact_prefix}.canvas-migration-issues-post.json"
        )
        relink_report_path = (
            ab_dir / f"{artifact_prefix}.canvas-auto-relink-report.json"
        )
        include_auto_relink = bool(self.ab_include_auto_relink_var.get())
        alias_map_path = self._resolve_alias_map_path(show_warning=True)
        if self.use_template_alias_map_var.get() and alias_map_path is None:
            return

        selected_migration_id = self.canvas_migration_id_var.get().strip()
        reference_audit_json = self._find_reference_audit_json()
        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )
        manual_review_dirs = [ab_dir, output_root]

        def task() -> None:
            self.root.after(
                0,
                lambda: self._log(
                    f"[A/B {variant}] Using latest migration ID for pre-export."
                ),
            )
            self.root.after(
                0, lambda: self._log(f"[A/B {variant}] Exporting pre issues...")
            )
            pre_payload = self._fetch_issues_and_build_checklist(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_path=pre_issues_path,
                selected_migration_id=selected_migration_id,
                force_latest_migration=True,
                reference_audit_json=reference_audit_json,
                manual_review_search_dirs=manual_review_dirs,
            )

            relink_report = None
            if include_auto_relink:
                self.root.after(
                    0, lambda: self._log(f"[A/B {variant}] Running auto-relink...")
                )
                report_json_path = auto_relink_missing_links(
                    base_url=base_url,
                    course_id=course_id,
                    token=token,
                    issues_json_path=pre_issues_path,
                    output_json_path=relink_report_path,
                    alias_map_json_path=alias_map_path,
                    dry_run=False,
                )
                relink_report = json.loads(report_json_path.read_text(encoding="utf-8"))
            else:
                self.root.after(
                    0,
                    lambda: self._log(
                        f"[A/B {variant}] Auto-relink skipped by toggle."
                    ),
                )

            self.root.after(
                0, lambda: self._log(f"[A/B {variant}] Exporting post issues...")
            )
            post_payload = self._fetch_issues_and_build_checklist(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_path=post_issues_path,
                selected_migration_id=str(pre_payload.get("migration_id", "")).strip(),
                reference_audit_json=reference_audit_json,
                manual_review_search_dirs=manual_review_dirs,
            )

            payload = {
                "variant": variant,
                "pre": pre_payload,
                "post": post_payload,
                "relink_report": relink_report,
                "relink_report_path": (
                    relink_report_path if include_auto_relink else None
                ),
            }
            self.root.after(0, lambda: self._handle_ab_variant_cycle_result(payload))

        self._run_background(f"Run A/B variant cycle ({variant})", task)

    def _handle_ab_variant_cycle_result(self, payload: dict) -> None:
        variant = str(payload.get("variant", "")).strip().upper() or "A"
        pre = payload.get("pre", {})
        post = payload.get("post", {})
        relink_report = payload.get("relink_report")
        relink_report_path = payload.get("relink_report_path")

        pre_count = int(pre.get("issues_count", 0))
        post_count = int(post.get("issues_count", 0))
        migration_id = str(
            post.get("migration_id", pre.get("migration_id", ""))
        ).strip()
        if migration_id:
            self.canvas_migration_id_var.set(migration_id)

        post_issues_path = post.get("issues_path")
        if isinstance(post_issues_path, Path):
            self.canvas_issues_output_var.set(str(post_issues_path))

        self._log(f"A/B variant {variant} complete.")
        self._log(f"Pre issues JSON: {pre.get('issues_path')}")
        self._log(f"Post issues JSON: {post.get('issues_path')}")
        self._log(f"Issue count delta: {pre_count} -> {post_count}")
        if relink_report_path:
            self._log(f"Auto-relink report JSON: {relink_report_path}")
        if isinstance(relink_report, dict):
            summary = relink_report.get("summary", {})
            self._log(
                "Auto-relink summary: "
                f"pages_updated={summary.get('pages_updated', 0)} | "
                f"total_rewrites={summary.get('total_rewrites', 0)} | "
                f"alias_rewrites={summary.get('total_alias_rewrites', 0)} | "
                f"unresolved_local_refs={summary.get('total_unresolved_local_refs', 0)}"
            )
        self._task_succeeded(f"Run A/B variant cycle ({variant})")

    def _apply_migration_result(
        self,
        *,
        result: MigrationOutput,
        safe_summary_path: Path,
        safe_summary: str,
        reference_audit_json: Path | None,
    ) -> None:
        self.report_json_var.set(str(result.report_json))
        self.safe_summary_path_var.set(str(safe_summary_path))
        self.visual_converted_zip_var.set(str(result.output_zip))
        self.canvas_upload_zip_var.set(str(result.output_zip))
        # Auto-fill the upload-tab template zip from the convert-tab setting
        # (so the user doesn't have to re-enter it in the Upload tab).
        tmpl_pkg = self.template_package_var.get().strip()
        if tmpl_pkg and not self.canvas_upload_template_zip_var.get().strip():
            self.canvas_upload_template_zip_var.set(tmpl_pkg)
        self.visual_audit_output_var.set(
            str(_default_visual_audit_json_path(result.output_zip))
        )
        self.math_audit_output_var.set(
            str(_default_math_audit_json_path(result.output_zip))
        )
        self.review_draft_json_var.set(
            str(_default_review_draft_json_path(result.output_zip))
        )
        self.reviewed_zip_output_var.set(
            str(_default_reviewed_zip_path(result.output_zip))
        )
        self.pattern_report_output_var.set(
            str(_default_pattern_report_json_path(result.output_zip))
        )
        self.latest_safe_summary = safe_summary

        self._log(f"Canvas-ready zip: {result.output_zip}")
        self._log(f"Migration report JSON: {result.report_json}")
        self._log(f"Migration report Markdown: {result.report_markdown}")
        self._log(f"Manual review CSV: {result.manual_review_csv}")
        self._log(f"Preflight checklist: {result.preflight_checklist}")
        if result.template_overlay_report_json is not None:
            self._log(
                f"Template overlay report JSON: {result.template_overlay_report_json}"
            )
        self._log(f"Policy profile used: {result.policy_profile_id}")
        self._log(f"Math handling: {self.math_handling_var.get()}")
        self._log(f"Accordion handling: {self.accordion_handling_var.get()}")
        self._log(f"Accordion title align: {self.accordion_alignment_var.get()}")
        self._log(f"Image layout mode: {self.image_layout_mode_var.get()}")
        self._log(
            f"Template module structure: {self.template_module_structure_var.get()}"
        )
        self._log(
            f"Template visual standards: {self.template_visual_standards_var.get()}"
        )
        self._log(
            f"Template color standards: {self.template_color_standards_var.get()}"
        )
        self._log(
            f"Template divider standards: {self.template_divider_standards_var.get()}"
        )
        self._log(
            f"Best-practice enforcer: {self.enable_best_practice_enforcer_var.get()}"
        )
        self._log(
            f"Reference alignment input: {reference_audit_json if reference_audit_json else 'none'}"
        )
        self._log(f"Safe summary: {safe_summary_path}")
        self._log("")
        self._log(safe_summary.strip())

    def _handle_migration_result(
        self,
        result: MigrationOutput,
        reference_audit_json: Path | None = None,
    ) -> None:
        safe_summary_path = _default_safe_summary_path(result.report_json)
        safe_summary = build_safe_summary_from_path(result.report_json)
        safe_summary_path.write_text(safe_summary, encoding="utf-8")
        self._apply_migration_result(
            result=result,
            safe_summary_path=safe_summary_path,
            safe_summary=safe_summary,
            reference_audit_json=reference_audit_json,
        )
        self._task_succeeded("Run local migration")

    def _handle_pre_import_pipeline_result(self, payload: dict) -> None:
        result = payload["result"]
        safe_summary_path = payload["safe_summary_path"]
        safe_summary = payload["safe_summary"]
        reference_audit_json = payload.get("reference_audit_json")
        visual_json = payload["visual_json"]
        visual_markdown = payload["visual_markdown"]
        visual_summary = payload["visual_summary"]
        math_json = payload["math_json"]
        math_markdown = payload["math_markdown"]
        math_summary = payload["math_summary"]
        page_review_json = payload["page_review_json"]
        page_review_markdown = payload["page_review_markdown"]
        page_review_html = payload["page_review_html"]
        page_review_summary = payload["page_review_summary"]
        approval_json = payload["approval_json"]
        approval_markdown = payload["approval_markdown"]
        approval_report = payload["approval_report"]
        pattern_json = payload["pattern_json"]
        pattern_markdown = payload["pattern_markdown"]
        pattern_report = payload["pattern_report"]

        self._apply_migration_result(
            result=result,
            safe_summary_path=safe_summary_path,
            safe_summary=safe_summary,
            reference_audit_json=reference_audit_json,
        )
        self.visual_audit_output_var.set(str(visual_json))
        self.math_audit_output_var.set(str(math_json))
        self._log(f"Visual audit JSON: {visual_json}")
        self._log(f"Visual audit Markdown: {visual_markdown}")
        self._log(
            "Visual audit summary: "
            f"files={visual_summary.get('files_scanned', 0)} | "
            f"dup_title={visual_summary.get('files_with_duplicate_title_first_block', 0)} | "
            f"shared_refs={visual_summary.get('files_with_remaining_shared_template_refs', 0)} | "
            f"title_tags={visual_summary.get('files_with_remaining_title_tags', 0)}"
        )
        self._log(f"Math audit JSON: {math_json}")
        self._log(f"Math audit Markdown: {math_markdown}")
        self._log(
            "Math audit summary: "
            f"files_with_math={math_summary.get('files_with_math', 0)} | "
            f"review_flags={math_summary.get('files_with_math_review_flags', 0)} | "
            f"equation_images={math_summary.get('total_converted_equation_images', 0)} | "
            f"raw_tex={math_summary.get('total_converted_raw_tex_delimiters', 0)} | "
            f"empty_mathml={math_summary.get('total_converted_empty_mathml_stubs', 0)}"
        )
        self._log(f"Page review JSON: {page_review_json}")
        self._log(f"Page review Markdown: {page_review_markdown}")
        self._log(f"Page review HTML workbench: {page_review_html}")
        self._log(
            "Page review summary: "
            f"high={page_review_summary.get('files_with_high_priority_review', 0)} | "
            f"medium={page_review_summary.get('files_with_medium_priority_review', 0)} | "
            f"manual_pages={page_review_summary.get('files_with_manual_issues', 0)} | "
            f"a11y_pages={page_review_summary.get('files_with_accessibility_issues', 0)}"
        )
        approval_summary = (
            approval_report.get("summary", {})
            if isinstance(approval_report, dict)
            else {}
        )
        self._log(f"Approval report JSON: {approval_json}")
        self._log(f"Approval report Markdown: {approval_markdown}")
        self._log(
            "Approval summary: "
            f"status={approval_summary.get('overall_status', 'unknown')} | "
            f"score={approval_summary.get('approval_score', 0)} | "
            f"cohort={approval_summary.get('reference_cohort_label', 'unknown')}"
        )
        pattern_summary = (
            pattern_report.get("summary", {})
            if isinstance(pattern_report, dict)
            else {}
        )
        self.pattern_report_output_var.set(str(pattern_json))
        self._log(f"Pattern report JSON: {pattern_json}")
        self._log(f"Pattern report Markdown: {pattern_markdown}")
        self._log(
            "Pattern summary: "
            f"training_pairs={pattern_summary.get('training_course_pairs', 0)} | "
            f"consensus={pattern_summary.get('consensus_transforms', 0)} | "
            f"current_matches={pattern_summary.get('current_matching_transforms', 0)} | "
            f"current_missing={pattern_summary.get('current_missing_transforms', 0)}"
        )
        self.page_review_html_var.set(str(page_review_html))
        if self.auto_open_page_review_var.get() and page_review_html.exists():
            self.root.after(400, lambda p=page_review_html: webbrowser.open(p.as_uri()))
        self.root.after(200, lambda: self.notebook.select(1))
        self._task_succeeded("Prepare Canvas package")

    def _open_page_review_in_browser(self) -> None:
        """Open the current page review HTML workbench in the system browser."""
        html_path_str = self.page_review_html_var.get().strip()
        if not html_path_str:
            # Fall back to searching the output directory
            output_dir_text = self.output_dir_var.get().strip()
            output_dir = (
                Path(output_dir_text)
                if output_dir_text
                else (self._resolve_workspace_root() / "output")
            )
            html_path = self._find_latest_matching_file(
                output_dir, "*.page-review.html"
            )
        else:
            html_path = Path(html_path_str)
        if html_path and html_path.exists():
            webbrowser.open(html_path.as_uri())
        else:
            messagebox.showinfo(
                "No page review",
                'No page review workbench found. Run "Prepare Canvas Package" first.',
            )

    def _generate_safe_summary_clicked(self) -> None:
        report_path = Path(self.report_json_var.get().strip())
        output_text = self.safe_summary_path_var.get().strip()

        if not report_path.exists():
            messagebox.showwarning(
                "Missing report", "Select a valid migration report JSON file."
            )
            return

        output_path = (
            Path(output_text)
            if output_text
            else _default_safe_summary_path(report_path)
        )

        def task() -> None:
            safe_summary = build_safe_summary_from_path(report_path)
            output_path.write_text(safe_summary, encoding="utf-8")
            self.root.after(
                0, lambda: self._handle_safe_summary_result(output_path, safe_summary)
            )

        self._run_background("Generate non-sensitive summary", task)

    def _handle_safe_summary_result(self, output_path: Path, safe_summary: str) -> None:
        self.safe_summary_path_var.set(str(output_path))
        self.latest_safe_summary = safe_summary
        self._log(f"Safe summary written: {output_path}")
        self._log("")
        self._log(safe_summary.strip())
        self._task_succeeded("Generate non-sensitive summary")

    def _copy_summary_clicked(self) -> None:
        if not self.latest_safe_summary:
            messagebox.showinfo("No summary", "Generate a safe summary first.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.latest_safe_summary)
        self._log("Safe summary copied to clipboard.")

    def _run_best_practices_audit_clicked(self) -> None:
        input_path = Path(self.best_practices_file_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip())
        sheet = self.best_practices_sheet_var.get().strip() or None

        if not input_path.exists():
            messagebox.showwarning(
                "Missing file", "Select a valid best-practices spreadsheet file."
            )
            return

        def task() -> None:
            json_path, md_path = run_audit(
                input_path=input_path, output_dir=output_dir, sheet_name=sheet
            )
            self.root.after(0, lambda: self._handle_audit_result(json_path, md_path))

        self._run_background("Run best-practices audit", task)

    def _handle_audit_result(self, json_path: Path, md_path: Path) -> None:
        self._log(f"Best-practices audit JSON: {json_path}")
        self._log(f"Best-practices audit Markdown: {md_path}")
        self._task_succeeded("Run best-practices audit")

    def _run_reference_audit_clicked(self) -> None:
        instructions_docx = Path(self.ref_instructions_docx_var.get().strip())
        best_practices_docx = Path(self.ref_best_practices_docx_var.get().strip())
        setup_checklist_text = self.ref_setup_checklist_docx_var.get().strip()
        setup_checklist_docx = (
            Path(setup_checklist_text) if setup_checklist_text else None
        )
        page_templates_docx = Path(self.ref_page_templates_docx_var.get().strip())
        syllabus_template_docx = Path(self.ref_syllabus_template_docx_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip()) / "reference_audit"
        workspace_root = self._resolve_workspace_root()
        draft_markdown = (
            workspace_root / "docs" / "lms-migration-custom-instructions-draft.md"
        )
        rules_json = workspace_root / "rules" / "sinclair_pilot_rules.json"
        findings_markdown = (
            workspace_root / "docs" / "pdf-best-practices-initial-findings.md"
        )

        required = (
            instructions_docx,
            best_practices_docx,
            page_templates_docx,
            syllabus_template_docx,
            draft_markdown,
            rules_json,
            findings_markdown,
        )
        optional = tuple(path for path in (setup_checklist_docx,) if path is not None)
        missing = [str(path) for path in (*required, *optional) if not path.exists()]
        if missing:
            messagebox.showwarning(
                "Missing file(s)",
                "The following required files are missing:\n\n" + "\n".join(missing),
            )
            return

        def task() -> None:
            json_path, md_path = run_reference_audit(
                instructions_docx=instructions_docx,
                draft_markdown=draft_markdown,
                best_practices_docx=best_practices_docx,
                setup_checklist_docx=setup_checklist_docx,
                page_templates_docx=page_templates_docx,
                syllabus_template_docx=syllabus_template_docx,
                rules_json=rules_json,
                findings_markdown=findings_markdown,
                output_dir=output_dir,
            )
            self.root.after(
                0, lambda: self._handle_reference_audit_result(json_path, md_path)
            )

        self._run_background("Run reference docs audit", task)

    def _handle_reference_audit_result(self, json_path: Path, md_path: Path) -> None:
        self._log(f"Reference audit JSON: {json_path}")
        self._log(f"Reference audit Markdown: {md_path}")
        self._task_succeeded("Run reference docs audit")

    def _run_visual_audit_clicked(self) -> None:
        original_zip = Path(self.visual_original_zip_var.get().strip())
        converted_zip = Path(self.visual_converted_zip_var.get().strip())
        output_json_text = self.visual_audit_output_var.get().strip()

        if not original_zip.exists():
            messagebox.showwarning(
                "Missing original ZIP", "Select a valid original D2L export ZIP."
            )
            return
        if not converted_zip.exists():
            messagebox.showwarning(
                "Missing converted ZIP", "Select a valid converted canvas-ready ZIP."
            )
            return

        output_json = (
            Path(output_json_text)
            if output_json_text
            else _default_visual_audit_json_path(converted_zip)
        )
        output_markdown = output_json.with_suffix(".md")

        def task() -> None:
            report = build_visual_audit(
                original_zip=original_zip,
                converted_zip=converted_zip,
            )
            output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
            summary = report.get("summary", {})
            markdown_lines = [
                "# Visual Audit",
                "",
                "## Summary",
                "",
                f"- Files scanned: {summary.get('files_scanned', 0)}",
                f"- Duplicate title/first-block files: {summary.get('files_with_duplicate_title_first_block', 0)}",
                f"- Remaining shared template refs: {summary.get('files_with_remaining_shared_template_refs', 0)}",
                f"- Remaining title tags: {summary.get('files_with_remaining_title_tags', 0)}",
                f"- Nonstandard divider files: {summary.get('files_with_nonstandard_hr', 0)}",
                f"- Icon-size anomaly files: {summary.get('files_with_icon_size_anomalies', 0)}",
                f"- Accordion cards (original): {summary.get('total_original_accordion_cards', 0)}",
                f"- Details blocks (converted): {summary.get('total_converted_details_blocks', 0)}",
                f"- MathML expressions (original): {summary.get('total_original_mathml', 0)}",
                f"- MathML expressions (converted): {summary.get('total_converted_mathml', 0)}",
                f"- Remaining WIRIS annotations: {summary.get('total_converted_wiris_annotations', 0)}",
                "",
            ]
            output_markdown.write_text("\n".join(markdown_lines), encoding="utf-8")
            self.root.after(
                0,
                lambda: self._handle_visual_audit_result(
                    output_json, output_markdown, summary
                ),
            )

        self._run_background("Run visual HTML audit", task)

    def _handle_visual_audit_result(
        self, json_path: Path, md_path: Path, summary: dict
    ) -> None:
        self.visual_audit_output_var.set(str(json_path))
        self._log(f"Visual audit JSON: {json_path}")
        self._log(f"Visual audit Markdown: {md_path}")
        self._log(
            "Visual audit summary: "
            f"files={summary.get('files_scanned', 0)} | "
            f"dup_title={summary.get('files_with_duplicate_title_first_block', 0)} | "
            f"shared_refs={summary.get('files_with_remaining_shared_template_refs', 0)} | "
            f"title_tags={summary.get('files_with_remaining_title_tags', 0)} | "
            f"accordion_cards={summary.get('total_original_accordion_cards', 0)} | "
            f"details_blocks={summary.get('total_converted_details_blocks', 0)}"
        )
        self._task_succeeded("Run visual HTML audit")

    def _run_math_audit_clicked(self) -> None:
        original_zip = Path(self.visual_original_zip_var.get().strip())
        converted_zip = Path(self.visual_converted_zip_var.get().strip())
        output_json_text = self.math_audit_output_var.get().strip()

        if not original_zip.exists():
            messagebox.showwarning(
                "Missing original ZIP", "Select a valid original D2L export ZIP."
            )
            return
        if not converted_zip.exists():
            messagebox.showwarning(
                "Missing converted ZIP", "Select a valid converted canvas-ready ZIP."
            )
            return

        output_json = (
            Path(output_json_text)
            if output_json_text
            else _default_math_audit_json_path(converted_zip)
        )
        output_markdown = output_json.with_suffix(".md")

        def task() -> None:
            report = build_math_audit(
                original_zip=original_zip,
                converted_zip=converted_zip,
            )
            output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
            summary = report.get("summary", {})
            markdown_lines = [
                "# Math Audit",
                "",
                "## Summary",
                "",
                f"- Files with math: {summary.get('files_with_math', 0)}",
                f"- Files with math review flags: {summary.get('files_with_math_review_flags', 0)}",
                f"- Mixed math-mode files: {summary.get('files_with_mixed_math_modes', 0)}",
                f"- Canvas equation images: {summary.get('total_converted_equation_images', 0)}",
                f"- Raw TeX delimiters: {summary.get('total_converted_raw_tex_delimiters', 0)}",
                f"- Empty MathML stubs: {summary.get('total_converted_empty_mathml_stubs', 0)}",
                f"- Absolute equation-image URLs: {summary.get('total_absolute_equation_image_urls', 0)}",
                f"- Equation images missing alt: {summary.get('total_equation_images_missing_alt', 0)}",
                "",
            ]
            output_markdown.write_text("\n".join(markdown_lines), encoding="utf-8")
            self.root.after(
                0,
                lambda: self._handle_math_audit_result(
                    output_json, output_markdown, summary
                ),
            )

        self._run_background("Run math audit", task)

    def _handle_math_audit_result(
        self, json_path: Path, md_path: Path, summary: dict
    ) -> None:
        self.math_audit_output_var.set(str(json_path))
        self._log(f"Math audit JSON: {json_path}")
        self._log(f"Math audit Markdown: {md_path}")
        self._log(
            "Math audit summary: "
            f"files_with_math={summary.get('files_with_math', 0)} | "
            f"review_flags={summary.get('files_with_math_review_flags', 0)} | "
            f"equation_images={summary.get('total_converted_equation_images', 0)} | "
            f"raw_tex={summary.get('total_converted_raw_tex_delimiters', 0)} | "
            f"empty_mathml={summary.get('total_converted_empty_mathml_stubs', 0)}"
        )
        self._task_succeeded("Run math audit")

    def _build_page_review_clicked(self) -> None:
        original_zip = Path(self.visual_original_zip_var.get().strip())
        converted_zip = Path(self.visual_converted_zip_var.get().strip())

        if not original_zip.exists():
            messagebox.showwarning(
                "Missing original ZIP", "Select a valid original D2L export ZIP."
            )
            return
        if not converted_zip.exists():
            messagebox.showwarning(
                "Missing converted ZIP", "Select a valid converted canvas-ready ZIP."
            )
            return

        migration_report_json = (
            Path(self.report_json_var.get().strip())
            if self.report_json_var.get().strip()
            else None
        )
        if migration_report_json is not None and not migration_report_json.exists():
            migration_report_json = None
        if migration_report_json is None:
            migration_report_json = self._find_latest_matching_file(
                converted_zip.parent, "*.migration-report.json"
            )

        visual_audit_json = (
            Path(self.visual_audit_output_var.get().strip())
            if self.visual_audit_output_var.get().strip()
            else None
        )
        if visual_audit_json is not None and not visual_audit_json.exists():
            visual_audit_json = None

        output_json = _default_page_review_json_path(converted_zip)
        output_markdown = output_json.with_suffix(".md")
        output_html = output_json.with_suffix(".html")

        def task() -> None:
            json_path, md_path, html_path = build_review_pack(
                original_zip=original_zip,
                converted_zip=converted_zip,
                migration_report_json=migration_report_json,
                visual_audit_json=visual_audit_json,
                output_json_path=output_json,
                output_markdown_path=output_markdown,
                output_html_path=output_html,
            )
            report = json.loads(json_path.read_text(encoding="utf-8"))
            summary = report.get("summary", {})
            self.root.after(
                0,
                lambda: self._handle_page_review_result(
                    json_path=json_path,
                    md_path=md_path,
                    html_path=html_path,
                    summary=summary,
                ),
            )

        self._run_background("Build page review workbench", task)

    def _handle_page_review_result(
        self,
        *,
        json_path: Path,
        md_path: Path,
        html_path: Path,
        summary: dict,
    ) -> None:
        self._log(f"Page review JSON: {json_path}")
        self._log(f"Page review Markdown: {md_path}")
        self._log(f"Page review HTML workbench: {html_path}")
        converted_zip = (
            Path(self.visual_converted_zip_var.get().strip())
            if self.visual_converted_zip_var.get().strip()
            else None
        )
        if converted_zip is not None and converted_zip.exists():
            self.review_draft_json_var.set(
                str(_default_review_draft_json_path(converted_zip))
            )
            self.reviewed_zip_output_var.set(
                str(_default_reviewed_zip_path(converted_zip))
            )
        self._log(
            "Page review summary: "
            f"high={summary.get('files_with_high_priority_review', 0)} | "
            f"medium={summary.get('files_with_medium_priority_review', 0)} | "
            f"manual_pages={summary.get('files_with_manual_issues', 0)} | "
            f"a11y_pages={summary.get('files_with_accessibility_issues', 0)}"
        )
        self._task_succeeded("Build page review workbench")

    def _apply_review_draft_clicked(self) -> None:
        draft_json = Path(self.review_draft_json_var.get().strip())
        converted_zip = Path(self.visual_converted_zip_var.get().strip())
        original_zip_text = self.visual_original_zip_var.get().strip()
        original_zip = Path(original_zip_text) if original_zip_text else None

        if not draft_json.exists():
            messagebox.showwarning(
                "Missing review draft",
                "Select a valid review draft JSON exported from the page review workbench.",
            )
            return
        if not converted_zip.exists():
            messagebox.showwarning(
                "Missing converted ZIP", "Select a valid converted canvas-ready ZIP."
            )
            return

        output_zip_text = self.reviewed_zip_output_var.get().strip()
        output_zip = (
            Path(output_zip_text)
            if output_zip_text
            else _default_reviewed_zip_path(converted_zip)
        )
        rules_path = (
            Path(self.rules_var.get().strip()) if self.rules_var.get().strip() else None
        )
        if rules_path is not None and not rules_path.exists():
            rules_path = None

        migration_report_json = (
            Path(self.report_json_var.get().strip())
            if self.report_json_var.get().strip()
            else None
        )
        if migration_report_json is not None and not migration_report_json.exists():
            migration_report_json = None

        def task() -> None:
            result = apply_review_draft(
                draft_json=draft_json,
                converted_zip=converted_zip,
                rules_path=rules_path,
                policy_profile_id=self.policy_profile_var.get().strip(),
                policy_profiles_path=self.policy_profiles_path,
                math_handling=self.math_handling_var.get().strip().lower()
                or "preserve-semantic",
                accordion_handling=self.accordion_handling_var.get().strip().lower()
                or "smart",
                accordion_alignment=self.accordion_alignment_var.get().strip().lower()
                or "left",
                accordion_flatten_hints=self._split_hint_tokens(
                    self.accordion_flatten_hints_var.get()
                ),
                accordion_details_hints=self._split_hint_tokens(
                    self.accordion_details_hints_var.get()
                ),
                apply_template_divider_standards=bool(
                    self.template_divider_standards_var.get()
                ),
                best_practice_enforcer=bool(
                    self.enable_best_practice_enforcer_var.get()
                ),
                output_zip_path=output_zip,
            )

            visual_json = None
            visual_markdown = None
            visual_summary = {}
            math_json = None
            math_markdown = None
            math_summary = {}
            page_review_json = None
            page_review_markdown = None
            page_review_html = None
            page_review_summary = {}
            pattern_json = None
            pattern_markdown = None
            pattern_report = {}

            if original_zip is not None and original_zip.exists():
                visual_json = _default_visual_audit_json_path(result.output_zip)
                visual_markdown = visual_json.with_suffix(".md")
                visual_report = build_visual_audit(
                    original_zip=original_zip,
                    converted_zip=result.output_zip,
                )
                visual_summary = visual_report.get("summary", {})
                visual_json.write_text(
                    json.dumps(visual_report, indent=2), encoding="utf-8"
                )
                visual_markdown.write_text(
                    "\n".join(
                        [
                            "# Visual Audit",
                            "",
                            "## Summary",
                            "",
                            f"- Files scanned: {visual_summary.get('files_scanned', 0)}",
                            f"- Shared template refs: {visual_summary.get('files_with_remaining_shared_template_refs', 0)}",
                            f"- Nonstandard divider files: {visual_summary.get('files_with_nonstandard_hr', 0)}",
                            f"- Icon-size anomaly files: {visual_summary.get('files_with_icon_size_anomalies', 0)}",
                        ]
                    ),
                    encoding="utf-8",
                )
                math_json = _default_math_audit_json_path(result.output_zip)
                math_markdown = math_json.with_suffix(".md")
                math_report = build_math_audit(
                    original_zip=original_zip,
                    converted_zip=result.output_zip,
                )
                math_summary = math_report.get("summary", {})
                math_json.write_text(
                    json.dumps(math_report, indent=2), encoding="utf-8"
                )
                math_markdown.write_text(
                    "\n".join(
                        [
                            "# Math Audit",
                            "",
                            "## Summary",
                            "",
                            f"- Files with math: {math_summary.get('files_with_math', 0)}",
                            f"- Files with math review flags: {math_summary.get('files_with_math_review_flags', 0)}",
                            f"- Canvas equation images: {math_summary.get('total_converted_equation_images', 0)}",
                            f"- Raw TeX delimiters: {math_summary.get('total_converted_raw_tex_delimiters', 0)}",
                        ]
                    ),
                    encoding="utf-8",
                )
                page_review_json = _default_page_review_json_path(result.output_zip)
                page_review_markdown = page_review_json.with_suffix(".md")
                page_review_html = page_review_json.with_suffix(".html")
                page_review_json, page_review_markdown, page_review_html = (
                    build_review_pack(
                        original_zip=original_zip,
                        converted_zip=result.output_zip,
                        migration_report_json=migration_report_json,
                        visual_audit_json=visual_json,
                        output_json_path=page_review_json,
                        output_markdown_path=page_review_markdown,
                        output_html_path=page_review_html,
                    )
                )
                page_review_report = json.loads(
                    page_review_json.read_text(encoding="utf-8")
                )
                page_review_summary = page_review_report.get("summary", {})

                pattern_json = _default_pattern_report_json_path(result.output_zip)
                pattern_markdown = pattern_json.with_suffix(".md")
                template_package_path = (
                    Path(self.template_package_var.get().strip())
                    if self.template_package_var.get().strip()
                    else None
                )
                if (
                    template_package_path is not None
                    and not template_package_path.exists()
                ):
                    template_package_path = None
                pattern_json, pattern_markdown = build_pattern_report(
                    current_course_code=self.sinclair_course_code_var.get().strip()
                    or result.output_zip.parent.name,
                    current_source_zip=original_zip,
                    current_converted_zip=result.output_zip,
                    training_courses_root=self._resolve_workspace_root()
                    / "resources"
                    / "training-corpus-v2"
                    / "courses",
                    template_package=template_package_path,
                    best_practices_docx=(
                        Path(self.ref_best_practices_docx_var.get().strip())
                        if self.ref_best_practices_docx_var.get().strip()
                        else None
                    ),
                    output_json_path=pattern_json,
                    output_markdown_path=pattern_markdown,
                )
                pattern_report = json.loads(pattern_json.read_text(encoding="utf-8"))

            writeback_report = json.loads(
                result.report_json.read_text(encoding="utf-8")
            )
            self.root.after(
                0,
                lambda: self._handle_review_writeback_result(
                    result=result,
                    report=writeback_report,
                    visual_json=visual_json,
                    visual_markdown=visual_markdown,
                    visual_summary=visual_summary,
                    math_json=math_json,
                    math_markdown=math_markdown,
                    math_summary=math_summary,
                    page_review_json=page_review_json,
                    page_review_markdown=page_review_markdown,
                    page_review_html=page_review_html,
                    page_review_summary=page_review_summary,
                    pattern_json=pattern_json,
                    pattern_markdown=pattern_markdown,
                    pattern_report=pattern_report,
                ),
            )

        self._run_background("Apply review draft", task)

    def _handle_review_writeback_result(
        self,
        *,
        result,
        report: dict,
        visual_json: Path | None,
        visual_markdown: Path | None,
        visual_summary: dict,
        math_json: Path | None,
        math_markdown: Path | None,
        math_summary: dict,
        page_review_json: Path | None,
        page_review_markdown: Path | None,
        page_review_html: Path | None,
        page_review_summary: dict,
        pattern_json: Path | None,
        pattern_markdown: Path | None,
        pattern_report: dict,
    ) -> None:
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        self.visual_converted_zip_var.set(str(result.output_zip))
        # Ensure the Upload tab always points at the most-recently reviewed zip,
        # not the original canvas-ready zip, so users can't accidentally upload
        # the unreviewed version.
        self.canvas_upload_zip_var.set(str(result.output_zip))
        self.math_audit_output_var.set(
            str(_default_math_audit_json_path(result.output_zip))
        )
        self.review_draft_json_var.set(
            str(_default_review_draft_json_path(result.output_zip))
        )
        self.reviewed_zip_output_var.set(str(result.output_zip))
        self.pattern_report_output_var.set(
            str(_default_pattern_report_json_path(result.output_zip))
        )
        self._log(f"Reviewed zip: {result.output_zip}")
        self._log(f"Review write-back JSON: {result.report_json}")
        self._log(f"Review write-back Markdown: {result.report_markdown}")
        self._log(
            "Write-back summary: "
            f"updated={summary.get('pages_updated', 0)} | "
            f"missing={summary.get('pages_missing', 0)} | "
            f"manual={summary.get('manual_review_issues', 0)} | "
            f"a11y={summary.get('accessibility_issues', 0)}"
        )
        if visual_json is not None:
            self.visual_audit_output_var.set(str(visual_json))
            self._log(f"Reviewed visual audit JSON: {visual_json}")
            if visual_markdown is not None:
                self._log(f"Reviewed visual audit Markdown: {visual_markdown}")
            self._log(
                "Reviewed visual summary: "
                f"files={visual_summary.get('files_scanned', 0)} | "
                f"shared_refs={visual_summary.get('files_with_remaining_shared_template_refs', 0)} | "
                f"nonstandard_hr={visual_summary.get('files_with_nonstandard_hr', 0)}"
            )
        if math_json is not None:
            self.math_audit_output_var.set(str(math_json))
            self._log(f"Reviewed math audit JSON: {math_json}")
            if math_markdown is not None:
                self._log(f"Reviewed math audit Markdown: {math_markdown}")
            self._log(
                "Reviewed math summary: "
                f"files_with_math={math_summary.get('files_with_math', 0)} | "
                f"review_flags={math_summary.get('files_with_math_review_flags', 0)} | "
                f"equation_images={math_summary.get('total_converted_equation_images', 0)} | "
                f"raw_tex={math_summary.get('total_converted_raw_tex_delimiters', 0)}"
            )
        if page_review_json is not None:
            self._log(f"Reviewed page review JSON: {page_review_json}")
            if page_review_markdown is not None:
                self._log(f"Reviewed page review Markdown: {page_review_markdown}")
            if page_review_html is not None:
                self.page_review_html_var.set(str(page_review_html))
                self._log(f"Reviewed page review HTML workbench: {page_review_html}")
            self._log(
                "Reviewed page review summary: "
                f"high={page_review_summary.get('files_with_high_priority_review', 0)} | "
                f"manual_pages={page_review_summary.get('files_with_manual_issues', 0)} | "
                f"a11y_pages={page_review_summary.get('files_with_accessibility_issues', 0)}"
            )
        if pattern_json is not None:
            pattern_summary = (
                pattern_report.get("summary", {})
                if isinstance(pattern_report, dict)
                else {}
            )
            self.pattern_report_output_var.set(str(pattern_json))
            self._log(f"Reviewed pattern report JSON: {pattern_json}")
            if pattern_markdown is not None:
                self._log(f"Reviewed pattern report Markdown: {pattern_markdown}")
            self._log(
                "Reviewed pattern summary: "
                f"current_matches={pattern_summary.get('current_matching_transforms', 0)} | "
                f"current_missing={pattern_summary.get('current_missing_transforms', 0)}"
            )
        self._task_succeeded("Apply review draft")

    def _build_pattern_report_clicked(self) -> None:
        converted_zip_text = self.visual_converted_zip_var.get().strip()
        converted_zip = Path(converted_zip_text) if converted_zip_text else None
        source_zip_text = self.visual_original_zip_var.get().strip()
        source_zip = Path(source_zip_text) if source_zip_text else None

        if converted_zip is None or not converted_zip.exists():
            messagebox.showwarning(
                "Missing converted ZIP", "Select a valid converted ZIP first."
            )
            return
        if source_zip is not None and not source_zip.exists():
            source_zip = None

        output_json_text = self.pattern_report_output_var.get().strip()
        output_json = (
            Path(output_json_text)
            if output_json_text
            else _default_pattern_report_json_path(converted_zip)
        )
        output_markdown = output_json.with_suffix(".md")
        template_package_path = (
            Path(self.template_package_var.get().strip())
            if self.template_package_var.get().strip()
            else None
        )
        if template_package_path is not None and not template_package_path.exists():
            template_package_path = None

        def task() -> None:
            json_path, md_path = build_pattern_report(
                current_course_code=self.sinclair_course_code_var.get().strip()
                or converted_zip.parent.name,
                current_source_zip=source_zip,
                current_converted_zip=converted_zip,
                training_courses_root=self._resolve_workspace_root()
                / "resources"
                / "training-corpus-v2"
                / "courses",
                template_package=template_package_path,
                best_practices_docx=(
                    Path(self.ref_best_practices_docx_var.get().strip())
                    if self.ref_best_practices_docx_var.get().strip()
                    else None
                ),
                output_json_path=output_json,
                output_markdown_path=output_markdown,
            )
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.root.after(
                0,
                lambda: self._handle_pattern_report_result(
                    json_path=json_path,
                    md_path=md_path,
                    report=report,
                ),
            )

        self._run_background("Build pattern report", task)

    def _handle_pattern_report_result(
        self, *, json_path: Path, md_path: Path, report: dict
    ) -> None:
        summary = report.get("summary", {})
        self.pattern_report_output_var.set(str(json_path))
        self._log(f"Pattern report JSON: {json_path}")
        self._log(f"Pattern report Markdown: {md_path}")
        self._log(
            "Pattern summary: "
            f"training_pairs={summary.get('training_course_pairs', 0)} | "
            f"consensus={summary.get('consensus_transforms', 0)} | "
            f"current_matches={summary.get('current_matching_transforms', 0)} | "
            f"current_missing={summary.get('current_missing_transforms', 0)}"
        )
        self._task_succeeded("Build pattern report")

    # ── Upload-to-Canvas actions ──────────────────────────────────────────

    def _run_canvas_upload_clicked(self) -> None:
        """Upload the Canvas-ready ZIP to the sandbox course for visual preview."""
        zip_text = self.canvas_upload_zip_var.get().strip()
        if not zip_text:
            messagebox.showwarning(
                "Missing ZIP",
                "Select a Canvas-ready ZIP or run 'Prepare Canvas Package' first.",
            )
            return
        zip_path = Path(zip_text)
        if not zip_path.exists():
            messagebox.showwarning("Missing ZIP", f"File not found: {zip_path}")
            return

        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        tmpl_zip_text = self.canvas_upload_template_zip_var.get().strip()
        tmpl_zip_path = (
            Path(tmpl_zip_text)
            if tmpl_zip_text and self.canvas_upload_include_template_var.get()
            else None
        )
        if tmpl_zip_path is not None and not tmpl_zip_path.exists():
            messagebox.showwarning(
                "Missing template package",
                f"Template package not found: {tmpl_zip_path}",
            )
            return
        output_text = self.canvas_preview_output_var.get().strip()
        output_json = (
            Path(output_text)
            if output_text
            else zip_path.with_name(zip_path.stem + ".preview-result.json")
        )

        def task() -> None:
            def _on_progress(msg: str) -> None:
                self.root.after(0, lambda m=msg: self._log(f"  {m}"))

            self.root.after(
                0,
                lambda: self._log(
                    "  Note: Canvas migration import can take 1–5 minutes. "
                    "Status updates will appear every few seconds."
                ),
            )
            result = run_preview(
                zip_path,
                base_url=base_url,
                token=token,
                course_id=course_id,
                template_zip_path=tmpl_zip_path,
                progress_callback=_on_progress,
            )
            output_json.parent.mkdir(parents=True, exist_ok=True)
            import dataclasses

            output_json.write_text(
                json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8"
            )
            self.root.after(
                0, lambda: self._handle_canvas_upload_result(result, output_json)
            )

        self._run_background("Upload to Canvas sandbox", task)

    def _handle_canvas_upload_result(self, result, output_json: Path) -> None:
        try:
            page_urls: list[str] = list(getattr(result, "page_urls", []))
            issues: list = list(getattr(result, "migration_issues", []))

            self._upload_page_urls = page_urls
            self._log(
                f"Upload complete — {len(page_urls)} page(s), {len(issues)} migration issue(s)."
            )
            self._log(f"Preview result JSON: {output_json}")

            self.upload_results_text.configure(state="normal")
            self.upload_results_text.delete("1.0", "end")
            if page_urls:
                self.upload_results_text.insert(
                    "end",
                    f"Migration complete — {len(page_urls)} page(s), {len(issues)} issue(s).\n"
                    f"Result saved to: {output_json}\n\n"
                    "Page preview URLs (click to open):\n",
                )
                for i, url in enumerate(page_urls):
                    self.upload_results_text.insert(
                        "end", f"  {url}\n", (f"url_{i}", "url")
                    )
            else:
                self.upload_results_text.insert(
                    "end",
                    f"Migration complete — no pages found.\n"
                    f"Migration issues: {len(issues)}\n"
                    f"Result saved to: {output_json}\n",
                )
            self.upload_results_text.configure(state="disabled")
            self.canvas_preview_output_var.set(str(output_json))
        except Exception as exc:
            self._log(f"[WARN] Upload results display error: {exc}")
        finally:
            self._task_succeeded("Upload to Canvas sandbox")

    def _on_upload_url_clicked(self, event: tk.Event) -> None:
        """Open the clicked URL in the default browser."""
        text_widget = self.upload_results_text
        index = text_widget.index(f"@{event.x},{event.y}")
        for tag in text_widget.tag_names(index):
            if tag.startswith("url_"):
                try:
                    i = int(tag[4:])
                    if 0 <= i < len(self._upload_page_urls):
                        webbrowser.open(self._upload_page_urls[i])
                except (ValueError, IndexError):
                    pass
                return

    def _get_canvas_credentials(self) -> tuple[str, str, str] | None:
        base_url = self.canvas_base_url_var.get().strip()
        course_id = self.canvas_course_id_var.get().strip()
        token = self.canvas_token_var.get().strip()

        if not base_url:
            messagebox.showwarning("Missing Canvas URL", "Enter a Canvas base URL.")
            return None
        if not course_id:
            messagebox.showwarning("Missing course ID", "Enter a Canvas course ID.")
            return None
        if not token:
            messagebox.showwarning("Missing token", "Enter a Canvas API token.")
            return None
        try:
            base_url = normalize_base_url(base_url)
        except CanvasAPIError as exc:
            messagebox.showwarning("Invalid URL", str(exc))
            return None
        return base_url, course_id, token

    def _fetch_canvas_imports_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        def task() -> None:
            migrations = fetch_content_migrations(
                base_url=base_url,
                course_id=course_id,
                token=token,
            )
            self.root.after(0, lambda: self._handle_canvas_imports_result(migrations))

        self._run_background("Fetch Canvas content migrations", task)

    def _handle_canvas_imports_result(self, migrations: list[dict]) -> None:
        if not migrations:
            self.canvas_migration_id_var.set("")
            self._log("Canvas migrations: none found for this course.")
            self._task_succeeded("Fetch Canvas content migrations")
            return

        latest_id = self._pick_latest_migration_id(migrations)
        self.canvas_migration_id_var.set(latest_id)
        self._maybe_sync_canvas_issues_output_path()

        self._log(
            f"Canvas migrations fetched: {len(migrations)}. "
            f"Latest migration ID set to: {latest_id or '(missing)'}"
        )
        for idx, item in enumerate(migrations[:10], start=1):
            self._log(
                f"  {idx}. id={item.get('id')} | "
                f"state={item.get('workflow_state')} | "
                f"created={item.get('created_at')}"
            )
        if len(migrations) > 10:
            self._log(f"  ... ({len(migrations) - 10} more)")
        self._task_succeeded("Fetch Canvas content migrations")

    def _export_canvas_issues_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds
        migration_id = self.canvas_migration_id_var.get().strip()
        if not migration_id:
            messagebox.showwarning(
                "Missing migration ID",
                "Enter a migration ID or click Fetch Imports first.",
            )
            return

        output_text = self.canvas_issues_output_var.get().strip()
        if self._should_auto_reset_canvas_issues_output(output_text):
            output_path = self._default_canvas_issues_output_path(course_id)
            self.canvas_issues_output_var.set(str(output_path))
        else:
            output_path = Path(output_text)

        def task() -> None:
            issues = fetch_migration_issues(
                base_url=base_url,
                course_id=course_id,
                migration_id=migration_id,
                token=token,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(issues, indent=2), encoding="utf-8")
            self.root.after(
                0,
                lambda: self._handle_canvas_issues_export_result(
                    output_path=output_path,
                    issues_count=len(issues),
                    migration_id=migration_id,
                ),
            )

        self._run_background("Export Canvas migration issues", task)

    def _handle_canvas_issues_export_result(
        self,
        *,
        output_path: Path,
        issues_count: int,
        migration_id: str,
    ) -> None:
        self.canvas_issues_output_var.set(str(output_path))
        self._log(
            f"Canvas migration issues exported: {issues_count} "
            f"(migration_id={migration_id})"
        )
        self._log(f"Issues JSON: {output_path}")
        self._task_succeeded("Export Canvas migration issues")

    def _auto_relink_missing_links_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        issues_path = Path(self.canvas_issues_output_var.get().strip())
        if not issues_path.exists():
            messagebox.showwarning(
                "Missing issues JSON",
                "Export Canvas issues first, or select a valid issues JSON output path.",
            )
            return

        output_path = issues_path.parent / "canvas-auto-relink-report.json"
        alias_map_path = self._resolve_alias_map_path(show_warning=True)
        if self.use_template_alias_map_var.get() and alias_map_path is None:
            return

        proceed = messagebox.askyesno(
            "Confirm Canvas Updates",
            "Auto-Relink will update Canvas page HTML in this course.\n\nContinue?",
        )
        if not proceed:
            return

        def task() -> None:
            report_path = auto_relink_missing_links(
                base_url=base_url,
                course_id=course_id,
                token=token,
                issues_json_path=issues_path,
                output_json_path=output_path,
                alias_map_json_path=alias_map_path,
                dry_run=False,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.root.after(
                0,
                lambda: self._handle_auto_relink_result(
                    report_path=report_path,
                    report=report,
                ),
            )

        self._run_background("Auto-relink missing links", task)

    def _handle_auto_relink_result(self, *, report_path: Path, report: dict) -> None:
        summary = report.get("summary", {})
        self._log(f"Auto-relink report JSON: {report_path}")
        self._log(
            "Auto-relink summary: "
            f"pages_scanned={summary.get('pages_scanned', 0)} | "
            f"pages_updated={summary.get('pages_updated', 0)} | "
            f"total_rewrites={summary.get('total_rewrites', 0)} | "
            f"alias_rewrites={summary.get('total_alias_rewrites', 0)} | "
            f"unresolved_local_refs={summary.get('total_unresolved_local_refs', 0)} | "
            f"file_name_collisions={summary.get('file_name_collisions', 0)}"
        )
        self._log(f"Alias map source: {report.get('alias_map_json') or 'none'}")
        self._log(f"Alias rules loaded: {report.get('alias_map_rules_loaded', 0)}")
        self._task_succeeded("Auto-relink missing links")

    def _run_live_link_audit_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        output_text = self.canvas_issues_output_var.get().strip()
        if output_text:
            output_dir = Path(output_text).parent
        else:
            output_dir = Path(
                self.output_dir_var.get().strip()
                or (self._resolve_workspace_root() / "output")
            )

        variant = self.ab_variant_var.get().strip().upper() or "A"
        artifact_prefix = self._ab_artifact_prefix(variant)
        if "/ab-test/" in str(output_dir).replace("\\", "/").lower():
            output_json = output_dir / f"{artifact_prefix}.canvas-live-link-audit.json"
        else:
            output_json = output_dir / "canvas-live-link-audit.json"
        output_md = output_json.with_suffix(".md")
        output_csv = output_json.with_suffix(".csv")

        alias_map_path = self._resolve_alias_map_path(show_warning=True)
        if self.use_template_alias_map_var.get() and alias_map_path is None:
            return
        apply_safe_fixes = bool(self.live_audit_apply_safe_fixes_var.get())

        if apply_safe_fixes:
            proceed = messagebox.askyesno(
                "Confirm Canvas Updates",
                "Live Link Audit is set to apply safe page fixes before reporting.\n\nContinue?",
            )
            if not proceed:
                return

        def task() -> None:
            json_path, md_path, csv_path = run_live_link_audit(
                base_url=base_url,
                course_id=course_id,
                token=token,
                output_json_path=output_json,
                output_markdown_path=output_md,
                output_csv_path=output_csv,
                apply_safe_fixes=apply_safe_fixes,
                alias_map_json_path=alias_map_path,
            )
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.root.after(
                0,
                lambda: self._handle_live_link_audit_result(
                    json_path=json_path,
                    md_path=md_path,
                    csv_path=csv_path,
                    report=report,
                ),
            )

        self._run_background("Run live Canvas link audit", task)

    def _handle_live_link_audit_result(
        self,
        *,
        json_path: Path,
        md_path: Path,
        csv_path: Path,
        report: dict,
    ) -> None:
        counts = report.get("counts", {})
        safe_fix = report.get("safe_fix_summary", {})
        self._log(f"Live audit JSON: {json_path}")
        self._log(f"Live audit Markdown: {md_path}")
        self._log(f"Live audit CSV: {csv_path}")
        self._log(
            "Live audit summary: "
            f"findings={counts.get('findings_total', 0)} | "
            f"pages={counts.get('pages', 0)} | "
            f"assignments={counts.get('assignments', 0)} | "
            f"discussions={counts.get('discussions', 0)} | "
            f"announcements={counts.get('announcements', 0)}"
        )
        self._log(
            "Live audit fixes: "
            f"pages_updated={safe_fix.get('pages_updated', 0)} | "
            f"rewrites={safe_fix.get('total_rewrites', 0)} | "
            f"alias_rewrites={safe_fix.get('total_alias_rewrites', 0)} | "
            f"unresolved_local_refs={safe_fix.get('total_unresolved_local_refs', 0)}"
        )
        self._task_succeeded("Run live Canvas link audit")

    def _snapshot_canvas_course_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        creds = self._get_canvas_credentials()
        if creds is None:
            return
        base_url, course_id, token = creds

        output_text = self.canvas_issues_output_var.get().strip()
        if output_text:
            output_dir = Path(output_text).parent
        else:
            output_dir = Path(
                self.output_dir_var.get().strip()
                or (self._resolve_workspace_root() / "output")
            )
        snapshot_json = output_dir / f"canvas-course-{course_id}.snapshot.json"
        snapshot_md = output_dir / f"canvas-course-{course_id}.snapshot.md"

        def task() -> None:
            json_path, md_path = snapshot_canvas_course(
                base_url=base_url,
                course_id=course_id,
                token=token,
                output_json_path=snapshot_json,
                output_markdown_path=snapshot_md,
            )
            self.root.after(
                0,
                lambda: self._handle_snapshot_canvas_course_result(json_path, md_path),
            )

        self._run_background("Snapshot Canvas course", task)

    def _handle_snapshot_canvas_course_result(
        self, json_path: Path, md_path: Path
    ) -> None:
        self._log(f"Canvas snapshot JSON: {json_path}")
        self._log(f"Canvas snapshot Markdown: {md_path}")
        self._task_succeeded("Snapshot Canvas course")

    def _build_approval_report_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        self._remember_sinclair_course_code()

        course_code = self.sinclair_course_code_var.get().strip()
        output_dir_text = self.output_dir_var.get().strip()
        output_dir = (
            Path(output_dir_text)
            if output_dir_text
            else (self._resolve_workspace_root() / "output")
        )

        source_zip = (
            Path(self.input_zip_var.get().strip())
            if self.input_zip_var.get().strip()
            else None
        )
        if source_zip is not None and not source_zip.exists():
            source_zip = None

        converted_zip = (
            Path(self.visual_converted_zip_var.get().strip())
            if self.visual_converted_zip_var.get().strip()
            else None
        )
        if converted_zip is not None and not converted_zip.exists():
            converted_zip = None
        if converted_zip is None:
            converted_zip = self._find_latest_matching_file(
                output_dir, "*.canvas-ready.zip"
            )

        migration_report_json = (
            Path(self.report_json_var.get().strip())
            if self.report_json_var.get().strip()
            else None
        )
        if migration_report_json is not None and not migration_report_json.exists():
            migration_report_json = None
        if migration_report_json is None:
            migration_report_json = self._find_latest_matching_file(
                output_dir, "*.migration-report.json"
            )

        visual_audit_json = (
            Path(self.visual_audit_output_var.get().strip())
            if self.visual_audit_output_var.get().strip()
            else None
        )
        if visual_audit_json is not None and not visual_audit_json.exists():
            visual_audit_json = None
        if visual_audit_json is None:
            visual_audit_json = self._find_latest_matching_file(
                output_dir, "*.visual-audit.json"
            )

        template_overlay_json = self._find_latest_matching_file(
            output_dir, "*.template-overlay-report.json"
        )
        live_audit_json = output_dir / "canvas-live-link-audit.json"
        if not live_audit_json.exists():
            live_audit_json = None

        course_id = self.canvas_course_id_var.get().strip()
        snapshot_json = self._find_latest_snapshot_json(output_dir, course_id)
        if snapshot_json is not None and not snapshot_json.exists():
            snapshot_json = None

        pre_issues_json = output_dir / "canvas-migration-issues-pre.json"
        if not pre_issues_json.exists():
            pre_issues_json = None

        post_issues_json = output_dir / "canvas-migration-issues-post.json"
        if not post_issues_json.exists():
            issues_text = self.canvas_issues_output_var.get().strip()
            fallback_issues = (
                Path(issues_text)
                if issues_text
                else (output_dir / "canvas-migration-issues.json")
            )
            post_issues_json = fallback_issues if fallback_issues.exists() else None

        if migration_report_json is not None:
            if not self._artifact_is_current(snapshot_json, migration_report_json):
                snapshot_json = None
            if not self._artifact_is_current(pre_issues_json, migration_report_json):
                pre_issues_json = None
            if not self._artifact_is_current(post_issues_json, migration_report_json):
                post_issues_json = None
            if not self._artifact_is_current(live_audit_json, migration_report_json):
                live_audit_json = None

        workspace_root = self._resolve_workspace_root()
        output_json = self._default_approval_report_json_path(output_dir)
        output_markdown = output_json.with_suffix(".md")

        def task() -> None:
            json_path, md_path = build_approval_report(
                current_course_code=course_code or output_dir.name,
                current_source_zip=source_zip,
                current_converted_zip=converted_zip,
                current_migration_report_json=migration_report_json,
                current_visual_audit_json=visual_audit_json,
                current_template_overlay_json=template_overlay_json,
                current_snapshot_json=snapshot_json,
                pre_issues_json=pre_issues_json,
                post_issues_json=post_issues_json,
                live_audit_json=live_audit_json,
                examples_dir=workspace_root / "resources" / "examples",
                training_metadata_root=workspace_root
                / "resources"
                / "training-corpus-v2"
                / "courses",
                output_root=workspace_root / "output",
                output_json_path=output_json,
                output_markdown_path=output_markdown,
            )
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.root.after(
                0,
                lambda: self._handle_approval_report_result(
                    json_path=json_path,
                    md_path=md_path,
                    report=report,
                ),
            )

        self._run_background("Build approval report", task)

    def _handle_approval_report_result(
        self, *, json_path: Path, md_path: Path, report: dict
    ) -> None:
        summary = report.get("summary", {})
        references = report.get("top_reference_courses", [])
        reference_text = ", ".join(
            f"{row.get('course_code', '')} ({row.get('similarity_score', 0)})"
            for row in references[:3]
            if isinstance(row, dict)
        )
        self._log(f"Approval report JSON: {json_path}")
        self._log(f"Approval report Markdown: {md_path}")
        self._log(
            "Approval summary: "
            f"status={summary.get('overall_status', 'unknown')} | "
            f"score={summary.get('approval_score', 0)} | "
            f"cohort={summary.get('reference_cohort_label', 'unknown')}"
        )
        self._log(f"Closest references: {reference_text or 'none'}")
        self._task_succeeded("Build approval report")

    def _find_latest_manual_review_csv(self, folder: Path) -> Path | None:
        candidates = sorted(
            folder.glob("*.manual-review.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _find_latest_matching_file(self, folder: Path, pattern: str) -> Path | None:
        candidates = sorted(
            [path for path in folder.glob(pattern) if path.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _find_latest_snapshot_json(
        self, folder: Path, course_id: str = ""
    ) -> Path | None:
        normalized_course_id = course_id.strip()
        if normalized_course_id:
            exact = folder / f"canvas-course-{normalized_course_id}.snapshot.json"
            if exact.exists():
                return exact
        return self._find_latest_matching_file(folder, "canvas-course-*.snapshot.json")

    def _default_approval_report_json_path(self, folder: Path) -> Path:
        report_path = self._find_latest_matching_file(folder, "*.migration-report.json")
        if report_path is not None and report_path.name.endswith(
            ".migration-report.json"
        ):
            filename = report_path.name.replace(
                ".migration-report.json", ".approval-report.json"
            )
            return folder / filename
        return folder / "migration-approval-report.json"

    def _load_json_file(self, path: Path | None) -> dict | list | None:
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _artifact_is_current(
        self, artifact_path: Path | None, baseline_path: Path | None
    ) -> bool:
        if artifact_path is None or not artifact_path.exists():
            return False
        if baseline_path is None or not baseline_path.exists():
            return True
        return artifact_path.stat().st_mtime >= baseline_path.stat().st_mtime

    def _refresh_readiness_snapshot(self) -> None:
        output_dir_text = self.output_dir_var.get().strip()
        output_dir = (
            Path(output_dir_text)
            if output_dir_text
            else (self._resolve_workspace_root() / "output")
        )

        migration_report_path = self._find_latest_matching_file(
            output_dir, "*.migration-report.json"
        )
        visual_audit_path = self._find_latest_matching_file(
            output_dir, "*.visual-audit.json"
        )
        math_audit_path = self._find_latest_matching_file(
            output_dir, "*.math-audit.json"
        )
        page_review_path = self._find_latest_matching_file(
            output_dir, "*.page-review.json"
        )
        approval_report_path = self._find_latest_matching_file(
            output_dir, "*.approval-report.json"
        )
        pattern_report_path = self._find_latest_matching_file(
            output_dir, "*.pattern-report.json"
        )
        snapshot_path = self._find_latest_snapshot_json(
            output_dir, self.canvas_course_id_var.get().strip()
        )
        issues_path = output_dir / "canvas-migration-issues-post.json"
        if not issues_path.exists():
            issues_path = output_dir / "canvas-migration-issues.json"
        if not issues_path.exists():
            issues_path = None
        live_audit_path = output_dir / "canvas-live-link-audit.json"
        if not live_audit_path.exists():
            live_audit_path = None

        if not self._artifact_is_current(visual_audit_path, migration_report_path):
            visual_audit_path = None
        if not self._artifact_is_current(math_audit_path, migration_report_path):
            math_audit_path = None
        if not self._artifact_is_current(page_review_path, migration_report_path):
            page_review_path = None
        if not self._artifact_is_current(approval_report_path, migration_report_path):
            approval_report_path = None
        if not self._artifact_is_current(pattern_report_path, migration_report_path):
            pattern_report_path = None
        if not self._artifact_is_current(snapshot_path, migration_report_path):
            snapshot_path = None
        if not self._artifact_is_current(issues_path, migration_report_path):
            issues_path = None
        if not self._artifact_is_current(live_audit_path, migration_report_path):
            live_audit_path = None

        migration_report = self._load_json_file(migration_report_path)
        visual_audit = self._load_json_file(visual_audit_path)
        math_audit = self._load_json_file(math_audit_path)
        page_review = self._load_json_file(page_review_path)
        approval_report = self._load_json_file(approval_report_path)
        pattern_report = self._load_json_file(pattern_report_path)
        snapshot_report = self._load_json_file(snapshot_path)
        issues_report = self._load_json_file(issues_path)
        live_audit_report = self._load_json_file(live_audit_path)

        if isinstance(migration_report, dict):
            summary = migration_report.get("summary", {})
            math_summary = (
                math_audit.get("summary", {}) if isinstance(math_audit, dict) else {}
            )
            manual_ct = int(summary.get("manual_review_issues", 0) or 0)
            a11y_ct = int(summary.get("accessibility_issues", 0) or 0)
            changes_ct = int(summary.get("total_automated_changes", 0) or 0)
            math_flags = int(math_summary.get("files_with_math_review_flags", 0) or 0)
            manual_str = f"{manual_ct} manual" if manual_ct else "✓ 0 manual"
            a11y_str = (
                f"{a11y_ct} accessibility flag{'s' if a11y_ct != 1 else ''}"
                if a11y_ct
                else "✓ 0 accessibility flags"
            )
            math_str = (
                f"  ·  {math_flags} math flag{'s' if math_flags != 1 else ''}"
                if math_flags
                else ""
            )
            self.readiness_local_var.set(
                f"✓ Package ready  ·  "
                f"{summary.get('html_files_changed', 0)} of {summary.get('html_files_scanned', 0)} pages changed  ·  "
                f"{changes_ct:,} automated changes  ·  "
                f"{manual_str}  ·  {a11y_str}{math_str}"
            )
        else:
            self.readiness_local_var.set("Local package: waiting for conversion.")

        if isinstance(approval_report, dict):
            approval_summary = approval_report.get("summary", {})
            references = approval_report.get("top_reference_courses", [])
            reference_codes = ", ".join(
                str(row.get("course_code", "")).strip()
                for row in references[:3]
                if isinstance(row, dict) and str(row.get("course_code", "")).strip()
            )
            page_review_summary = (
                page_review.get("summary", {}) if isinstance(page_review, dict) else {}
            )
            pattern_summary = (
                pattern_report.get("summary", {})
                if isinstance(pattern_report, dict)
                else {}
            )
            math_summary = (
                math_audit.get("summary", {}) if isinstance(math_audit, dict) else {}
            )
            status = approval_summary.get("overall_status", "unknown")
            score = approval_summary.get("approval_score", 0)
            status_icon = "✓" if status == "approved" else "⚠"
            high_review = int(
                page_review_summary.get("files_with_high_priority_review", 0) or 0
            )
            pattern_gaps = int(
                pattern_summary.get("current_missing_transforms", 0) or 0
            )
            math_f = int(math_summary.get("files_with_math_review_flags", 0) or 0)
            self.readiness_review_var.set(
                f"{status_icon} Review: {status.upper()}  ·  Score: {score}/100  ·  "
                f"Cohort: {approval_summary.get('reference_cohort_label', 'unknown')}  ·  "
                f"Refs: {reference_codes or 'none'}\n"
                f"    Page review: {high_review} high-priority  ·  "
                f"Pattern gaps: {pattern_gaps}  ·  "
                f"Math flags: {math_f}"
            )
        elif isinstance(pattern_report, dict):
            pattern_summary = pattern_report.get("summary", {})
            math_summary = (
                math_audit.get("summary", {}) if isinstance(math_audit, dict) else {}
            )
            self.readiness_review_var.set(
                "Upload review: pattern report ready"
                f" | matches {pattern_summary.get('current_matching_transforms', 0)}"
                f" | gaps {pattern_summary.get('current_missing_transforms', 0)}"
                f" | consensus {pattern_summary.get('consensus_transforms', 0)}"
                f" | math flags {math_summary.get('files_with_math_review_flags', 0)}"
            )
        elif isinstance(page_review, dict):
            review_summary = page_review.get("summary", {})
            math_summary = (
                math_audit.get("summary", {}) if isinstance(math_audit, dict) else {}
            )
            self.readiness_review_var.set(
                "Upload review: page review ready"
                f" | high {review_summary.get('files_with_high_priority_review', 0)}"
                f" | medium {review_summary.get('files_with_medium_priority_review', 0)}"
                f" | manual pages {review_summary.get('files_with_manual_issues', 0)}"
                f" | math flags {math_summary.get('files_with_math_review_flags', 0)}"
            )
        elif isinstance(math_audit, dict):
            math_summary = math_audit.get("summary", {})
            self.readiness_review_var.set(
                "Upload review: math audit ready"
                f" | files with math {math_summary.get('files_with_math', 0)}"
                f" | review flags {math_summary.get('files_with_math_review_flags', 0)}"
                f" | raw tex {math_summary.get('total_converted_raw_tex_delimiters', 0)}"
            )
        elif isinstance(visual_audit, dict):
            visual_summary = visual_audit.get("summary", {})
            self.readiness_review_var.set(
                "Upload review: visual audit ready"
                f" | duplicate title {visual_summary.get('files_with_duplicate_title_first_block', 0)}"
                f" | shared refs {visual_summary.get('files_with_remaining_shared_template_refs', 0)}"
                " | approval report not run"
            )
        else:
            self.readiness_review_var.set("Upload review: not run.")

        issues_count = len(issues_report) if isinstance(issues_report, list) else 0
        live_findings = 0
        if isinstance(live_audit_report, dict):
            live_findings = int(
                ((live_audit_report.get("counts") or {}).get("findings_total", 0)) or 0
            )
        if isinstance(snapshot_report, dict) or issues_count or live_findings:
            snapshot_course_id = ""
            if isinstance(snapshot_report, dict):
                snapshot_course_id = str(snapshot_report.get("course_id", "")).strip()
            self.readiness_canvas_var.set(
                "Canvas post-import: "
                f"issues {issues_count}"
                f" | live findings {live_findings}"
                f" | snapshot {snapshot_course_id or 'not captured'}"
            )
        else:
            self.readiness_canvas_var.set("Canvas post-import: not run.")

        next_step = "Next step: choose a D2L zip and click Prepare Canvas Package."
        if isinstance(migration_report, dict):
            next_step = "Next step: click Review Readiness before upload, or import the package into Canvas."
        if isinstance(page_review, dict) and not isinstance(approval_report, dict):
            page_review_summary = page_review.get("summary", {})
            if (
                int(page_review_summary.get("files_with_high_priority_review", 0) or 0)
                > 0
            ):
                next_step = "Next step: review the high-priority pages in the page review workbench before upload."
        if isinstance(approval_report, dict):
            approval_summary = approval_report.get("summary", {})
            status = str(approval_summary.get("overall_status", "")).strip().lower()
            if not (isinstance(snapshot_report, dict) or issues_count or live_findings):
                next_step = "Next step: import into Canvas, then run Canvas Cleanup + Audit and Capture Course Snapshot."
            elif status == "approved" and issues_count == 0 and live_findings == 0:
                next_step = "Next step: the course is in a strong state for handoff or final Canvas spot-checks."
            else:
                next_step = "Next step: work the remaining checklist items, then rerun Review Readiness."
        self.readiness_next_step_var.set(next_step)

    def _find_reference_audit_json(self) -> Path | None:
        output_root_text = self.output_dir_var.get().strip()
        output_root = (
            Path(output_root_text)
            if output_root_text
            else (self._resolve_workspace_root() / "output")
        )
        direct = output_root / "reference_audit" / "reference-audit.json"
        if direct.exists():
            return direct

        workspace_default = (
            self._resolve_workspace_root()
            / "output"
            / "reference_audit"
            / "reference-audit.json"
        )
        if workspace_default.exists():
            return workspace_default
        return None

    def _build_fix_checklist_clicked(self) -> None:
        issues_path = Path(self.canvas_issues_output_var.get().strip())
        if not issues_path.exists():
            messagebox.showwarning(
                "Missing issues JSON",
                "Export Canvas issues first, or select a valid issues JSON output path.",
            )
            return

        output_dir = issues_path.parent
        manual_review_csv = self._find_latest_manual_review_csv(output_dir)
        reference_audit_json = self._find_reference_audit_json()

        def task() -> None:
            csv_path, md_path = build_fix_checklist(
                canvas_issues_json=issues_path,
                output_dir=output_dir,
                manual_review_csv=manual_review_csv,
                reference_audit_json=reference_audit_json,
            )
            self.root.after(
                0,
                lambda: self._handle_build_fix_checklist_result(
                    csv_path=csv_path,
                    md_path=md_path,
                    manual_review_csv=manual_review_csv,
                    reference_audit_json=reference_audit_json,
                ),
            )

        self._run_background("Build fix checklist", task)

    def _handle_build_fix_checklist_result(
        self,
        *,
        csv_path: Path,
        md_path: Path,
        manual_review_csv: Path | None,
        reference_audit_json: Path | None,
    ) -> None:
        self._log(f"Fix checklist CSV: {csv_path}")
        self._log(f"Fix checklist Markdown: {md_path}")
        self._log(
            f"Manual review source: {manual_review_csv if manual_review_csv else 'none'}"
        )
        self._log(
            f"Reference audit source: {reference_audit_json if reference_audit_json else 'none'}"
        )
        self._task_succeeded("Build fix checklist")


def main() -> None:
    root = tk.Tk()
    app = LMSMigrationUI(root)
    app.copy_summary_btn.configure(state="disabled")
    root.mainloop()


if __name__ == "__main__":
    main()

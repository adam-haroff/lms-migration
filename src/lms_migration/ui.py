from __future__ import annotations

import json
import re
import threading
import traceback
from pathlib import Path
from typing import Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Tkinter is required for the local UI. Install a Python build that includes Tk."
    ) from exc

from .best_practices import run_audit
from .canvas_api import (
    CanvasAPIError,
    fetch_content_migrations,
    fetch_migration_issues,
    normalize_base_url,
)
from .canvas_post_import import auto_relink_missing_links
from .canvas_live_audit import run_live_link_audit
from .canvas_snapshot import snapshot_canvas_course
from .fix_checklist import build_fix_checklist
from .pipeline import MigrationOutput, run_migration
from .policy_profiles import list_policy_profiles
from .reference_audit import run_reference_audit
from .safe_summary import build_safe_summary_from_path


def _default_safe_summary_path(report_path: Path) -> Path:
    if report_path.name.endswith(".migration-report.json"):
        return report_path.with_name(report_path.name.replace(".migration-report.json", ".safe-summary.txt"))
    return report_path.with_suffix(".safe-summary.txt")


class LMSMigrationUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("LMS Migration Pilot - Local UI")
        self.root.geometry("1050x760")

        self.is_busy = False
        self.latest_safe_summary = ""
        self.ui_state_path = self._resolve_workspace_root() / ".lms-migrate-ui-state.json"
        self.course_code_history = self._load_history("sinclair_course_code_history")
        self.input_zip_history = self._load_history("input_zip_history")

        default_output = self._resolve_workspace_root() / "output"
        self.policy_profiles_path = self._resolve_workspace_root() / "rules" / "policy_profiles.json"
        self.available_policy_profiles = self._load_available_policy_profiles()
        self.input_zip_var = tk.StringVar(value="")
        self.rules_var = tk.StringVar(value=str(self._resolve_default_rules()))
        self.output_dir_var = tk.StringVar(value=str(default_output))
        self.enable_best_practice_enforcer_var = tk.BooleanVar(value=True)
        default_policy = "strict" if "strict" in self.available_policy_profiles else self.available_policy_profiles[0]
        self.policy_profile_var = tk.StringVar(value=default_policy)
        self.report_json_var = tk.StringVar(value="")
        self.safe_summary_path_var = tk.StringVar(value="")

        self.best_practices_file_var = tk.StringVar(value="")
        self.best_practices_sheet_var = tk.StringVar(value="")

        self.ref_instructions_docx_var = tk.StringVar(value="")
        self.ref_best_practices_docx_var = tk.StringVar(value="")
        self.ref_page_templates_docx_var = tk.StringVar(value="")
        self.ref_syllabus_template_docx_var = tk.StringVar(value="")

        self.canvas_base_url_var = tk.StringVar(value="https://sinclair.instructure.com")
        self.canvas_course_id_var = tk.StringVar(value="")
        self.sinclair_course_code_var = tk.StringVar(value="")
        self.canvas_token_var = tk.StringVar(value="")
        self.canvas_migration_id_var = tk.StringVar(value="")
        self.canvas_issues_output_var = tk.StringVar(
            value=str(default_output / "canvas-migration-issues.json")
        )
        self.template_alias_map_var = tk.StringVar(
            value=str(self._resolve_workspace_root() / "rules" / "template_asset_aliases.json")
        )
        self.use_template_alias_map_var = tk.BooleanVar(value=False)
        self.live_audit_apply_safe_fixes_var = tk.BooleanVar(value=False)
        self.ab_variant_var = tk.StringVar(value="A")
        self.ab_include_auto_relink_var = tk.BooleanVar(value=True)
        self.show_canvas_advanced_var = tk.BooleanVar(value=False)

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
            self.ui_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            # Best effort only: avoid blocking migration work on local state write failures.
            pass

    def _remember_sinclair_course_code(self, value: str | None = None) -> None:
        code = (value if value is not None else self.sinclair_course_code_var.get()).strip()
        if not code:
            return

        deduped = [existing for existing in self.course_code_history if existing.lower() != code.lower()]
        self.course_code_history = tuple(([code] + deduped)[:25])
        if hasattr(self, "sinclair_course_code_combo"):
            self.sinclair_course_code_combo.configure(values=self.course_code_history)
        self._save_ui_state()

    def _remember_input_zip_path(self, value: str | None = None) -> None:
        zip_path = (value if value is not None else self.input_zip_var.get()).strip()
        if not zip_path:
            return

        deduped = [existing for existing in self.input_zip_history if existing.lower() != zip_path.lower()]
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
        output_root = Path(output_root_text) if output_root_text else (self._resolve_workspace_root() / "output")

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
        output_root = Path(output_root_text) if output_root_text else (self._resolve_workspace_root() / "output")
        normalized_variant = variant.strip().upper() or "A"
        return output_root / "ab-test" / normalized_variant

    def _slugify_token(self, value: str) -> str:
        lowered = value.strip().lower()
        cleaned = re.sub(r"[^a-z0-9._-]+", "-", lowered)
        cleaned = cleaned.strip("-._")
        return cleaned or "course"

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
            self.canvas_issues_output_var.set(str(self._default_ab_issues_output_path(self.ab_variant_var.get(), "pre")))
            return

        normalized = current_output.replace("\\", "/").lower()
        if "/ab-test/" in normalized or self._should_auto_reset_canvas_issues_output(current_output):
            self.canvas_issues_output_var.set(str(self._default_ab_issues_output_path(self.ab_variant_var.get(), "pre")))

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
                text="Hide Advanced Canvas Options" if show else "Show Advanced Canvas Options"
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
                str(self._default_canvas_issues_output_path(self.canvas_course_id_var.get().strip()))
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

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.scroll_canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0)
        self.scroll_canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.scroll_canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)

        main = ttk.Frame(self.scroll_canvas, padding=12)
        self.scroll_window = self.scroll_canvas.create_window((0, 0), window=main, anchor="nw")
        main.bind("<Configure>", self._on_main_configure)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_configure)

        main.columnconfigure(1, weight=1)

        identifiers = ttk.LabelFrame(main, text="0) Course Identifiers", padding=10)
        identifiers.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        identifiers.columnconfigure(1, weight=1)

        ttk.Label(identifiers, text="Canvas course ID").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(identifiers, textvariable=self.canvas_course_id_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(identifiers, text="Sinclair course code").grid(row=1, column=0, sticky="w", pady=3)
        self.sinclair_course_code_combo = ttk.Combobox(
            identifiers,
            textvariable=self.sinclair_course_code_var,
            values=self.course_code_history,
        )
        self.sinclair_course_code_combo.grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )
        self.sinclair_course_code_combo.bind(
            "<<ComboboxSelected>>",
            lambda *_: self._remember_sinclair_course_code(),
        )
        self.sinclair_course_code_combo.bind(
            "<FocusOut>",
            lambda *_: self._remember_sinclair_course_code(),
        )

        migration = ttk.LabelFrame(main, text="1) Local Course Migration", padding=10)
        migration.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        migration.columnconfigure(1, weight=1)

        ttk.Label(migration, text="D2L export ZIP").grid(row=0, column=0, sticky="w", pady=3)
        self.input_zip_combo = ttk.Combobox(
            migration,
            textvariable=self.input_zip_var,
            values=self.input_zip_history,
        )
        self.input_zip_combo.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        self.input_zip_combo.bind(
            "<<ComboboxSelected>>",
            lambda *_: self._remember_and_apply_input_zip(),
        )
        self.input_zip_combo.bind(
            "<FocusOut>",
            lambda *_: self._remember_and_apply_input_zip(),
        )
        ttk.Button(
            migration,
            text="Browse ZIP",
            command=lambda: self._browse_file(
                self.input_zip_var,
                [("ZIP files", "*.zip"), ("All files", "*.*")],
            ),
        ).grid(row=0, column=2, sticky="e", pady=3)
        self._add_file_row(
            parent=migration,
            row=1,
            label="Rules JSON",
            variable=self.rules_var,
            button_text="Browse Rules",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        self._add_dir_row(
            parent=migration,
            row=2,
            label="Output directory",
            variable=self.output_dir_var,
            button_text="Browse Output",
        )
        ttk.Label(migration, text="Policy profile").grid(row=3, column=0, sticky="w", pady=3)
        self.policy_profile_combo = ttk.Combobox(
            migration,
            textvariable=self.policy_profile_var,
            values=self.available_policy_profiles,
            state="readonly",
        )
        self.policy_profile_combo.grid(row=3, column=1, sticky="ew", padx=6, pady=3)
        self.policy_profile_combo.current(
            self.available_policy_profiles.index(self.policy_profile_var.get())
        )
        ttk.Checkbutton(
            migration,
            text="Apply Best-Practice Enforcer (safe subset)",
            variable=self.enable_best_practice_enforcer_var,
        ).grid(row=4, column=1, sticky="w", pady=(0, 3))

        self.run_migration_btn = ttk.Button(
            migration,
            text="Run Migration",
            command=self._run_migration_clicked,
        )
        self.run_migration_btn.grid(row=5, column=2, sticky="e", pady=(8, 0))
        self.run_full_pipeline_btn = ttk.Button(
            migration,
            text="Run Pre-Import Pipeline",
            command=self._run_pre_import_pipeline_clicked,
        )
        self.run_full_pipeline_btn.grid(row=5, column=1, sticky="e", pady=(8, 0), padx=(0, 8))

        summary = ttk.LabelFrame(main, text="2) Non-Sensitive Summary", padding=10)
        summary.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        summary.columnconfigure(1, weight=1)

        self._add_file_row(
            parent=summary,
            row=0,
            label="Migration report JSON",
            variable=self.report_json_var,
            button_text="Browse Report",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        self._add_file_row(
            parent=summary,
            row=1,
            label="Safe summary output",
            variable=self.safe_summary_path_var,
            button_text="Save As",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            save_mode=True,
        )

        actions = ttk.Frame(summary)
        actions.grid(row=2, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.build_summary_btn = ttk.Button(
            actions,
            text="Generate Safe Summary",
            command=self._generate_safe_summary_clicked,
        )
        self.build_summary_btn.grid(row=0, column=0, padx=(0, 8))
        self.copy_summary_btn = ttk.Button(
            actions,
            text="Copy Summary to Clipboard",
            command=self._copy_summary_clicked,
        )
        self.copy_summary_btn.grid(row=0, column=1)

        audit = ttk.LabelFrame(main, text="3) Best-Practices Audit (Optional)", padding=10)
        audit.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        audit.columnconfigure(1, weight=1)

        self._add_file_row(
            parent=audit,
            row=0,
            label="Best-practices file (.xlsx/.csv)",
            variable=self.best_practices_file_var,
            button_text="Browse File",
            filetypes=[("Spreadsheet", "*.xlsx *.csv"), ("All files", "*.*")],
        )
        ttk.Label(audit, text="Excel tab name (optional, .xlsx only)").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(audit, textvariable=self.best_practices_sheet_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )
        self.run_audit_btn = ttk.Button(
            audit,
            text="Run Audit",
            command=self._run_best_practices_audit_clicked,
        )
        self.run_audit_btn.grid(row=1, column=2, sticky="e", pady=3)

        reference = ttk.LabelFrame(main, text="4) Reference Docs Audit (Optional)", padding=10)
        reference.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        reference.columnconfigure(1, weight=1)

        self._add_file_row(
            parent=reference,
            row=0,
            label="Instructions docx",
            variable=self.ref_instructions_docx_var,
            button_text="Browse File",
            filetypes=[("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            parent=reference,
            row=1,
            label="Best practices docx",
            variable=self.ref_best_practices_docx_var,
            button_text="Browse File",
            filetypes=[("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            parent=reference,
            row=2,
            label="Page templates docx",
            variable=self.ref_page_templates_docx_var,
            button_text="Browse File",
            filetypes=[("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self._add_file_row(
            parent=reference,
            row=3,
            label="Syllabus template docx",
            variable=self.ref_syllabus_template_docx_var,
            button_text="Browse File",
            filetypes=[("Word Document", "*.docx"), ("All files", "*.*")],
        )
        self.run_reference_audit_btn = ttk.Button(
            reference,
            text="Run Reference Audit",
            command=self._run_reference_audit_clicked,
        )
        self.run_reference_audit_btn.grid(row=4, column=2, sticky="e", pady=(6, 0))

        canvas = ttk.LabelFrame(main, text="5) Canvas Import Issues Export (Optional)", padding=10)
        canvas.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        canvas.columnconfigure(1, weight=1)

        ttk.Label(canvas, text="Canvas base URL").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(canvas, textvariable=self.canvas_base_url_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(canvas, text="Canvas course ID").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(canvas, textvariable=self.canvas_course_id_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(canvas, text="API token").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(canvas, textvariable=self.canvas_token_var, show="*").grid(
            row=2, column=1, sticky="ew", padx=6, pady=3
        )
        self.canvas_advanced_toggle_btn = ttk.Button(
            canvas,
            text="Show Advanced Canvas Options",
            command=self._toggle_canvas_advanced,
        )
        self.canvas_advanced_toggle_btn.grid(row=3, column=1, sticky="w", pady=(2, 4))

        self.canvas_advanced_frame = ttk.Frame(canvas)
        self.canvas_advanced_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.canvas_advanced_frame.columnconfigure(1, weight=1)

        ttk.Label(self.canvas_advanced_frame, text="Migration ID").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(self.canvas_advanced_frame, textvariable=self.canvas_migration_id_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Label(self.canvas_advanced_frame, text="Issues JSON output").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(self.canvas_advanced_frame, textvariable=self.canvas_issues_output_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )
        ttk.Button(
            self.canvas_advanced_frame,
            text="Save As",
            command=lambda: self._browse_file(
                self.canvas_issues_output_var,
                [("JSON files", "*.json"), ("All files", "*.*")],
                save_mode=True,
            ),
        ).grid(row=1, column=2, sticky="e", pady=3)
        ttk.Label(self.canvas_advanced_frame, text="Template alias map").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(self.canvas_advanced_frame, textvariable=self.template_alias_map_var).grid(
            row=2, column=1, sticky="ew", padx=6, pady=3
        )
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
            ab_row,
            text="Run A/B Variant Cycle",
            command=self._run_ab_variant_cycle_clicked,
        )
        self.run_ab_variant_cycle_btn.grid(row=0, column=3, sticky="e", padx=(12, 0))

        canvas_actions = ttk.Frame(canvas)
        canvas_actions.grid(row=5, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.run_post_import_pipeline_btn = ttk.Button(
            canvas_actions,
            text="Run Post-Import Pipeline",
            command=self._run_post_import_pipeline_clicked,
        )
        self.run_post_import_pipeline_btn.grid(row=0, column=0, padx=(0, 8))
        self.fetch_canvas_imports_btn = ttk.Button(
            canvas_actions,
            text="Fetch Imports",
            command=self._fetch_canvas_imports_clicked,
        )
        self.fetch_canvas_imports_btn.grid(row=0, column=1, padx=(0, 8))
        self.export_canvas_issues_btn = ttk.Button(
            canvas_actions,
            text="Export Issues JSON",
            command=self._export_canvas_issues_clicked,
        )
        self.export_canvas_issues_btn.grid(row=0, column=2)
        self.auto_relink_btn = ttk.Button(
            canvas_actions,
            text="Auto-Relink Missing Links",
            command=self._auto_relink_missing_links_clicked,
        )
        self.auto_relink_btn.grid(row=0, column=3, padx=(8, 0))
        self.build_fix_checklist_btn = ttk.Button(
            canvas_actions,
            text="Build Fix Checklist",
            command=self._build_fix_checklist_clicked,
        )
        self.build_fix_checklist_btn.grid(row=0, column=4, padx=(8, 0))
        self.snapshot_canvas_course_btn = ttk.Button(
            canvas_actions,
            text="Snapshot Course",
            command=self._snapshot_canvas_course_clicked,
        )
        self.snapshot_canvas_course_btn.grid(row=0, column=5, padx=(8, 0))
        self.live_link_audit_btn = ttk.Button(
            canvas_actions,
            text="Live Link Audit",
            command=self._run_live_link_audit_clicked,
        )
        self.live_link_audit_btn.grid(row=0, column=6, padx=(8, 0))
        ttk.Checkbutton(
            canvas_actions,
            text="Apply Safe Fixes",
            variable=self.live_audit_apply_safe_fixes_var,
        ).grid(row=0, column=7, padx=(8, 0))
        self._apply_canvas_advanced_visibility()

        log_frame = ttk.LabelFrame(main, text="Run Log", padding=10)
        log_frame.grid(row=6, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=18)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        self._log("Ready. Select a D2L zip and click Run Migration.")
        self._bind_mousewheel()

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
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=3)
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
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=3)
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

    def _browse_directory(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory()
        if path:
            variable.set(path)

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
        self.scroll_canvas.yview_scroll(delta, "units")

    def _log(self, text: str) -> None:
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        self.input_zip_combo.configure(state=state)
        self.run_migration_btn.configure(state=state)
        self.run_full_pipeline_btn.configure(state=state)
        self.policy_profile_combo.configure(state="disabled" if busy else "readonly")
        self.build_summary_btn.configure(state=state)
        self.copy_summary_btn.configure(state=state if self.latest_safe_summary else "disabled")
        self.run_audit_btn.configure(state=state)
        self.run_reference_audit_btn.configure(state=state)
        self.run_post_import_pipeline_btn.configure(state=state)
        self.fetch_canvas_imports_btn.configure(state=state)
        self.export_canvas_issues_btn.configure(state=state)
        self.auto_relink_btn.configure(state=state)
        self.build_fix_checklist_btn.configure(state=state)
        self.snapshot_canvas_course_btn.configure(state=state)
        self.live_link_audit_btn.configure(state=state)
        self.run_ab_variant_cycle_btn.configure(state=state)
        self.canvas_advanced_toggle_btn.configure(state=state)
        self.ab_variant_combo.configure(state="disabled" if busy else "readonly")

    def _run_background(self, task_name: str, target: Callable[[], None]) -> None:
        if self.is_busy:
            return
        self._set_busy(True)
        self._log(f"[START] {task_name}")

        def worker() -> None:
            try:
                target()
            except Exception as exc:
                tb = traceback.format_exc()
                self.root.after(0, lambda: self._task_failed(task_name, exc, tb))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _task_failed(self, task_name: str, exc: Exception, traceback_text: str) -> None:
        self._set_busy(False)
        self._log(f"[ERROR] {task_name}: {exc}")
        self._log(traceback_text.strip())
        messagebox.showerror("Task failed", f"{task_name} failed:\n{exc}")

    def _task_succeeded(self, task_name: str) -> None:
        self._log(f"[DONE] {task_name}")
        self._set_busy(False)

    def _run_migration_clicked(self) -> None:
        self._maybe_apply_course_folder_defaults()
        input_zip = Path(self.input_zip_var.get().strip())
        rules_path = Path(self.rules_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip())
        policy_profile_id = self.policy_profile_var.get().strip()
        best_practice_enforcer = bool(self.enable_best_practice_enforcer_var.get())

        if not input_zip.exists():
            messagebox.showwarning("Missing input", "Select a valid D2L export ZIP.")
            return
        if not rules_path.exists():
            messagebox.showwarning("Missing rules", "Select a valid rules JSON file.")
            return
        if not self.policy_profiles_path.exists():
            messagebox.showwarning("Missing profiles", f"Policy profiles file not found: {self.policy_profiles_path}")
            return
        self._remember_sinclair_course_code()
        self._remember_input_zip_path(str(input_zip))

        def task() -> None:
            reference_audit_json = self._find_reference_audit_json()
            result = run_migration(
                input_zip=input_zip,
                output_dir=output_dir,
                rules_path=rules_path,
                policy_profile_id=policy_profile_id,
                policy_profiles_path=self.policy_profiles_path,
                reference_audit_json=reference_audit_json,
                best_practice_enforcer=best_practice_enforcer,
            )
            self.root.after(0, lambda: self._handle_migration_result(result, reference_audit_json))

        self._run_background("Run local migration", task)

    def _run_pre_import_pipeline_clicked(self) -> None:
        self._run_migration_clicked()

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
                raise RuntimeError("No content migrations found for this Canvas course.")
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
            manual_review_csv=manual_review_csv if manual_review_csv and manual_review_csv.exists() else None,
            reference_audit_json=reference_audit_json if reference_audit_json and reference_audit_json.exists() else None,
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
        output_root = Path(output_root_text) if output_root_text else (self._resolve_workspace_root() / "output")
        manual_review_dirs = [issues_path.parent, output_root]

        def task() -> None:
            self.root.after(0, lambda: self._log("[Post-Import] Using latest migration ID for issues export."))
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
            self.root.after(0, lambda: self._handle_post_import_pipeline_result(payload))

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

        self._log(f"Canvas migration issues exported: {issues_count} (migration_id={migration_id})")
        self._log(f"Issues JSON: {issues_path}")
        self._log(f"Fix checklist CSV: {checklist_csv}")
        self._log(f"Fix checklist Markdown: {checklist_md}")
        self._log(f"Manual review source: {manual_review_csv if manual_review_csv else 'none'}")
        self._log(f"Reference audit source: {reference_audit_json if reference_audit_json else 'none'}")
        self._task_succeeded("Run post-import pipeline")

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
        post_issues_path = ab_dir / f"{artifact_prefix}.canvas-migration-issues-post.json"
        relink_report_path = ab_dir / f"{artifact_prefix}.canvas-auto-relink-report.json"
        include_auto_relink = bool(self.ab_include_auto_relink_var.get())
        alias_map_path = self._resolve_alias_map_path(show_warning=True)
        if self.use_template_alias_map_var.get() and alias_map_path is None:
            return

        selected_migration_id = self.canvas_migration_id_var.get().strip()
        reference_audit_json = self._find_reference_audit_json()
        output_root_text = self.output_dir_var.get().strip()
        output_root = Path(output_root_text) if output_root_text else (self._resolve_workspace_root() / "output")
        manual_review_dirs = [ab_dir, output_root]

        def task() -> None:
            self.root.after(0, lambda: self._log(f"[A/B {variant}] Using latest migration ID for pre-export."))
            self.root.after(0, lambda: self._log(f"[A/B {variant}] Exporting pre issues..."))
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
                self.root.after(0, lambda: self._log(f"[A/B {variant}] Running auto-relink..."))
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
                self.root.after(0, lambda: self._log(f"[A/B {variant}] Auto-relink skipped by toggle."))

            self.root.after(0, lambda: self._log(f"[A/B {variant}] Exporting post issues..."))
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
                "relink_report_path": relink_report_path if include_auto_relink else None,
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
        migration_id = str(post.get("migration_id", pre.get("migration_id", ""))).strip()
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
        self.latest_safe_summary = safe_summary

        self._log(f"Canvas-ready zip: {result.output_zip}")
        self._log(f"Migration report JSON: {result.report_json}")
        self._log(f"Migration report Markdown: {result.report_markdown}")
        self._log(f"Manual review CSV: {result.manual_review_csv}")
        self._log(f"Preflight checklist: {result.preflight_checklist}")
        self._log(f"Policy profile used: {result.policy_profile_id}")
        self._log(f"Best-practice enforcer: {self.enable_best_practice_enforcer_var.get()}")
        self._log(f"Reference alignment input: {reference_audit_json if reference_audit_json else 'none'}")
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

    def _generate_safe_summary_clicked(self) -> None:
        report_path = Path(self.report_json_var.get().strip())
        output_text = self.safe_summary_path_var.get().strip()

        if not report_path.exists():
            messagebox.showwarning("Missing report", "Select a valid migration report JSON file.")
            return

        output_path = Path(output_text) if output_text else _default_safe_summary_path(report_path)

        def task() -> None:
            safe_summary = build_safe_summary_from_path(report_path)
            output_path.write_text(safe_summary, encoding="utf-8")
            self.root.after(0, lambda: self._handle_safe_summary_result(output_path, safe_summary))

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
            messagebox.showwarning("Missing file", "Select a valid best-practices spreadsheet file.")
            return

        def task() -> None:
            json_path, md_path = run_audit(input_path=input_path, output_dir=output_dir, sheet_name=sheet)
            self.root.after(0, lambda: self._handle_audit_result(json_path, md_path))

        self._run_background("Run best-practices audit", task)

    def _handle_audit_result(self, json_path: Path, md_path: Path) -> None:
        self._log(f"Best-practices audit JSON: {json_path}")
        self._log(f"Best-practices audit Markdown: {md_path}")
        self._task_succeeded("Run best-practices audit")

    def _run_reference_audit_clicked(self) -> None:
        instructions_docx = Path(self.ref_instructions_docx_var.get().strip())
        best_practices_docx = Path(self.ref_best_practices_docx_var.get().strip())
        page_templates_docx = Path(self.ref_page_templates_docx_var.get().strip())
        syllabus_template_docx = Path(self.ref_syllabus_template_docx_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip()) / "reference_audit"
        workspace_root = self._resolve_workspace_root()
        draft_markdown = workspace_root / "docs" / "lms-migration-custom-instructions-draft.md"
        rules_json = workspace_root / "rules" / "sinclair_pilot_rules.json"
        findings_markdown = workspace_root / "docs" / "pdf-best-practices-initial-findings.md"

        required = (
            instructions_docx,
            best_practices_docx,
            page_templates_docx,
            syllabus_template_docx,
            draft_markdown,
            rules_json,
            findings_markdown,
        )
        missing = [str(path) for path in required if not path.exists()]
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
                page_templates_docx=page_templates_docx,
                syllabus_template_docx=syllabus_template_docx,
                rules_json=rules_json,
                findings_markdown=findings_markdown,
                output_dir=output_dir,
            )
            self.root.after(0, lambda: self._handle_reference_audit_result(json_path, md_path))

        self._run_background("Run reference docs audit", task)

    def _handle_reference_audit_result(self, json_path: Path, md_path: Path) -> None:
        self._log(f"Reference audit JSON: {json_path}")
        self._log(f"Reference audit Markdown: {md_path}")
        self._task_succeeded("Run reference docs audit")

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

        latest = migrations[0]
        latest_id = str(latest.get("id", "")).strip()
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
            output_dir = Path(self.output_dir_var.get().strip() or (self._resolve_workspace_root() / "output"))

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
            output_dir = Path(self.output_dir_var.get().strip() or (self._resolve_workspace_root() / "output"))
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
            self.root.after(0, lambda: self._handle_snapshot_canvas_course_result(json_path, md_path))

        self._run_background("Snapshot Canvas course", task)

    def _handle_snapshot_canvas_course_result(self, json_path: Path, md_path: Path) -> None:
        self._log(f"Canvas snapshot JSON: {json_path}")
        self._log(f"Canvas snapshot Markdown: {md_path}")
        self._task_succeeded("Snapshot Canvas course")

    def _find_latest_manual_review_csv(self, folder: Path) -> Path | None:
        candidates = sorted(
            folder.glob("*.manual-review.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _find_reference_audit_json(self) -> Path | None:
        output_root_text = self.output_dir_var.get().strip()
        output_root = Path(output_root_text) if output_root_text else (self._resolve_workspace_root() / "output")
        direct = output_root / "reference_audit" / "reference-audit.json"
        if direct.exists():
            return direct

        workspace_default = self._resolve_workspace_root() / "output" / "reference_audit" / "reference-audit.json"
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
        self._log(f"Manual review source: {manual_review_csv if manual_review_csv else 'none'}")
        self._log(f"Reference audit source: {reference_audit_json if reference_audit_json else 'none'}")
        self._task_succeeded("Build fix checklist")


def main() -> None:
    root = tk.Tk()
    app = LMSMigrationUI(root)
    app.copy_summary_btn.configure(state="disabled")
    root.mainloop()


if __name__ == "__main__":
    main()

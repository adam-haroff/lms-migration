from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyProfile:
    profile_id: str
    description: str
    template_checks_enabled: bool
    sanitize_brightspace_assets: bool
    neutralize_legacy_d2l_links: bool
    use_alt_text_for_removed_template_images: bool
    repair_missing_local_references: bool
    check_instructor_notes: bool
    check_template_placeholders: bool
    check_legacy_quiz_wording: bool
    require_mc_closing_bullet: bool
    preflight_items: tuple[str, ...]


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def _parse_profile(profile_id: str, raw: dict[str, Any]) -> PolicyProfile:
    return PolicyProfile(
        profile_id=profile_id,
        description=_as_str(raw.get("description"), "No description"),
        template_checks_enabled=_as_bool(raw.get("template_checks_enabled"), True),
        sanitize_brightspace_assets=_as_bool(raw.get("sanitize_brightspace_assets"), True),
        neutralize_legacy_d2l_links=_as_bool(raw.get("neutralize_legacy_d2l_links"), True),
        use_alt_text_for_removed_template_images=_as_bool(
            raw.get("use_alt_text_for_removed_template_images"), True
        ),
        repair_missing_local_references=_as_bool(raw.get("repair_missing_local_references"), True),
        check_instructor_notes=_as_bool(raw.get("check_instructor_notes"), True),
        check_template_placeholders=_as_bool(raw.get("check_template_placeholders"), True),
        check_legacy_quiz_wording=_as_bool(raw.get("check_legacy_quiz_wording"), True),
        require_mc_closing_bullet=_as_bool(raw.get("require_mc_closing_bullet"), True),
        preflight_items=_as_str_tuple(raw.get("preflight_items")),
    )


def load_policy_profiles(config_path: Path) -> dict[str, PolicyProfile]:
    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    raw_profiles = raw.get("profiles", {})
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError(f"No profiles found in config: {config_path}")

    parsed: dict[str, PolicyProfile] = {}
    for profile_id, profile_raw in raw_profiles.items():
        if not isinstance(profile_raw, dict):
            continue
        parsed[profile_id] = _parse_profile(profile_id, profile_raw)

    if not parsed:
        raise ValueError(f"No valid profiles found in config: {config_path}")
    return parsed


def get_policy_profile(profile_id: str, config_path: Path) -> PolicyProfile:
    profiles = load_policy_profiles(config_path)
    if profile_id not in profiles:
        valid = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown policy profile '{profile_id}'. Valid profiles: {valid}")
    return profiles[profile_id]


def list_policy_profiles(config_path: Path) -> tuple[str, ...]:
    profiles = load_policy_profiles(config_path)
    return tuple(sorted(profiles.keys()))

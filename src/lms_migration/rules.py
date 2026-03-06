from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegexReplacement:
    description: str
    pattern: str
    replacement: str
    flags: str = ""


@dataclass(frozen=True)
class LinkRewrite:
    description: str
    source: str
    target: str


@dataclass(frozen=True)
class ManualTrigger:
    reason: str
    pattern: str
    flags: str = ""


@dataclass(frozen=True)
class BannerRule:
    enabled: bool
    html: str
    insert_mode: str = "prepend_body"


@dataclass(frozen=True)
class MigrationRules:
    replacements: tuple[RegexReplacement, ...]
    link_rewrites: tuple[LinkRewrite, ...]
    manual_review_triggers: tuple[ManualTrigger, ...]
    banner: BannerRule


def _as_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _parse_replacements(raw: list[dict[str, Any]]) -> tuple[RegexReplacement, ...]:
    parsed: list[RegexReplacement] = []
    for item in raw:
        pattern = _as_str(item.get("pattern")).strip()
        if not pattern:
            continue
        parsed.append(
            RegexReplacement(
                description=_as_str(item.get("description"), "Unnamed replacement"),
                pattern=pattern,
                replacement=_as_str(item.get("replacement")),
                flags=_as_str(item.get("flags")),
            )
        )
    return tuple(parsed)


def _parse_link_rewrites(raw: list[dict[str, Any]]) -> tuple[LinkRewrite, ...]:
    parsed: list[LinkRewrite] = []
    for item in raw:
        source = _as_str(item.get("from")).strip()
        target = _as_str(item.get("to")).strip()
        if not source or not target:
            continue
        parsed.append(
            LinkRewrite(
                description=_as_str(item.get("description"), f"Rewrite {source} -> {target}"),
                source=source,
                target=target,
            )
        )
    return tuple(parsed)


def _parse_manual_triggers(raw: list[dict[str, Any]]) -> tuple[ManualTrigger, ...]:
    parsed: list[ManualTrigger] = []
    for item in raw:
        pattern = _as_str(item.get("pattern")).strip()
        if not pattern:
            continue
        parsed.append(
            ManualTrigger(
                reason=_as_str(item.get("reason"), "Manual review required"),
                pattern=pattern,
                flags=_as_str(item.get("flags")),
            )
        )
    return tuple(parsed)


def _parse_banner(raw: dict[str, Any]) -> BannerRule:
    return BannerRule(
        enabled=bool(raw.get("enabled", False)),
        html=_as_str(raw.get("html")),
        insert_mode=_as_str(raw.get("insert_mode"), "prepend_body") or "prepend_body",
    )


def load_rules(path: Path) -> MigrationRules:
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    return MigrationRules(
        replacements=_parse_replacements(raw.get("replacements", [])),
        link_rewrites=_parse_link_rewrites(raw.get("link_rewrites", [])),
        manual_review_triggers=_parse_manual_triggers(raw.get("manual_review_triggers", [])),
        banner=_parse_banner(raw.get("banner", {})),
    )

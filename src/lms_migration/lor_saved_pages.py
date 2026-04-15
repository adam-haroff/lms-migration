from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import posixpath
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from .html_tools import (
    CanvasSanitizerPolicy,
    apply_accessibility_markup_fixes,
    apply_canvas_sanitizer,
)
from .template_merger import (
    _clean_d2l_scaffold,
    _extract_body,
    _extract_checklist_candidates,
    _fill_module_intro,
)
from .template_overlay import (
    TemplateOverlayConfig,
    apply_template_overlay,
    build_template_overlay_context,
    materialize_template_assets,
)

_HTML_SUFFIXES = {".html", ".htm"}
_IGNORED_FILE_NAMES = {".ds_store", "thumbs.db"}
_LOIDENT_RE = re.compile(r"\bloidentid=(\d+)\b", flags=re.IGNORECASE)
_TITLE_RE = re.compile(
    r"<title\b[^>]*>(?P<title>.*?)</title>", flags=re.IGNORECASE | re.DOTALL
)
_FIRST_HEADING_RE = re.compile(
    r"<h[1-6]\b[^>]*>(?P<body>.*?)</h[1-6]>", flags=re.IGNORECASE | re.DOTALL
)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_LOCAL_REF_RE = re.compile(
    r'(?P<prefix>\b(?:src|href)\s*=\s*["\'])(?P<url>[^"\']+)(?P<suffix>["\'])',
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class LorInventoryRow:
    module_section: str
    item_title: str
    href: str
    lo_ident_id: str | None


@dataclass(frozen=True)
class SavedLorPage:
    source_path: Path
    relative_path: str
    copy_roots: tuple[Path, ...]
    wrapper_path: Path | None
    checklist_source_path: Path | None
    module_section: str | None
    item_title: str
    display_title: str
    lo_ident_id: str | None
    matched_inventory: bool


@dataclass(frozen=True)
class LorRecoveryOutput:
    output_zip: Path
    report_json: Path
    report_markdown: Path


def _normalize_label(value: str) -> str:
    lowered = html.unescape(value).replace("\xa0", " ").lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", _STRIP_TAGS_RE.sub(" ", html.unescape(value))).strip()


def _extract_html_title(value: str) -> str:
    title_match = _TITLE_RE.search(value)
    if title_match is not None:
        text = _plain_text(title_match.group("title"))
        if text:
            return text
    heading_match = _FIRST_HEADING_RE.search(value)
    if heading_match is not None:
        text = _plain_text(heading_match.group("body"))
        if text:
            return text
    return ""


def _extract_lo_ident_id(value: str) -> str | None:
    match = _LOIDENT_RE.search(value)
    if match is not None:
        return match.group(1)
    return None


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Recovered Page"


def _simple_page_body(*, title: str, body_html: str) -> str:
    return body_html.strip()


def _strip_template_icon_refs(html_text: str) -> str:
    return re.sub(
        r"<img[^>]+template-images/icons/[^>]*>",
        "",
        html_text,
        flags=re.IGNORECASE,
    )


def _prepend_top_divider(html_text: str) -> str:
    divider = '<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">'
    if divider in html_text:
        return html_text
    return re.sub(
        r"(<body[^>]*>)",
        r"\1\n" + divider,
        html_text,
        count=1,
        flags=re.IGNORECASE,
    )


def _is_local_ref(url: str) -> bool:
    lowered = url.strip().lower()
    if not lowered:
        return False
    return not (
        lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:", "javascript:", "#", "/"))
    )


def _load_inventory_rows(csv_path: Path | None) -> list[LorInventoryRow]:
    if csv_path is None:
        return []

    rows: list[LorInventoryRow] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            material_type = str(row.get("material_type") or "").strip().lower()
            href = str(row.get("href") or "").strip()
            lo_ident_id = _extract_lo_ident_id(href)
            if material_type != "contentlink" or not lo_ident_id:
                continue
            rows.append(
                LorInventoryRow(
                    module_section=str(row.get("module_section") or "").strip(),
                    item_title=str(row.get("item_title") or "").strip(),
                    href=href,
                    lo_ident_id=lo_ident_id,
                )
            )
    return rows


def _discover_html_pages(input_dir: Path) -> list[Path]:
    pages: list[Path] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() in _IGNORED_FILE_NAMES:
            continue
        if path.suffix.lower() not in _HTML_SUFFIXES:
            continue
        pages.append(path)
    return pages


@dataclass(frozen=True)
class _SavedHtmlCandidate:
    content_path: Path
    wrapper_path: Path | None
    copy_roots: tuple[Path, ...]


def _is_within_saved_page_folder(path: Path, input_dir: Path) -> bool:
    try:
        rel = path.relative_to(input_dir)
    except ValueError:
        return False
    return any(part.lower().endswith("_files") for part in rel.parts[:-1])


def _discover_saved_html_candidates(input_dir: Path) -> list[_SavedHtmlCandidate]:
    candidates: list[_SavedHtmlCandidate] = []
    used_content_paths: set[Path] = set()
    used_wrapper_paths: set[Path] = set()

    top_level_html = [
        path
        for path in sorted(input_dir.glob("*.html"))
        if path.is_file() and path.name.lower() not in _IGNORED_FILE_NAMES
    ]

    for wrapper_path in top_level_html:
        sibling_dir = input_dir / f"{wrapper_path.stem}_files"
        if sibling_dir.is_dir():
            inner_htmls = sorted(
                path
                for path in sibling_dir.glob("*.html")
                if path.is_file() and path.name.lower() not in _IGNORED_FILE_NAMES
            )
            if len(inner_htmls) == 1:
                content_path = inner_htmls[0]
                candidates.append(
                    _SavedHtmlCandidate(
                        content_path=content_path,
                        wrapper_path=wrapper_path,
                        copy_roots=(sibling_dir,),
                    )
                )
                used_content_paths.add(content_path.resolve())
                used_wrapper_paths.add(wrapper_path.resolve())
                continue

        copy_roots: list[Path] = [wrapper_path]
        if sibling_dir.is_dir():
            copy_roots.append(sibling_dir)
        candidates.append(
            _SavedHtmlCandidate(
                content_path=wrapper_path,
                wrapper_path=None,
                copy_roots=tuple(copy_roots),
            )
        )
        used_content_paths.add(wrapper_path.resolve())
        used_wrapper_paths.add(wrapper_path.resolve())

    for path in _discover_html_pages(input_dir):
        resolved = path.resolve()
        if resolved in used_content_paths or resolved in used_wrapper_paths:
            continue
        if _is_within_saved_page_folder(path, input_dir):
            candidates.append(
                _SavedHtmlCandidate(
                    content_path=path,
                    wrapper_path=None,
                    copy_roots=(path.parent,),
                )
            )
            used_content_paths.add(resolved)
            continue
        sibling_dir = path.with_name(f"{path.stem}_files")
        copy_roots: list[Path] = [path]
        if sibling_dir.is_dir():
            copy_roots.append(sibling_dir)
        candidates.append(
            _SavedHtmlCandidate(
                content_path=path,
                wrapper_path=None,
                copy_roots=tuple(copy_roots),
            )
        )
        used_content_paths.add(resolved)

    return candidates


def _infer_inventory_row(
    *,
    path: Path,
    input_dir: Path,
    html_text: str,
    inventory_rows: list[LorInventoryRow],
    lo_ident_id_hint: str | None = None,
) -> LorInventoryRow | None:
    if not inventory_rows:
        return None

    relative_path = path.relative_to(input_dir).as_posix()
    direct_lo_ident_id = (
        lo_ident_id_hint
        or _extract_lo_ident_id(relative_path)
        or _extract_lo_ident_id(html_text)
    )
    if direct_lo_ident_id:
        for row in inventory_rows:
            if row.lo_ident_id == direct_lo_ident_id:
                return row

    rel_norm = _normalize_label(relative_path)
    parent_norm = _normalize_label(path.parent.name)
    stem_norm = _normalize_label(path.stem)
    title_norm = _normalize_label(_extract_html_title(html_text))
    candidate_name_tokens = {token for token in {stem_norm, title_norm} if token}

    scored: list[tuple[int, LorInventoryRow]] = []
    for row in inventory_rows:
        title_match = _normalize_label(row.item_title)
        if title_match not in candidate_name_tokens:
            continue

        score = 10
        section_match = _normalize_label(row.module_section)
        if section_match and section_match in rel_norm:
            score += 10
        elif section_match and section_match == parent_norm:
            score += 8
        scored.append((score, row))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _resolve_saved_pages(
    *,
    input_dir: Path,
    inventory_rows: list[LorInventoryRow],
) -> list[SavedLorPage]:
    working: list[tuple[_SavedHtmlCandidate, str, LorInventoryRow | None]] = []

    for candidate in _discover_saved_html_candidates(input_dir):
        html_text = candidate.content_path.read_text(encoding="utf-8", errors="ignore")
        wrapper_text = (
            candidate.wrapper_path.read_text(encoding="utf-8", errors="ignore")
            if candidate.wrapper_path is not None
            else ""
        )
        lo_ident_id_hint = _extract_lo_ident_id(wrapper_text) or _extract_lo_ident_id(
            html_text
        )
        row = _infer_inventory_row(
            path=candidate.content_path,
            input_dir=input_dir,
            html_text=f"{wrapper_text}\n{html_text}",
            inventory_rows=inventory_rows,
            lo_ident_id_hint=lo_ident_id_hint,
        )
        title = (
            row.item_title
            if row is not None
            else (_extract_html_title(html_text) or candidate.content_path.stem)
        )
        working.append((candidate, title, row))

    def _display_section_title(value: str | None) -> str | None:
        if not value:
            return None
        return value.split(" > ")[-1].strip() or value.strip()

    pages: list[SavedLorPage] = []
    for candidate, title, row in working:
        relative_path = candidate.content_path.relative_to(input_dir).as_posix()
        module_section = _display_section_title(
            row.module_section if row is not None else candidate.content_path.parent.name
        )
        pages.append(
            SavedLorPage(
                source_path=candidate.content_path,
                relative_path=relative_path,
                copy_roots=candidate.copy_roots,
                wrapper_path=candidate.wrapper_path,
                checklist_source_path=None,
                module_section=module_section or None,
                item_title=title,
                display_title=title,
                lo_ident_id=(
                    row.lo_ident_id
                    if row is not None
                    else (
                        _extract_lo_ident_id(
                            candidate.wrapper_path.read_text(
                                encoding="utf-8", errors="ignore"
                            )
                        )
                        if candidate.wrapper_path is not None
                        else None
                    )
                ),
                matched_inventory=row is not None,
            )
        )

    by_section_and_title: dict[tuple[str | None, str], SavedLorPage] = {
        (page.module_section, page.item_title): page for page in pages
    }
    merged_keys: set[tuple[str | None, str]] = set()
    merged_pages: list[SavedLorPage] = []

    for page in pages:
        page_key = (page.module_section, page.item_title)
        if page_key in merged_keys:
            continue
        if page.item_title == "Activities Checklist":
            intro_key = (page.module_section, "Introduction and Objectives")
            if intro_key in by_section_and_title:
                continue
        if page.item_title != "Introduction and Objectives":
            merged_pages.append(page)
            merged_keys.add(page_key)
            continue

        checklist_key = (page.module_section, "Activities Checklist")
        checklist_page = by_section_and_title.get(checklist_key)
        if checklist_page is None:
            merged_pages.append(page)
            merged_keys.add(page_key)
            continue

        merged_pages.append(
            SavedLorPage(
                source_path=page.source_path,
                relative_path=page.relative_path,
                copy_roots=page.copy_roots,
                wrapper_path=page.wrapper_path,
                checklist_source_path=checklist_page.source_path,
                module_section=page.module_section,
                item_title="Introduction and Checklist",
                display_title="Introduction and Checklist",
                lo_ident_id=page.lo_ident_id,
                matched_inventory=page.matched_inventory or checklist_page.matched_inventory,
            )
        )
        merged_keys.add(page_key)
        merged_keys.add(checklist_key)

    normalized_pages: list[SavedLorPage] = []
    for page in merged_pages:
        section_component = _safe_path_component(page.module_section or "Recovered Pages")
        title_component = _safe_path_component(page.display_title)
        relative_path = f"{section_component}/{title_component}.html"
        normalized_pages.append(
            SavedLorPage(
                source_path=page.source_path,
                relative_path=relative_path,
                copy_roots=page.copy_roots,
                wrapper_path=page.wrapper_path,
                checklist_source_path=page.checklist_source_path,
                module_section=page.module_section,
                item_title=page.item_title,
                display_title=page.display_title,
                lo_ident_id=page.lo_ident_id,
                matched_inventory=page.matched_inventory,
            )
        )

    return normalized_pages


def _build_minimal_html_document(*, title: str, body_html: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '  <meta charset="utf-8">\n'
        f"  <title>{html.escape(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html.strip()}\n"
        "</body>\n"
        "</html>\n"
    )


def _zip_directory(source_dir: Path, output_zip: Path) -> None:
    with ZipFile(output_zip, "w", compression=ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(source_dir))


def _rewrite_and_copy_local_refs(
    *,
    html_text: str,
    source_dir: Path,
    staged_html_path: Path,
    staged_root: Path,
    shared_asset_root: Path,
    digest_to_relative_path: dict[str, str],
    used_asset_names: set[str],
) -> str:
    copied_assets: dict[str, str] = {}
    page_parent_rel = staged_html_path.parent.relative_to(staged_root).as_posix()
    start_dir = page_parent_rel if page_parent_rel else "."

    def _replacement(match: re.Match) -> str:
        original_url = match.group("url")
        if not _is_local_ref(original_url):
            return match.group(0)
        source_asset = (source_dir / original_url).resolve()
        if not source_asset.exists() or not source_asset.is_file():
            return match.group(0)
        if original_url in copied_assets:
            new_url = copied_assets[original_url]
        else:
            digest = hashlib.sha256(source_asset.read_bytes()).hexdigest()
            shared_relative = digest_to_relative_path.get(digest)
            if shared_relative is None:
                basename = _safe_path_component(source_asset.stem) + source_asset.suffix.lower()
                candidate = basename
                counter = 2
                while candidate.lower() in used_asset_names:
                    candidate = f"{_safe_path_component(source_asset.stem)}-{counter}{source_asset.suffix.lower()}"
                    counter += 1
                used_asset_names.add(candidate.lower())
                destination = shared_asset_root / candidate
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_asset, destination)
                shared_relative = destination.relative_to(staged_root).as_posix()
                digest_to_relative_path[digest] = shared_relative
            new_url = posixpath.relpath(shared_relative, start=start_dir)
            copied_assets[original_url] = new_url
        return f"{match.group('prefix')}{new_url}{match.group('suffix')}"

    return _LOCAL_REF_RE.sub(_replacement, html_text)


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_manifest(
    *,
    package_title: str,
    module_title: str,
    pages: list[SavedLorPage],
    staged_root: Path,
) -> str:
    lines: list[str] = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1" '
        'xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0" '
        'xmlns:imsmd="http://www.imsglobal.org/xsd/imsmd_rootv1p2p1" '
        'xmlns:lom="http://ltsc.ieee.org/xsd/LOM" '
        'identifier="D2L_SAVED_LOR_PAGE_RECOVERY">',
        "  <metadata>",
        "    <imsmd:lom>",
        "      <imsmd:general>",
        "        <imsmd:title>",
        f'          <imsmd:langstring xml:lang="en-us">{_escape_xml(package_title)}</imsmd:langstring>',
        "        </imsmd:title>",
        "        <imsmd:language>en-us</imsmd:language>",
        "      </imsmd:general>",
        "    </imsmd:lom>",
        "  </metadata>",
        '  <organizations default="d2l_orgs">',
        '    <organization identifier="d2l_org">',
        '      <item identifier="9000" identifierref="RES_RECOVERY_MODULE" d2l_2p0:id="9000" description="" completion_type="2">',
        f"        <title>{_escape_xml(module_title)}</title>",
    ]

    resources: list[str] = [
        '    <resource identifier="RES_RECOVERY_MODULE" type="webcontent" '
        'd2l_2p0:material_type="contentmodule" d2l_2p0:link_target="" href="" title="" />'
    ]

    by_section: dict[str, list[SavedLorPage]] = defaultdict(list)
    for page in pages:
        by_section[page.module_section or "Recovered Pages"].append(page)

    page_counter = 1
    section_counter = 1
    for section_title, section_pages in by_section.items():
        section_identifier = f"910{section_counter}"
        section_resource = f"RES_RECOVERY_SECTION_{section_counter}"
        lines.extend(
            [
                f'        <item identifier="{section_identifier}" identifierref="{section_resource}" d2l_2p0:id="{section_identifier}" description="" completion_type="2">',
                f"          <title>{_escape_xml(section_title)}</title>",
            ]
        )
        resources.append(
            f'    <resource identifier="{section_resource}" type="webcontent" '
            f'd2l_2p0:material_type="contentmodule" d2l_2p0:link_target="" href="" title="{_escape_xml(section_title)}" />'
        )
        for page in section_pages:
            page_identifier = f"920{page_counter}"
            page_resource = f"RES_RECOVERY_PAGE_{page_counter}"
            lines.extend(
                [
                    f'          <item identifier="{page_identifier}" identifierref="{page_resource}" d2l_2p0:id="{page_identifier}" description="" completion_type="2">',
                    f"            <title>{_escape_xml(page.display_title)}</title>",
                    "          </item>",
                ]
            )
            resources.append(
                f'    <resource identifier="{page_resource}" type="webcontent" '
                f'd2l_2p0:material_type="content" d2l_2p0:link_target="" href="{_escape_xml(page.relative_path)}" title="" />'
            )
            page_counter += 1
        lines.append("        </item>")
        section_counter += 1

    lines.extend(["      </item>", "    </organization>", "  </organizations>", "  <resources>"])

    file_counter = 1
    for file_path in sorted(staged_root.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(staged_root).as_posix()
        if relative_path == "imsmanifest.xml" or file_path.suffix.lower() in _HTML_SUFFIXES:
            continue
        resources.append(
            f'    <resource identifier="RES_RECOVERY_FILE_{file_counter}" type="webcontent" '
            f'd2l_2p0:material_type="content" d2l_2p0:link_target="" href="{_escape_xml(relative_path)}" title="" />'
        )
        file_counter += 1

    lines.extend(resources)
    lines.extend(["  </resources>", "</manifest>", ""])
    return "\n".join(lines)


def build_saved_lor_pages_recovery_package(
    *,
    input_dir: Path,
    output_dir: Path,
    inventory_csv: Path | None = None,
    package_title: str = "Recovered LOR Pages",
    module_title: str = "Recovered LOR Pages",
    template_package: Path | None = None,
    template_alias_map_json: Path | None = None,
) -> LorRecoveryOutput:
    if not input_dir.exists():
        raise ValueError(f"Input directory does not exist: {input_dir}")

    inventory_rows = _load_inventory_rows(inventory_csv)
    pages = _resolve_saved_pages(input_dir=input_dir, inventory_rows=inventory_rows)
    if not pages:
        raise ValueError(f"No HTML pages found under: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "saved-lor-pages-recovery.zip"
    report_json = output_dir / "saved-lor-pages-recovery.json"
    report_md = output_dir / "saved-lor-pages-recovery.md"

    overlay_context = None
    if template_package is not None:
        if not template_package.exists():
            raise ValueError(f"Template package does not exist: {template_package}")
        if (
            template_alias_map_json is not None
            and not template_alias_map_json.exists()
        ):
            raise ValueError(
                f"Template alias map JSON does not exist: {template_alias_map_json}"
            )
        overlay_context = build_template_overlay_context(
            TemplateOverlayConfig(
                template_package=template_package,
                alias_map_json=template_alias_map_json,
                use_template_web_resources=False,
                apply_visual_standards=True,
                apply_color_standards=True,
                apply_divider_standards=True,
                image_layout_mode="safe-block",
            )
        )

    overlay_summaries: list[dict] = []
    overlay_total_mapped = 0
    overlay_total_unresolved = 0

    with TemporaryDirectory(prefix="lor-recovery-") as tmp_dir:
        staged_root = Path(tmp_dir) / "package"
        staged_root.mkdir(parents=True, exist_ok=True)
        shared_asset_root = staged_root / "course-content" / "course-images"
        digest_to_relative_path: dict[str, str] = {}
        used_asset_names: set[str] = set()

        if overlay_context is not None:
            materialize_template_assets(
                context=overlay_context,
                destination_root=staged_root,
            )

        for page in pages:
            staged_html = staged_root / page.relative_path
            staged_html.parent.mkdir(parents=True, exist_ok=True)
            raw_html = page.source_path.read_text(encoding="utf-8", errors="ignore")
            if page.checklist_source_path is not None:
                checklist_html = page.checklist_source_path.read_text(
                    encoding="utf-8", errors="ignore"
                )
                checklist_items = _extract_checklist_candidates(
                    _clean_d2l_scaffold(_extract_body(checklist_html))
                )
                rebuilt = _fill_module_intro(
                    raw_html,
                    module_number=None,
                    chapter_title=page.module_section or "Recovered Topic",
                    path_seed=page.relative_path,
                    extra_checklist_items=checklist_items,
                    use_template_web_resources=False,
                )
                rebuilt = _build_minimal_html_document(
                    title=page.display_title,
                    body_html=_extract_body(rebuilt),
                )
                if overlay_context is None:
                    rebuilt = _strip_template_icon_refs(rebuilt)
            else:
                body_html = _clean_d2l_scaffold(_extract_body(raw_html))
                body_html = _simple_page_body(
                    title=page.display_title,
                    body_html=body_html,
                )
                rebuilt = _build_minimal_html_document(
                    title=page.display_title,
                    body_html=body_html,
                )

            if overlay_context is not None:
                (
                    rebuilt,
                    _overlay_changes,
                    _overlay_issues,
                    overlay_summary,
                ) = apply_template_overlay(
                    rebuilt,
                    file_path=page.relative_path,
                    context=overlay_context,
                )
                overlay_summaries.append(overlay_summary)
                overlay_total_mapped += int(overlay_summary.get("mapped_direct", 0)) + int(
                    overlay_summary.get("mapped_alias", 0)
                )
                overlay_total_unresolved += int(overlay_summary.get("unresolved", 0))

            rebuilt, _sanitizer_changes = apply_canvas_sanitizer(
                rebuilt,
                policy=CanvasSanitizerPolicy(),
                file_path=page.relative_path,
            )
            rebuilt, _a11y_changes = apply_accessibility_markup_fixes(
                rebuilt,
                repair_heading_jumps=True,
            )
            if page.checklist_source_path is None:
                rebuilt = _prepend_top_divider(rebuilt)
            rebuilt = _rewrite_and_copy_local_refs(
                html_text=rebuilt,
                source_dir=page.source_path.parent,
                staged_html_path=staged_html,
                staged_root=staged_root,
                shared_asset_root=shared_asset_root,
                digest_to_relative_path=digest_to_relative_path,
                used_asset_names=used_asset_names,
            )
            staged_html.write_text(rebuilt, encoding="utf-8")

        manifest = _build_manifest(
            package_title=package_title,
            module_title=module_title,
            pages=pages,
            staged_root=staged_root,
        )
        (staged_root / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
        _zip_directory(staged_root, zip_path)

    summary = {
        "input_dir": str(input_dir),
        "inventory_csv": str(inventory_csv) if inventory_csv is not None else None,
        "package_title": package_title,
        "module_title": module_title,
        "pages_found": len(pages),
        "pages_matched_to_inventory": sum(1 for page in pages if page.matched_inventory),
        "sections_found": len({page.module_section for page in pages}),
        "template_overlay_enabled": overlay_context is not None,
        "template_overlay_mapped_refs": overlay_total_mapped,
        "template_overlay_unresolved_refs": overlay_total_unresolved,
        "shared_image_count": len(digest_to_relative_path),
        "pages": [
            {
                "relative_path": page.relative_path,
                "wrapper_path": (
                    page.wrapper_path.relative_to(input_dir).as_posix()
                    if page.wrapper_path is not None
                    else None
                ),
                "checklist_source_path": (
                    page.checklist_source_path.relative_to(input_dir).as_posix()
                    if page.checklist_source_path is not None
                    else None
                ),
                "module_section": page.module_section,
                "item_title": page.item_title,
                "display_title": page.display_title,
                "lo_ident_id": page.lo_ident_id,
                "matched_inventory": page.matched_inventory,
            }
            for page in pages
        ],
        "overlay_summaries": overlay_summaries,
    }
    report_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Saved LOR Pages Recovery",
        "",
        f"- Input directory: `{input_dir}`",
        f"- Output zip: `{zip_path.name}`",
        f"- Pages found: {summary['pages_found']}",
        f"- Pages matched to inventory: {summary['pages_matched_to_inventory']}",
        f"- Sections found: {summary['sections_found']}",
        f"- Shared images copied: {summary['shared_image_count']}",
        f"- Template overlay enabled: {'yes' if overlay_context is not None else 'no'}",
    ]
    if overlay_context is not None:
        lines.extend(
            [
                f"- Template refs mapped: {overlay_total_mapped}",
                f"- Template refs unresolved: {overlay_total_unresolved}",
            ]
        )
    lines.extend(
        [
            "",
            "## Included Pages",
            "",
        ]
    )
    for page in pages:
        matched = "matched" if page.matched_inventory else "unmatched"
        section = page.module_section or "Recovered Pages"
        lines.append(
            f"- `{section}` → `{page.display_title}` ({matched}; `{page.relative_path}`)"
        )
    lines.extend(
        [
            "",
            "## Import",
            "",
            "1. In Canvas, open `Settings` → `Import Course Content`.",
            "2. Choose `D2L export .zip format`.",
            f"3. Import `{zip_path.name}`.",
            "4. Review the recovered pages and move them into the intended modules.",
            "",
            "## Saving Guidance",
            "",
            "- Browser default Save Page As output is supported, including top-level wrapper `.html` files with sibling `*_files` folders.",
            "- Per-theory subfolders are also supported if you prefer a tidier staging area.",
            "- `Web Page, Complete` is preferred so any local page assets come along with the HTML file.",
            "- When both `Introduction and Objectives` and `Activities Checklist` are present for the same theory, the recovery package combines them into one `Introduction and Checklist` page.",
            "",
        ]
    )
    report_md.write_text("\n".join(lines), encoding="utf-8")

    return LorRecoveryOutput(
        output_zip=zip_path,
        report_json=report_json,
        report_markdown=report_md,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-build-lor-recovery",
        description=(
            "Convert a folder of browser-saved D2L LOR pages into a small D2L "
            "recovery package that imports those pages into Canvas."
        ),
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing saved LOR HTML pages (recursive scan).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/lor-recovery"),
        help="Directory for the generated recovery package and reports.",
    )
    parser.add_argument(
        "--inventory-csv",
        type=Path,
        default=None,
        help="Optional inventory CSV (for example com-2220-contentlink-inventory.csv) used to preserve titles/sections.",
    )
    parser.add_argument(
        "--package-title",
        type=str,
        default="Recovered LOR Pages",
        help="Package title written into imsmanifest metadata.",
    )
    parser.add_argument(
        "--module-title",
        type=str,
        default="Recovered LOR Pages",
        help="Top-level recovery module title used in the import package.",
    )
    parser.add_argument(
        "--template-package",
        type=Path,
        default=None,
        help="Optional Canvas template export package (.imscc) for template asset remapping.",
    )
    parser.add_argument(
        "--template-alias-map-json",
        type=Path,
        default=None,
        help="Optional alias map JSON used with --template-package.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    result = build_saved_lor_pages_recovery_package(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        inventory_csv=args.inventory_csv,
        package_title=args.package_title,
        module_title=args.module_title,
        template_package=args.template_package,
        template_alias_map_json=args.template_alias_map_json,
    )
    print(f"Saved LOR recovery zip: {result.output_zip}")
    print(f"Saved LOR recovery JSON: {result.report_json}")
    print(f"Saved LOR recovery Markdown: {result.report_markdown}")


if __name__ == "__main__":
    main()

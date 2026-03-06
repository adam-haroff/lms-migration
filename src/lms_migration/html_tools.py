from __future__ import annotations

import html
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

from .rules import BannerRule, LinkRewrite, ManualTrigger, RegexReplacement


@dataclass(frozen=True)
class AppliedChange:
    category: str
    description: str
    count: int


@dataclass(frozen=True)
class ManualReviewIssue:
    reason: str
    evidence: str


@dataclass(frozen=True)
class TemplateCheckPolicy:
    check_instructor_notes: bool = True
    check_template_placeholders: bool = True
    check_legacy_quiz_wording: bool = True
    require_mc_closing_bullet: bool = True


@dataclass(frozen=True)
class CanvasSanitizerPolicy:
    sanitize_brightspace_assets: bool = True
    neutralize_legacy_d2l_links: bool = True
    use_alt_text_for_removed_template_images: bool = True
    repair_missing_local_references: bool = True


@dataclass(frozen=True)
class BestPracticeEnforcerPolicy:
    enabled: bool = False
    enforce_module_checklist_closer: bool = False
    ensure_external_links_new_tab: bool = False


_BSP_TEMPLATE_RE = re.compile(r"^/?shared/brightspace_html_template/", flags=re.IGNORECASE)
_BSP_FONT_RE = re.compile(r"^https?://s\.brightspace\.com/", flags=re.IGNORECASE)
_LEGACY_D2L_RE = re.compile(r"^/?d2l/", flags=re.IGNORECASE)
_LEGACY_ENFORCED_RE = re.compile(r"^/?content/enforced/", flags=re.IGNORECASE)
_BOOTSTRAP_GRID_CLASS_RE = re.compile(
    r"^(?:container(?:-fluid)?|row|col(?:-[a-z]+)?-\d{1,2}|offset(?:-[a-z]+)?-\d{1,2})$",
    flags=re.IGNORECASE,
)


def _re_flags(flag_string: str) -> int:
    flags = 0
    lowered = flag_string.lower()
    if "i" in lowered:
        flags |= re.IGNORECASE
    if "m" in lowered:
        flags |= re.MULTILINE
    if "s" in lowered:
        flags |= re.DOTALL
    return flags


def apply_replacements(content: str, replacements: Iterable[RegexReplacement]) -> tuple[str, list[AppliedChange]]:
    updated = content
    applied: list[AppliedChange] = []

    for replacement in replacements:
        pattern = re.compile(replacement.pattern, flags=_re_flags(replacement.flags))
        updated, count = pattern.subn(replacement.replacement, updated)
        if count:
            applied.append(
                AppliedChange(category="replacement", description=replacement.description, count=count)
            )

    return updated, applied


def apply_link_rewrites(content: str, rewrites: Iterable[LinkRewrite]) -> tuple[str, list[AppliedChange]]:
    updated = content
    applied: list[AppliedChange] = []

    for rewrite in rewrites:
        count = updated.count(rewrite.source)
        if count:
            updated = updated.replace(rewrite.source, rewrite.target)
            applied.append(
                AppliedChange(category="link_rewrite", description=rewrite.description, count=count)
            )

    return updated, applied


def apply_banner_rule(content: str, banner_rule: BannerRule) -> tuple[str, list[AppliedChange]]:
    if not banner_rule.enabled or not banner_rule.html.strip():
        return content, []

    updated = content
    lower_content = content.lower()
    if banner_rule.html.strip().lower() in lower_content:
        return content, []

    if banner_rule.insert_mode == "prepend_body":
        body_pattern = re.compile(r"<body[^>]*>", flags=re.IGNORECASE)
        body_match = body_pattern.search(content)
        if body_match:
            updated = (
                content[: body_match.end()]
                + "\n"
                + banner_rule.html
                + "\n"
                + content[body_match.end() :]
            )
        else:
            updated = banner_rule.html + "\n" + content
    else:
        updated = banner_rule.html + "\n" + content

    return updated, [AppliedChange(category="template", description="Injected course template banner", count=1)]


def _is_brightspace_template_ref(url: str) -> bool:
    cleaned = url.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned:
        return False
    return bool(_BSP_TEMPLATE_RE.match(cleaned) or _BSP_FONT_RE.match(cleaned))


def _is_legacy_d2l_link(url: str) -> bool:
    cleaned = url.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned:
        return False
    return bool(_LEGACY_D2L_RE.match(cleaned) or _LEGACY_ENFORCED_RE.match(cleaned))


def apply_canvas_sanitizer(
    content: str,
    policy: CanvasSanitizerPolicy | None = None,
) -> tuple[str, list[AppliedChange]]:
    """
    Apply local HTML cleanup to reduce Canvas import warnings from legacy D2L/Brightspace refs.
    """
    applied_policy = policy or CanvasSanitizerPolicy()
    updated = content
    applied: list[AppliedChange] = []

    if applied_policy.sanitize_brightspace_assets:
        class_attr_pattern = re.compile(
            r'(?P<prefix>\sclass\s*=\s*)(?P<quote>["\'])(?P<classes>[^"\']*)(?P=quote)',
            flags=re.IGNORECASE,
        )
        stripped_grid_tokens = 0

        def replace_class_attr(match: re.Match[str]) -> str:
            nonlocal stripped_grid_tokens
            classes_text = match.group("classes").strip()
            if not classes_text:
                return ""
            original_tokens = [token for token in classes_text.split() if token]
            kept_tokens = [token for token in original_tokens if not _BOOTSTRAP_GRID_CLASS_RE.match(token)]
            removed = len(original_tokens) - len(kept_tokens)
            if removed <= 0:
                return match.group(0)
            stripped_grid_tokens += removed
            if not kept_tokens:
                return ""
            return f'{match.group("prefix")}"{" ".join(kept_tokens)}"'

        updated = class_attr_pattern.sub(replace_class_attr, updated)
        if stripped_grid_tokens:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed Bootstrap grid classes that conflict with Canvas layout",
                    count=stripped_grid_tokens,
                )
            )

        link_pattern = re.compile(
            r"<link\b[^>]*\bhref\s*=\s*([\"'])(?P<href>[^\"']+)\1[^>]*>",
            flags=re.IGNORECASE,
        )
        removed_link_tags = 0

        def replace_link_tag(match: re.Match[str]) -> str:
            nonlocal removed_link_tags
            href = match.group("href").strip()
            if _is_brightspace_template_ref(href):
                removed_link_tags += 1
                return ""
            return match.group(0)

        updated = link_pattern.sub(replace_link_tag, updated)
        if removed_link_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template stylesheet references",
                    count=removed_link_tags,
                )
            )

        script_pattern = re.compile(
            r"<script\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>\s*</script>",
            flags=re.IGNORECASE | re.DOTALL,
        )
        removed_script_tags = 0

        def replace_script_tag(match: re.Match[str]) -> str:
            nonlocal removed_script_tags
            src = match.group("src").strip()
            if _is_brightspace_template_ref(src):
                removed_script_tags += 1
                return ""
            return match.group(0)

        updated = script_pattern.sub(replace_script_tag, updated)
        if removed_script_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template script references",
                    count=removed_script_tags,
                )
            )

        img_pattern = re.compile(
            r"<img\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>",
            flags=re.IGNORECASE,
        )
        removed_img_tags = 0
        replaced_img_with_alt = 0

        def replace_img_tag(match: re.Match[str]) -> str:
            nonlocal removed_img_tags
            nonlocal replaced_img_with_alt
            src = match.group("src").strip()
            if not _is_brightspace_template_ref(src):
                return match.group(0)

            removed_img_tags += 1
            tag = match.group(0)
            if not applied_policy.use_alt_text_for_removed_template_images:
                return ""

            alt_match = re.search(
                r"\balt\s*=\s*([\"'])(?P<alt>.*?)\1",
                tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
            alt_text = alt_match.group("alt").strip() if alt_match else ""
            if not alt_text:
                return ""
            if alt_text.lower() in {"banner", "logo", "image", "decorative"}:
                return ""

            replaced_img_with_alt += 1
            escaped_alt = html.escape(alt_text, quote=False)
            return f'<span class="migration-template-image-text">{escaped_alt}</span>'

        updated = img_pattern.sub(replace_img_tag, updated)
        if removed_img_tags:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Removed missing Brightspace template image references",
                    count=removed_img_tags,
                )
            )
        if replaced_img_with_alt:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Replaced removed template images with existing alt text",
                    count=replaced_img_with_alt,
                )
            )

        # Brightspace template anchors often point to /shared/Brightspace_HTML_Template/*
        # and trigger Canvas missing-link warnings during import.
        anchor_template_pattern = re.compile(
            r'(<a\b[^>]*\bhref\s*=\s*)([\"\'])(?P<href>[^\"\']+)\2',
            flags=re.IGNORECASE,
        )
        neutralized_template_links = 0

        def replace_template_anchor_href(match: re.Match[str]) -> str:
            nonlocal neutralized_template_links
            href = match.group("href").strip()
            if not _is_brightspace_template_ref(href):
                return match.group(0)

            neutralized_template_links += 1
            escaped_href = html.escape(href, quote=True)
            prefix = match.group(1)
            return (
                f'{prefix}"#" '
                f'data-migration-link-status="needs-review" '
                f'data-migration-link-reason="brightspace-template-link" '
                f'data-migration-original-href="{escaped_href}"'
            )

        updated = anchor_template_pattern.sub(replace_template_anchor_href, updated)
        if neutralized_template_links:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Neutralized Brightspace template anchor links",
                    count=neutralized_template_links,
                )
            )

    if applied_policy.neutralize_legacy_d2l_links:
        anchor_href_pattern = re.compile(
            r'(<a\b[^>]*\bhref\s*=\s*)([\"\'])(?P<href>[^\"\']+)\2',
            flags=re.IGNORECASE,
        )
        neutralized_links = 0

        def replace_anchor_href(match: re.Match[str]) -> str:
            nonlocal neutralized_links
            href = match.group("href").strip()
            if not _is_legacy_d2l_link(href):
                return match.group(0)

            neutralized_links += 1
            escaped_href = html.escape(href, quote=True)
            prefix = match.group(1)
            return (
                f'{prefix}"#" '
                f'data-migration-link-status="needs-review" '
                f'data-migration-original-href="{escaped_href}"'
            )

        updated = anchor_href_pattern.sub(replace_anchor_href, updated)
        if neutralized_links:
            applied.append(
                AppliedChange(
                    category="sanitizer",
                    description="Neutralized legacy D2L links requiring manual relink in Canvas",
                    count=neutralized_links,
                )
            )

    return updated, applied


def repair_missing_local_references(
    content: str,
    *,
    file_path: str,
    available_paths: Iterable[str],
    keep_alt_text_for_missing_images: bool = True,
) -> tuple[str, list[AppliedChange]]:
    """
    Repair or neutralize local references that don't map to files in the package.
    """
    available_set = {str(path).strip().replace("\\", "/").lstrip("/") for path in available_paths}
    if not available_set:
        return content, []

    lower_map: dict[str, str] = {}
    by_dir_and_tail: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_basename: dict[str, list[str]] = defaultdict(list)
    for path in available_set:
        lowered = path.lower()
        lower_map.setdefault(lowered, path)
        directory = posixpath.dirname(path).lower()
        basename = posixpath.basename(path)
        by_basename[basename.lower()].append(path)
        if "_" in basename:
            tail = basename.split("_", 1)[1].lower()
            by_dir_and_tail[(directory, tail)].append(path)

    rewired_local_refs = 0
    neutralized_anchor_refs = 0
    removed_missing_images = 0
    replaced_missing_images_with_alt = 0

    def resolve_candidate(raw_url: str) -> tuple[str, str]:
        """Return (status, target_path_or_empty)."""
        parsed = urlparse(raw_url)
        if parsed.scheme or raw_url.startswith("//"):
            return ("keep", "")
        path_text = unquote(parsed.path).strip()
        if not path_text:
            return ("keep", "")
        if raw_url.strip().startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return ("keep", "")

        current_dir = posixpath.dirname(file_path)
        if path_text.startswith("/"):
            normalized = posixpath.normpath(path_text.lstrip("/"))
        else:
            normalized = posixpath.normpath(posixpath.join(current_dir, path_text))
        normalized = normalized.lstrip("./")
        lowered = normalized.lower()

        if normalized in available_set:
            return ("rewrite", normalized)
        if lowered in lower_map:
            return ("rewrite", lower_map[lowered])

        target_dir = posixpath.dirname(normalized).lower()
        target_basename = posixpath.basename(normalized)

        if target_basename:
            exact_name_matches = by_basename.get(target_basename.lower(), [])
            if len(exact_name_matches) == 1:
                return ("rewrite", exact_name_matches[0])

        if "_" in target_basename:
            tail = target_basename.split("_", 1)[1].lower()
            tail_matches = by_dir_and_tail.get((target_dir, tail), [])
            if len(tail_matches) == 1:
                return ("rewrite", tail_matches[0])
            if not tail_matches:
                # Try cross-directory unique tail match as fallback.
                all_tail_matches = [
                    path
                    for (dir_key, tail_key), paths in by_dir_and_tail.items()
                    if tail_key == tail
                    for path in paths
                ]
                if len(all_tail_matches) == 1:
                    return ("rewrite", all_tail_matches[0])

        return ("missing", normalized)

    def _strip_is_course_file_query(query: str) -> str:
        if not query:
            return ""
        pairs = parse_qsl(query, keep_blank_values=True)
        if not pairs:
            return query
        filtered = [(k, v) for (k, v) in pairs if k.lower() != "iscoursefile"]
        if len(filtered) == len(pairs):
            return query
        if not filtered:
            return ""
        return urlencode(filtered, doseq=True)

    def rebuild_url(raw_url: str, target_path: str) -> str:
        parsed = urlparse(raw_url)
        current_dir = posixpath.dirname(file_path).strip().replace("\\", "/").lstrip("./")
        if current_dir:
            rebuilt_path = posixpath.relpath(target_path, start=current_dir)
        else:
            rebuilt_path = target_path
        rebuilt_path = rebuilt_path.replace("\\", "/")
        if rebuilt_path in {"", "."}:
            rebuilt_path = posixpath.basename(target_path) or target_path
        query = _strip_is_course_file_query(parsed.query)
        if query:
            rebuilt_path += f"?{query}"
        if parsed.fragment:
            rebuilt_path += f"#{parsed.fragment}"
        return rebuilt_path

    def replace_attr(tag_html: str, attr_name: str, value: str) -> str:
        pattern = re.compile(
            rf'(\b{attr_name}\s*=\s*)(["\'])([^"\']*)(\2)',
            flags=re.IGNORECASE,
        )
        return pattern.sub(lambda m: f'{m.group(1)}"{html.escape(value, quote=True)}"', tag_html, count=1)

    anchor_pattern = re.compile(r"<a\b[^>]*\bhref\s*=\s*([\"'])(?P<href>[^\"']+)\1[^>]*>", flags=re.IGNORECASE)

    def replace_anchor(match: re.Match[str]) -> str:
        nonlocal rewired_local_refs
        nonlocal neutralized_anchor_refs
        original_tag = match.group(0)
        href = match.group("href").strip()
        status, target = resolve_candidate(href)
        if status == "keep":
            return original_tag
        if status == "rewrite":
            new_href = rebuild_url(href, target)
            if new_href == href:
                return original_tag
            rewired_local_refs += 1
            return replace_attr(original_tag, "href", new_href)

        neutralized_anchor_refs += 1
        tag = replace_attr(original_tag, "href", "#")
        if "data-migration-link-status=" not in tag:
            original_href = html.escape(href, quote=True)
            tag = tag[:-1] + (
                f' data-migration-link-status="needs-review" '
                f'data-migration-original-href="{original_href}">'
            )
        return tag

    updated = anchor_pattern.sub(replace_anchor, content)

    image_pattern = re.compile(r"<img\b[^>]*\bsrc\s*=\s*([\"'])(?P<src>[^\"']+)\1[^>]*>", flags=re.IGNORECASE)

    def replace_image(match: re.Match[str]) -> str:
        nonlocal rewired_local_refs
        nonlocal removed_missing_images
        nonlocal replaced_missing_images_with_alt
        original_tag = match.group(0)
        src = match.group("src").strip()
        status, target = resolve_candidate(src)
        if status == "keep":
            return original_tag
        if status == "rewrite":
            new_src = rebuild_url(src, target)
            if new_src == src:
                return original_tag
            rewired_local_refs += 1
            return replace_attr(original_tag, "src", new_src)

        removed_missing_images += 1
        if not keep_alt_text_for_missing_images:
            return ""
        alt_match = re.search(
            r'\balt\s*=\s*(["\'])(?P<alt>.*?)\1',
            original_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        alt_text = alt_match.group("alt").strip() if alt_match else ""
        if not alt_text or alt_text.lower() in {"banner", "logo", "image", "decorative"}:
            return ""
        replaced_missing_images_with_alt += 1
        return f'<span class="migration-missing-image-text">{html.escape(alt_text, quote=False)}</span>'

    updated = image_pattern.sub(replace_image, updated)

    applied: list[AppliedChange] = []
    if rewired_local_refs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Rewired local file references to existing package assets",
                count=rewired_local_refs,
            )
        )
    if neutralized_anchor_refs:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Neutralized unresolved local links for manual relink",
                count=neutralized_anchor_refs,
            )
        )
    if removed_missing_images:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Removed unresolved local image references",
                count=removed_missing_images,
            )
        )
    if replaced_missing_images_with_alt:
        applied.append(
            AppliedChange(
                category="sanitizer",
                description="Replaced removed unresolved images with existing alt text",
                count=replaced_missing_images_with_alt,
            )
        )

    return updated, applied


def apply_best_practice_enforcer(
    content: str,
    *,
    file_path: str = "",
    policy: BestPracticeEnforcerPolicy | None = None,
) -> tuple[str, list[AppliedChange]]:
    """
    Apply a safe subset of best-practice enforcement rules.
    """
    applied_policy = policy or BestPracticeEnforcerPolicy()
    if not applied_policy.enabled:
        return content, []

    updated = content
    applied: list[AppliedChange] = []
    normalized_file = file_path.lower()
    lowered = updated.lower()

    if applied_policy.enforce_module_checklist_closer:
        is_intro_or_checklist = (
            "introduction and objectives" in normalized_file
            or "introduction and checklist" in normalized_file
            or "module checklist" in lowered
        )
        required_closer_plain = "contact your instructor with any questions or post in the course q&a."
        required_closer_html = "Contact your instructor with any questions or post in the Course Q&amp;A."
        if is_intro_or_checklist and required_closer_plain not in lowered:
            closer_added = False

            heading_match = re.search(
                r"<h[1-6][^>]*>\s*module checklist\s*</h[1-6]>",
                updated,
                flags=re.IGNORECASE,
            )
            if heading_match:
                remainder = updated[heading_match.end() :]
                ul_open_match = re.search(r"<ul\b[^>]*>", remainder, flags=re.IGNORECASE)
                if ul_open_match:
                    after_ul = remainder[ul_open_match.end() :]
                    ul_close_match = re.search(r"</ul>", after_ul, flags=re.IGNORECASE)
                    if ul_close_match:
                        insert_at = heading_match.end() + ul_open_match.end() + ul_close_match.start()
                        updated = updated[:insert_at] + f"\n  <li>{required_closer_html}</li>" + updated[insert_at:]
                        closer_added = True

            if not closer_added:
                fallback_block = (
                    "<p class=\"migration-checklist-closer\">"
                    "<strong>Module Checklist Reminder:</strong> "
                    f"{required_closer_html}"
                    "</p>"
                )
                body_close_match = re.search(r"</body>", updated, flags=re.IGNORECASE)
                if body_close_match:
                    updated = updated[: body_close_match.start()] + fallback_block + "\n" + updated[body_close_match.start() :]
                else:
                    updated = updated + "\n" + fallback_block
                closer_added = True

            if closer_added:
                lowered = updated.lower()
                applied.append(
                    AppliedChange(
                        category="best_practice",
                        description="Added required Module Checklist closing reminder",
                        count=1,
                    )
                )

    if applied_policy.ensure_external_links_new_tab:
        anchor_tag_pattern = re.compile(r"<a\b[^>]*>", flags=re.IGNORECASE)
        updated_link_count = 0

        def replace_external_anchor(match: re.Match[str]) -> str:
            nonlocal updated_link_count
            tag = match.group(0)
            href_match = re.search(
                r'\bhref\s*=\s*(["\'])(?P<href>[^"\']+)\1',
                tag,
                flags=re.IGNORECASE,
            )
            if href_match is None:
                return tag

            href_value = href_match.group("href").strip()
            if not re.match(r"^https?://", href_value, flags=re.IGNORECASE):
                return tag

            original = tag
            if re.search(r"\btarget\s*=", tag, flags=re.IGNORECASE) is None:
                tag = tag[:-1] + ' target="_blank">'

            if re.search(r'\btarget\s*=\s*(["\'])_blank\1', tag, flags=re.IGNORECASE):
                rel_match = re.search(
                    r'\brel\s*=\s*(["\'])(?P<rel>[^"\']*)\1',
                    tag,
                    flags=re.IGNORECASE,
                )
                if rel_match is None:
                    tag = tag[:-1] + ' rel="noopener noreferrer">'
                else:
                    rel_tokens = [token for token in rel_match.group("rel").split() if token]
                    rel_lower = {token.lower() for token in rel_tokens}
                    updated_tokens = list(rel_tokens)
                    if "noopener" not in rel_lower:
                        updated_tokens.append("noopener")
                    if "noreferrer" not in rel_lower:
                        updated_tokens.append("noreferrer")
                    updated_rel = " ".join(updated_tokens).strip()
                    tag = (
                        tag[: rel_match.start("rel")]
                        + updated_rel
                        + tag[rel_match.end("rel") :]
                    )

            if tag != original:
                updated_link_count += 1
            return tag

        updated = anchor_tag_pattern.sub(replace_external_anchor, updated)
        if updated_link_count:
            applied.append(
                AppliedChange(
                    category="best_practice",
                    description="Updated external links to open in new tab with safe rel attributes",
                    count=updated_link_count,
                )
            )

    return updated, applied


def detect_manual_review_issues(content: str, triggers: Iterable[ManualTrigger]) -> list[ManualReviewIssue]:
    issues: list[ManualReviewIssue] = []
    for trigger in triggers:
        pattern = re.compile(trigger.pattern, flags=_re_flags(trigger.flags))
        match = pattern.search(content)
        if match:
            snippet = match.group(0)
            evidence = snippet[:120].replace("\n", " ")
            issues.append(ManualReviewIssue(reason=trigger.reason, evidence=evidence))
    return issues


def check_template_heuristics(
    content: str,
    file_path: str = "",
    policy: TemplateCheckPolicy | None = None,
) -> list[ManualReviewIssue]:
    """Detect likely template-compliance issues derived from local reference docs."""
    applied_policy = policy or TemplateCheckPolicy()
    issues: list[ManualReviewIssue] = []
    lowered = content.lower()
    normalized_file = file_path.lower()

    # Instructor note placeholders should be resolved before release.
    if applied_policy.check_instructor_notes and re.search(r"\[\s*instructor note\s*:", content, flags=re.IGNORECASE):
        issues.append(
            ManualReviewIssue(
                reason="Instructor Note placeholder remains in content",
                evidence="[Instructor Note: ...]",
            )
        )

    # Common template placeholders from syllabus/page templates.
    if applied_policy.check_template_placeholders:
        placeholder_patterns = (
            r"\bfill in text here\b",
            r"\[title here\]",
            r"\bxx\b",
        )
        for pattern in placeholder_patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                issues.append(
                    ManualReviewIssue(
                        reason="Template placeholder text remains in content",
                        evidence=match.group(0)[:120],
                    )
                )
                break

    # Legacy quiz wording should be replaced for pilot shells.
    if applied_policy.check_legacy_quiz_wording and re.search(
        r'click\s+the\s+"?take\s+the\s+quiz"?\s+button',
        content,
        flags=re.IGNORECASE,
    ):
        issues.append(
            ManualReviewIssue(
                reason='Legacy quiz instructions detected ("Take the Quiz")',
                evidence='Click the "Take the Quiz" button',
            )
        )

    # IC pages should retain checklist closing reminder based on templates.
    is_intro_checklist_page = (
        "introduction and checklist" in lowered
        or "introduction-and-checklist" in normalized_file
    )
    required_mc_closer = "contact your instructor with any questions or post in the course q&a"
    if applied_policy.require_mc_closing_bullet and is_intro_checklist_page and required_mc_closer not in lowered:
        issues.append(
            ManualReviewIssue(
                reason="Module Checklist closing reminder appears to be missing",
                evidence="Contact your instructor with any questions or post in the Course Q&A.",
            )
        )

    return issues


def check_accessibility_heuristics(content: str) -> list[ManualReviewIssue]:
    issues: list[ManualReviewIssue] = []

    for match in re.finditer(r"<img\b[^>]*>", content, flags=re.IGNORECASE):
        img_tag = match.group(0)
        alt_match = re.search(r"\balt\s*=\s*([\"'])(.*?)\1", img_tag, flags=re.IGNORECASE | re.DOTALL)
        if alt_match is None:
            issues.append(
                ManualReviewIssue(
                    reason="Image missing alt attribute",
                    evidence=img_tag[:120],
                )
            )
        elif not alt_match.group(2).strip():
            issues.append(
                ManualReviewIssue(
                    reason="Image alt attribute is empty",
                    evidence=img_tag[:120],
                )
            )

    heading_levels = [
        int(m.group(1))
        for m in re.finditer(r"<h([1-6])\b", content, flags=re.IGNORECASE)
    ]
    for previous, current in zip(heading_levels, heading_levels[1:]):
        if current - previous > 1:
            issues.append(
                ManualReviewIssue(
                    reason="Heading level jump detected",
                    evidence=f"h{previous} -> h{current}",
                )
            )

    for table_match in re.finditer(r"<table\b.*?</table>", content, flags=re.IGNORECASE | re.DOTALL):
        table_html = table_match.group(0)
        if re.search(r"<caption\b", table_html, flags=re.IGNORECASE) is None:
            issues.append(
                ManualReviewIssue(
                    reason="Table missing caption",
                    evidence=table_html[:120].replace("\n", " "),
                )
            )

    for link_match in re.finditer(r"<a\b[^>]*>(.*?)</a>", content, flags=re.IGNORECASE | re.DOTALL):
        visible_text = re.sub(r"<[^>]+>", "", link_match.group(1))
        if visible_text.strip().lower() in {"click here", "here", "learn more", "more"}:
            issues.append(
                ManualReviewIssue(
                    reason="Non-descriptive link text",
                    evidence=visible_text.strip()[:120],
                )
            )

    return issues

"""template_merger.py — Phase 3 template shell merger.

Classifies D2L pages by role and wraps them with the eLearn Standard Template
shell structure.  Runs as a post-processing pass inside ``pipeline.py``, after
all per-file HTML transforms (sanitiser, overlay, rules) have finished.

Operations
----------
MODULE_INTRO pages
    Body replaced in-place with the module-intro-and-checklist template shell
    (star / bullseye / checkmark icons, red Sinclair headings).  The original
    ``<head>`` (including the D2L identifier) is preserved so manifest
    references remain valid.

WELCOME_INSTRUCTOR page
    Instructor bio extracted and injected into ``about-the-instructor.html``
    from the template.  The original Welcome page is replaced in-place with a
    redirect notice so no manifest link breaks.  The filled
    ``about-the-instructor.html`` is written to ``wiki_content/``.

Standalone template pages
    ``home-page.html``, ``policies-and-support.html``,
    ``canvas-resources-for-students.html``, and ``next-steps.html`` are copied
    from the template package into ``wiki_content/`` when not already present.
    They appear in Canvas as standalone Pages.

All other pages
    Passed through unchanged.

Notes
-----
* ``$IMS-CC-FILEBASE$/template-images/...`` URLs in injected template HTML are
  rewritten to ``../TemplateAssets/{basename}`` so they resolve once Canvas
  imports the package.  The ``TemplateAssets/`` folder is already materialised
  by ``materialize_template_assets()`` earlier in the pipeline.
* No changes to ``imsmanifest.xml``.  Newly added wiki_content pages appear as
  standalone Pages in Canvas; instructors assign them to Modules manually.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from zipfile import ZipFile


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PageRole(str, Enum):
    MODULE_INTRO = "module_intro"
    WELCOME_INSTRUCTOR = "welcome_instructor"
    STANDALONE = "standalone"


@dataclass
class MergedPageRecord:
    original_path: str
    role: PageRole
    action: str  # "template_wrapped" | "merged_into_about_instructor" | "added_from_template" | "passthrough"
    target_path: str = ""
    module_number: int | None = None
    chapter_title: str = ""


@dataclass
class TemplateMergeResult:
    pages: list[MergedPageRecord] = field(default_factory=list)
    added_template_pages: list[str] = field(default_factory=list)

    @property
    def wrapped_count(self) -> int:
        return sum(1 for p in self.pages if p.action == "template_wrapped")

    @property
    def added_count(self) -> int:
        return len(self.added_template_pages)


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title>[^<]*</title>", re.IGNORECASE)
_META_ID_RE = re.compile(
    r'(<meta\s+name=["\']identifier["\'][^>]*content=["\'])([^"\']+)(["\'][^>]*/?>)',
    re.IGNORECASE,
)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)

# $IMS-CC-FILEBASE$/template-images/.../{basename}
_TEMPLATE_ASSET_URL_RE = re.compile(
    r"\$IMS-CC-FILEBASE\$/template-images/[^\"' >]*?/([^/\"' >]+)",
    re.IGNORECASE,
)

# Module folder: "01-ChapterName/" or "16-Ch_Name/"
_MODULE_FOLDER_RE = re.compile(r"^(\d{2})-(.+?)/")

# Heading containing specific text (any level h1-h6)
_INTRO_HEADING_RE = re.compile(
    r"<h[1-6][^>]*>(?:(?!</h[1-6]>).)*?introduction(?:(?!</h[1-6]>).)*?</h[1-6]>",
    re.IGNORECASE | re.DOTALL,
)
_OBJECTIVES_HEADING_RE = re.compile(
    r"<h[1-6][^>]*>(?:(?!</h[1-6]>).)*?objectives?(?:(?!</h[1-6]>).)*?</h[1-6]>",
    re.IGNORECASE | re.DOTALL,
)

# Instructor Note placeholder paragraphs in template HTML
_INSTRUCTOR_NOTE_BLOCK_RE = re.compile(
    r"<p>\[<strong>(?:<span[^>]*>)*\s*Instructor Note:.*?</strong>\]</p>",
    re.DOTALL | re.IGNORECASE,
)

# Placeholder objective list items in module-intro template
_PLACEHOLDER_OBJ_UL_RE = re.compile(
    r"<ul>\s*(?:<li>Objective</li>\s*)+</ul>",
    re.IGNORECASE | re.DOTALL,
)

# About-the-instructor: the instructor bio placeholder block
# Spans from "[Type Name Here...]" <h3> to the next <hr>
_INSTRUCTOR_BIO_BLOCK_RE = re.compile(
    r"<h3>\[Type Name Here.*?(?=<hr)",
    re.DOTALL | re.IGNORECASE,
)

# Strip the Bootstrap/D2L scaffold from a body: print link, banner img, footer, scripts
_PRINT_LINK_RE = re.compile(
    r"<p[^>]*>(?:(?!</p>).)*?Printer-friendly version.*?</p>",
    re.DOTALL | re.IGNORECASE,
)
_BANNER_IMG_RE = re.compile(
    r"<p[^>]*>\s*(?:<span[^>]*>)?\s*<img[^>]*(?:banner|logo|rule)[^>]*/?>(?:</span>)?\s*</p>",
    re.DOTALL | re.IGNORECASE,
)
_FOOTER_RE = re.compile(
    r"<(?:div[^>]*>)?\s*<footer[^>]*>.*?</footer>(?:\s*</div>)?",
    re.DOTALL | re.IGNORECASE,
)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_EMPTY_PARA_RE = re.compile(r"<p[^>]*>\s*(?:&nbsp;\s*)*</p>", re.IGNORECASE)

# Standalone template pages to add to the output package.
# Stored in CourseOverview/ so the D2L importer processes them
# (Canvas skips wiki_content/ paths when using d2l_exporter/canvas_cartridge_importer).
_STANDALONE_TEMPLATE_PAGES = [
    "CourseOverview/Home Page.html",
    "CourseOverview/Policies and Support.html",
    "CourseOverview/Canvas Resources for Students.html",
    "CourseOverview/Next Steps.html",
    "CourseOverview/About the Instructor.html",
]

# Mapping: source template basename → destination CourseOverview filename
_TEMPLATE_PAGE_SOURCE_MAP: dict[str, str] = {
    "CourseOverview/Home Page.html": "home-page.html",
    "CourseOverview/Policies and Support.html": "policies-and-support.html",
    "CourseOverview/Canvas Resources for Students.html": "canvas-resources-for-students.html",
    "CourseOverview/Next Steps.html": "next-steps.html",
    "CourseOverview/About the Instructor.html": "about-the-instructor.html",
}

# Template pages used as shells for wrapping
_MODULE_INTRO_TEMPLATE_PAGE = "wiki_content/module-1-introduction-and-checklist.html"
_ABOUT_INSTRUCTOR_TEMPLATE_PAGE = "wiki_content/about-the-instructor.html"

# Classification keyword sets
_WELCOME_KEYWORDS = frozenset(
    ["welcome from instructor", "welcome from the instructor", "text from"]
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_page(
    path: str,
    title: str,
) -> tuple[PageRole, int | None, str]:
    """Classify a D2L page and extract module metadata.

    Args:
        path: Relative path of the HTML file within the package.
        title: Page ``<title>`` text.

    Returns:
        ``(role, module_number, chapter_title)`` where the last two are only
        meaningful for :attr:`PageRole.MODULE_INTRO` pages.
    """
    path_lower = path.lower()
    title_lower = title.lower().strip()

    # Module intro: XX-ChapterName/Introduction and Objectives.html
    m = _MODULE_FOLDER_RE.match(path)
    if m and ("introduction" in path_lower or "objectives" in path_lower):
        module_number = int(m.group(1))
        chapter_title = m.group(2).replace("_", " ").replace("-", " ").title().strip()
        return PageRole.MODULE_INTRO, module_number, chapter_title

    if any(kw in title_lower for kw in _WELCOME_KEYWORDS) or (
        "welcome" in path_lower and "instructor" in path_lower
    ):
        return PageRole.WELCOME_INSTRUCTOR, None, ""

    return PageRole.STANDALONE, None, ""


# ---------------------------------------------------------------------------
# HTML utilities
# ---------------------------------------------------------------------------


def _extract_title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return re.sub(r"<[^>]+>", "", m.group(0)).strip() if m else ""


def _extract_body(html: str) -> str:
    m = _BODY_RE.search(html)
    return m.group(1).strip() if m else html


def _replace_title(html: str, new_title: str) -> str:
    return _TITLE_RE.sub(f"<title>{new_title}</title>", html, count=1)


def _replace_identifier(html: str, seed: str) -> str:
    new_id = "g" + hashlib.md5(seed.encode()).hexdigest()
    return _META_ID_RE.sub(rf"\g<1>{new_id}\g<3>", html, count=1)


def _replace_body(html: str, new_body: str) -> str:
    return _BODY_RE.sub(f"<body>\n{new_body}\n</body>", html)


def _rewrite_template_asset_urls(html: str, depth: int = 1) -> str:
    """Rewrite ``$IMS-CC-FILEBASE$/template-images/...`` to relative paths.

    Args:
        html: HTML text to process.
        depth: Directory depth of the file from the package root (1 for both
               ``wiki_content/`` pages and module-folder pages).
    """
    prefix = "../" * depth + "TemplateAssets/"

    def _sub(m: re.Match) -> str:
        return prefix + m.group(1)

    return _TEMPLATE_ASSET_URL_RE.sub(_sub, html)


def _clean_d2l_scaffold(body: str) -> str:
    """Strip Bootstrap/D2L navigation scaffolding from a processed D2L body."""
    body = _PRINT_LINK_RE.sub("", body)
    body = _BANNER_IMG_RE.sub("", body)
    body = _FOOTER_RE.sub("", body)
    body = _SCRIPT_RE.sub("", body)
    body = _EMPTY_PARA_RE.sub("", body)
    return body.strip()


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _extract_intro_paragraphs(body: str) -> str:
    """Return the Introduction section content (paragraphs, not the heading)."""
    intro_m = _INTRO_HEADING_RE.search(body)
    if not intro_m:
        # Fallback: first non-empty paragraphs
        paras = re.findall(
            r"<p[^>]*>(?!&nbsp;\s*</p>).+?</p>", body, re.DOTALL | re.IGNORECASE
        )
        return "\n".join(paras[:3])

    search_from = intro_m.end()
    # Section ends at next heading or <hr>
    end_m = re.search(r"<(?:h[1-6]|hr)[\s>]", body[search_from:], re.IGNORECASE)
    end_pos = end_m.start() if end_m else len(body[search_from:])
    section = body[search_from : search_from + end_pos]

    # Remove rule/separator images
    section = re.sub(
        r"<(?:p[^>]*>)?\s*<img[^>]*(?:rule|gradient|separator)[^>]*/?>(?:</p>)?",
        "",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    )
    section = _EMPTY_PARA_RE.sub("", section)
    return section.strip()


def _extract_objectives_list(body: str) -> str:
    """Return the ordered/unordered objectives list HTML fragment."""
    obj_m = _OBJECTIVES_HEADING_RE.search(body)
    search_from = obj_m.end() if obj_m else 0
    list_m = re.search(
        r"<(?:ol|ul)[^>]*>.*?</(?:ol|ul)>",
        body[search_from:],
        re.DOTALL | re.IGNORECASE,
    )
    return list_m.group(0) if list_m else ""


# ---------------------------------------------------------------------------
# Module intro  template filling
# ---------------------------------------------------------------------------

# Template body for module intro shell — icons use TemplateAssets/*
# We build this once with a placeholder and fill sections at runtime.
# (No need to read the template HTML for every module — the body structure
#  is stable; only the icon filenames and content differ.)

_MODULE_INTRO_BODY_TMPL = """\
<h2 style="color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;">
  <img role="presentation" src="../TemplateAssets/star.png"
       alt="" width="45" height="45" loading="lazy">
  <strong>Introduction</strong>
</h2>
{intro_content}
<hr>
<h2>
  <img role="presentation" src="../TemplateAssets/bullseye.png"
       alt="" width="45" height="45" loading="lazy">
  <strong><span style="color: #ac1a2f;">Module Objectives</span></strong>
</h2>
<p>By the end of this module, students will be able to:</p>
{objectives_content}
<hr>
<h2>
  <img role="presentation" src="../TemplateAssets/checkmark.png"
       alt="" width="45" height="45" loading="lazy">
  <span style="color: #ac1a2f;"><strong>Module Checklist</strong></span>
</h2>
<p>Complete the items listed below as you work through this module:</p>
<ul>
  <li>Read all assigned content and review lecture materials.</li>
  <li>Complete the learning activities for this module.</li>
  <li>Submit all assignments before the posted due date.</li>
</ul>
"""


def _fill_module_intro(
    d2l_html: str,
    module_number: int | None,
    chapter_title: str,
    path_seed: str,
) -> str:
    """Return the in-place replacement HTML for a module intro page.

    Keeps the original D2L ``<head>`` (identifier, workflow state) and
    replaces only the body with the template shell.
    """
    body = _clean_d2l_scaffold(_extract_body(d2l_html))

    intro_content = _extract_intro_paragraphs(body)
    if not intro_content:
        intro_content = (
            "<p>Refer to the course materials for an introduction to this module.</p>"
        )

    objectives_html = _extract_objectives_list(body)
    if objectives_html:
        # Normalise to <ul> for visual consistency with the template shell
        li_items = re.findall(
            r"<li>.*?</li>", objectives_html, re.DOTALL | re.IGNORECASE
        )
        objectives_content = (
            "<ul>\n" + "\n".join(f"  {li}" for li in li_items) + "\n</ul>"
        )
    else:
        objectives_content = "<ul><li>See course materials for this module's learning objectives.</li></ul>"

    new_body = _MODULE_INTRO_BODY_TMPL.format(
        intro_content=intro_content,
        objectives_content=objectives_content,
    )

    mod_str = f"Module {module_number}: " if module_number else ""
    new_title = f"{mod_str}{chapter_title}: Introduction and Objectives"

    result = _replace_title(d2l_html, new_title)
    result = _replace_body(result, new_body)
    return result


# ---------------------------------------------------------------------------
# About-the-instructor template filling
# ---------------------------------------------------------------------------


def _clean_instructor_bio(body: str) -> str:
    """Strip scaffold and extract a clean bio HTML fragment from the Welcome page body."""
    cleaned = _clean_d2l_scaffold(body)

    # Remove outer no-class divs left by Bootstrap stripping
    cleaned = re.sub(r"<div>\s*", "", cleaned)
    cleaned = re.sub(r"\s*</div>", "", cleaned)

    # Remove the page-title <h1> (instructor name as heading — template handles this)
    cleaned = re.sub(
        r"<h1[^>]*>.*?</h1>",
        "",
        cleaned,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Promote the instructor photo to block float if still inline in a paragraph
    # (the existing pipeline may have already handled this via wrap_floated_blocks)

    return cleaned.strip()


def _fill_about_instructor(
    welcome_d2l_html: str,
    template_html: str,
) -> str:
    """Inject the D2L instructor bio into the about-the-instructor template."""
    bio_fragment = _clean_instructor_bio(_extract_body(welcome_d2l_html))

    # Rewrite icon URLs in the template (they still have $IMS-CC-FILEBASE$ tokens)
    result = _rewrite_template_asset_urls(template_html, depth=1)

    # Replace the instructor bio placeholder block
    if _INSTRUCTOR_BIO_BLOCK_RE.search(result):
        result = _INSTRUCTOR_BIO_BLOCK_RE.sub(bio_fragment + "\n", result, count=1)
    else:
        # Fallback: replace first Instructor Note block
        if _INSTRUCTOR_NOTE_BLOCK_RE.search(result):
            result = _INSTRUCTOR_NOTE_BLOCK_RE.sub(bio_fragment, result, count=1)
        else:
            # Last resort: append before closing body
            result = result.replace("</body>", bio_fragment + "\n</body>", 1)

    # Generate stable unique identifier so it doesn't collide with existing pages
    result = _replace_identifier(result, "about-the-instructor-merged")
    return result


# ---------------------------------------------------------------------------
# Manifest injection
# ---------------------------------------------------------------------------

# Human-readable display titles for the template pages we inject
# Keys are DESTINATION filenames (natural language, as stored in CourseOverview/)
_TEMPLATE_PAGE_TITLES: dict[str, str] = {
    "About the Instructor.html": "About the Instructor",
    "Home Page.html": "Home Page",
    "Policies and Support.html": "Policies and Support",
    "Canvas Resources for Students.html": "Canvas Resources for Students",
    "Next Steps.html": "Next Steps",
}


def _inject_manifest_entries(
    unpack_dir: Path,
    new_pages: list[
        str
    ],  # relative posix paths, e.g. "wiki_content/about-the-instructor.html"
) -> None:
    """Register newly-added wiki_content pages in ``imsmanifest.xml``.

    Adds both a ``<resource>`` entry (so Canvas knows the file) and an
    ``<item>`` entry inside ``<organization>`` (so Canvas creates the Page
    and lists it in the course).  Existing entries are never modified.
    """
    manifest_path = unpack_dir / "imsmanifest.xml"
    if not manifest_path.exists() or not new_pages:
        return

    manifest = manifest_path.read_text(encoding="utf-8", errors="replace")

    resource_lines: list[str] = []
    item_lines: list[str] = []

    for rel_path in new_pages:
        basename = Path(rel_path).name
        stem = Path(rel_path).stem  # e.g. "about-the-instructor"
        slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
        identifier = f"TMPL_{slug.upper().replace('-', '_')}"
        title = _TEMPLATE_PAGE_TITLES.get(basename, stem.replace("-", " ").title())

        # D2L manifests use backslash-separated href paths
        manifest_href = rel_path.replace("/", "\\")

        resource_lines.append(
            f'        <resource identifier="{identifier}" type="webcontent"'
            f' d2l_2p0:material_type="content" d2l_2p0:link_target=""'
            f' href="{manifest_href}" title="" />'
        )
        item_lines.append(
            f'            <item identifier="TMPL_ITEM_{slug.upper().replace("-", "_")}"'
            f' identifierref="{identifier}" completion_type="2">\n'
            f"                <title>{title}</title>\n"
            f"            </item>"
        )

    # Insert resource entries before </resources>
    resources_close = re.search(r"(\s*</resources>)", manifest)
    if resources_close and resource_lines:
        insert_pos = resources_close.start()
        manifest = (
            manifest[:insert_pos]
            + "\n"
            + "\n".join(resource_lines)
            + "\n"
            + manifest[insert_pos:]
        )

    # Insert item entries before </organization> (the single org block)
    org_close = re.search(r"(\s*</organization>)", manifest)
    if org_close and item_lines:
        insert_pos = org_close.start()
        manifest = (
            manifest[:insert_pos]
            + "\n"
            + "\n".join(item_lines)
            + "\n"
            + manifest[insert_pos:]
        )

    manifest_path.write_text(manifest, encoding="utf-8")


# ---------------------------------------------------------------------------
# Template page loader
# ---------------------------------------------------------------------------


def _load_template_wiki_pages(template_package: Path) -> dict[str, str]:
    """Return a mapping of ``basename → html_text`` for wiki_content pages."""
    pages: dict[str, str] = {}
    with ZipFile(template_package, "r") as zf:
        for name in zf.namelist():
            if name.startswith("wiki_content/") and name.endswith(".html"):
                basename = Path(name).name
                pages[basename] = zf.read(name).decode("utf-8", errors="replace")
    return pages


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_template_merge(
    unpack_dir: Path,
    template_package: Path,
) -> TemplateMergeResult:
    """Apply the template shell merger to processed HTML files in *unpack_dir*.

    Should be called **after** all per-file HTML transforms have completed
    (sanitiser, overlay, rules) but **before** the output zip is assembled.

    Args:
        unpack_dir: Directory containing the extracted + processed D2L package.
        template_package: Path to the eLearn Standard Template ``.imscc`` file.

    Returns:
        :class:`TemplateMergeResult` summarising what was changed.
    """
    result = TemplateMergeResult()

    # Load template wiki pages (used as shells / for standalone additions)
    template_pages = _load_template_wiki_pages(template_package)

    # Survey all HTML files
    html_files = sorted(unpack_dir.rglob("*.html")) + sorted(unpack_dir.rglob("*.htm"))

    welcome_path: str | None = None

    for html_file in html_files:
        rel = str(html_file.relative_to(unpack_dir).as_posix())
        content = html_file.read_text(encoding="utf-8", errors="replace")
        title = _extract_title(content)
        role, module_number, chapter_title = classify_page(rel, title)

        if role == PageRole.MODULE_INTRO:
            new_html = _fill_module_intro(
                d2l_html=content,
                module_number=module_number,
                chapter_title=chapter_title,
                path_seed=rel,
            )
            html_file.write_text(new_html, encoding="utf-8")
            result.pages.append(
                MergedPageRecord(
                    original_path=rel,
                    role=role,
                    action="template_wrapped",
                    module_number=module_number,
                    chapter_title=chapter_title,
                )
            )

        elif role == PageRole.WELCOME_INSTRUCTOR:
            welcome_path = rel
            welcome_content = content
            result.pages.append(
                MergedPageRecord(
                    original_path=rel,
                    role=role,
                    action="merged_into_about_instructor",
                    target_path="CourseOverview/About the Instructor.html",
                )
            )

        else:
            result.pages.append(
                MergedPageRecord(
                    original_path=rel,
                    role=role,
                    action="passthrough",
                )
            )

    # Build about-the-instructor if we found a welcome page
    if welcome_path is not None and "about-the-instructor.html" in template_pages:
        about_html = _fill_about_instructor(
            welcome_d2l_html=welcome_content,  # type: ignore[possibly-undefined]
            template_html=template_pages["about-the-instructor.html"],
        )
        about_dest = unpack_dir / "CourseOverview" / "About the Instructor.html"
        about_dest.parent.mkdir(parents=True, exist_ok=True)
        about_dest.write_text(about_html, encoding="utf-8")
        result.added_template_pages.append("CourseOverview/About the Instructor.html")

        # Replace the original welcome page with a brief redirect notice so any
        # manifest link that points to it doesn't produce a 404.
        welcome_file = unpack_dir / welcome_path
        redirect_body = (
            "<p>This content has been incorporated into the "
            '<a href="About the Instructor.html">About the Instructor</a> page.</p>'
        )
        redirect_html = (
            "<!DOCTYPE html>\n<html><head>\n"
            f'<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n'
            f"<title>Welcome from the Instructor</title>\n"
            "</head>\n<body>\n"
            f"{redirect_body}\n"
            "</body></html>"
        )
        welcome_file.write_text(redirect_html, encoding="utf-8")

    # Add standalone template pages (only if not already present)
    for dest_rel in _STANDALONE_TEMPLATE_PAGES:
        dest = unpack_dir / dest_rel
        dest_basename = Path(dest_rel).name

        # about-the-instructor is already handled above
        if dest_basename == "About the Instructor.html":
            continue

        if dest.exists():
            continue  # don't overwrite course-specific content

        # Look up the source template basename from the mapping
        source_basename = _TEMPLATE_PAGE_SOURCE_MAP.get(dest_rel)
        if not source_basename:
            continue
        page_html = template_pages.get(source_basename)
        if not page_html:
            continue

        # CourseOverview/ is depth=1 from root — same as wiki_content/
        page_html = _rewrite_template_asset_urls(page_html, depth=1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page_html, encoding="utf-8")
        result.added_template_pages.append(dest_rel)

    # NOTE: Template pages live in CourseOverview/, not wiki_content/, so the D2L
    # importer cannot turn them into Canvas wiki pages.  Injecting <item> entries
    # into the manifest's <organization> block creates *empty module containers*
    # (one per top-level item) rather than pages.  The correct approach is to use
    # the Canvas Pages API via `run_preview(inject_template_pages=True)` AFTER
    # import, which creates the pages directly.  Do NOT inject manifest entries.

    return result

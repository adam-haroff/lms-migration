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

Module ordering (Canvas module_meta.xml)
    ``course_settings/module_meta.xml`` is written into the package. In
    curated mode, it places the template shell modules first, the D2L content
    modules in the middle, and the Course Conclusion module last. In full
    template-course mode, it preserves the template modules in order, inserts
    the D2L content modules before the Course Conclusion module, and keeps the
    template sample/reference modules available for faculty use.

Home page auto-selection
    The course code prefix (e.g. "ACC", "COM", "PSY") is extracted from the
    D2L manifest title and mapped to one of four Sinclair divisional home page
    templates:

    - Health Sciences  → ``home-page.html`` (default)
    - Business & Public Services → ``home-page-bps.html``
    - Liberal Arts, Communication & Social Sciences → ``home-page-lcs.html``
    - STEM → ``home-page-stem.html``

    The selected variant is written to ``wiki_content/home-page.html`` with
    ``<meta name="front_page" content="true"/>`` so Canvas sets it as the
    course home page on import.  ``course_settings/course_settings.xml`` is
    also written with ``<default_view>wiki</default_view>`` so Canvas switches
    the home from Modules view to the Page.

Notes
-----
* ``$IMS-CC-FILEBASE$/template-images/...`` URLs in injected template HTML are
  rewritten either to relative ``web_resources/template-images/...`` paths in
  full starter-shell mode, or to canonical ``template-images/...`` paths in
  overlay-only mode so the generated package better matches the final Canvas
  file organization.
"""

from __future__ import annotations

import hashlib
import html
import re
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PageRole(str, Enum):
    MODULE_INTRO = "module_intro"
    LEARNING_ACTIVITIES = "learning_activities"
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


@dataclass
class ScannedPage:
    path: str
    html_file: Path
    content: str
    title: str
    role: PageRole
    module_number: int | None
    chapter_title: str


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title>[^<]*</title>", re.IGNORECASE)
_META_ID_RE = re.compile(
    r'(<meta\s+name=["\']identifier["\'][^>]*content=["\'])([^"\']+)(["\'][^>]*/?>)',
    re.IGNORECASE,
)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)
_TEMPLATE_FILEBASE_URL_RE = re.compile(
    r"\$IMS-CC-FILEBASE\$/([^\"' >]+)",
    re.IGNORECASE,
)

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
_CHECKLIST_HEADING_RE = re.compile(
    r"<h[1-6][^>]*>(?:(?!</h[1-6]>).)*?(?:checklist|to meet the learning objectives)(?:(?!</h[1-6]>).)*?</h[1-6]>",
    re.IGNORECASE | re.DOTALL,
)
_LIST_RE = re.compile(
    r"<(?:ol|ul)[^>]*>.*?</(?:ol|ul)>",
    re.DOTALL | re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_LEARNING_SECTION_MARKER_RE = re.compile(
    r"(?P<marker>"
    r"<h[1-6][^>]*>.*?</h[1-6]>"
    r"|<(?:p|div)[^>]*>\s*(?:<(?:strong|span|em|b)[^>]*>\s*)*"
    r"<img\b[^>]*src\s*=\s*[\"'][^\"']+?(?:dothis|explorethis|reviewthis|viewthis|paper\.png|folder\.png|circle-arrow\.png|bookmark\.png|video\.png|book\.png)[^\"']*[\"'][^>]*>"
    r"(?:\s*</(?:strong|span|em|b)>\s*)*</(?:p|div)>"
    r")",
    re.DOTALL | re.IGNORECASE,
)
_LEARNING_SEPARATOR_RE = re.compile(
    r"<(?:p[^>]*>\s*)?<img[^>]*(?:rule_brown_gradient|separator|gradient|rule)[^>]*>(?:\s*</p>)?",
    re.IGNORECASE | re.DOTALL,
)
_EMPTY_BLOCK_RE = re.compile(
    r"<(?:p|div)[^>]*>\s*(?:&nbsp;|\s|<br\s*/?>)*</(?:p|div)>",
    re.IGNORECASE,
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
# Division → home page mapping
# ---------------------------------------------------------------------------

# Maps a Sinclair divisional code to the template home-page variant basename.
# The default variant (Health Sciences) is "home-page.html".
#
# Division codes are the official Sinclair Academic Division abbreviations:
#   BPS  Business & Public Services
#   LCS  Liberal Arts, Communication & Social Sciences
#   STEM Science, Technology, Engineering & Mathematics
#   HS   Health Sciences (default — all unrecognised prefixes fall here)
#
# Reference: https://www.sinclair.edu/programs/
_DIVISION_HOME_PAGE: dict[str, str] = {
    "bps": "home-page-bps.html",
    "lcs": "home-page-lcs.html",
    "stem": "home-page-stem.html",
    "hs": "home-page.html",
}

# Maps every known Sinclair course-code prefix to a division code.
# Derived from the Sinclair course catalog (sinclair.edu/programs/).
# Prefixes not listed here default to "hs" (Health Sciences home page).
_PREFIX_TO_DIVISION: dict[str, str] = {
    # ── Business & Public Services ──────────────────────────────────────────
    "acc": "bps",
    "adm": "bps",
    "bis": "bps",
    "bus": "bps",
    "cjs": "bps",
    "eco": "bps",
    "fin": "bps",
    "hrs": "bps",
    "lgm": "bps",
    "mgt": "bps",
    "mkt": "bps",
    "pal": "bps",
    "par": "bps",
    "pbl": "bps",
    "pls": "bps",
    "rea": "bps",
    "ret": "bps",
    "sfm": "bps",
    # ── Liberal Arts, Communication & Social Sciences ────────────────────────
    "ant": "lcs",
    "com": "lcs",
    "edu": "lcs",
    "eng": "lcs",
    "fla": "lcs",
    "fra": "lcs",
    "geo": "lcs",
    "his": "lcs",
    "hon": "lcs",
    "hum": "lcs",
    "icd": "lcs",
    "jpn": "lcs",
    "lib": "lcs",
    "mda": "lcs",
    "phi": "lcs",
    "psc": "lcs",
    "psy": "lcs",
    "rel": "lcs",
    "soc": "lcs",
    "spa": "lcs",
    "spe": "lcs",
    # ── STEM ─────────────────────────────────────────────────────────────────
    "arc": "stem",
    "asl": "stem",
    "ast": "stem",
    "bio": "stem",
    "che": "stem",
    "cis": "stem",
    "cit": "stem",
    "cnt": "stem",
    "eet": "stem",
    "egr": "stem",
    "emt": "stem",
    "env": "stem",
    "ict": "stem",
    "mat": "stem",
    "mec": "stem",
    "mtd": "stem",
    "phy": "stem",
    "sci": "stem",
    "tec": "stem",
    # ── Health Sciences (explicit — also the default) ─────────────────────────
    "aht": "hs",
    "bms": "hs",
    "dms": "hs",
    "dnt": "hs",
    "ems": "hs",
    "hlc": "hs",
    "hlt": "hs",
    "him": "hs",
    "mlt": "hs",
    "nrs": "hs",
    "oce": "hs",
    "omt": "hs",
    "opt": "hs",
    "pha": "hs",
    "pht": "hs",
    "rsp": "hs",
    "sgm": "hs",
    "sur": "hs",
    "vet": "hs",
    "xrt": "hs",
}

# ---------------------------------------------------------------------------
# Module meta constants (Canvas IMSCC extension)
# ---------------------------------------------------------------------------

# Identifiers and titles for the template shell modules.
# These match the template's module_meta.xml so Canvas merges them correctly.
_TEMPLATE_INSTRUCTOR_MODULE_ID = "gefc69a554f08c641ed6d85003000fb40"
_TEMPLATE_INSTRUCTOR_MODULE_TITLE = "Instructor Module (Do Not Publish)"

_TEMPLATE_START_HERE_ID = "g43cc723a24e2461e24205f608e560f0a"
_TEMPLATE_START_HERE_TITLE = "Start Here"

_TEMPLATE_CONCLUSION_ID = "g66a1695af5ea06b33c8a1add85501ac2"
_TEMPLATE_CONCLUSION_TITLE = "Module 16: Course Conclusion"
_TEMPLATE_SHELL_MODULE_TITLES = (
    _TEMPLATE_INSTRUCTOR_MODULE_TITLE,
    _TEMPLATE_START_HERE_TITLE,
    _TEMPLATE_CONCLUSION_TITLE,
)

# D2L module titles that map to a template shell — excluded from the
# middle section so they don't appear twice.
_D2L_SHELL_MODULE_TITLES = frozenset(
    [
        "preparing your course - for faculty use only",
        "preparing your course",
        "course overview",
        "start here",
        "d2l start here carryover (manual placement)",
        "instructor module (do not publish)",
        "module 16: course conclusion",
        "course conclusion",
    ]
)

_MODULE_META_NS = "http://canvas.instructure.com/xsd/cccv1p0"
_MODULE_META_XSD = "https://canvas.instructure.com/xsd/cccv1p0.xsd"
_EXTRA_TEMPLATE_RESOURCE_HREFS = frozenset(
    {
        "wiki_content/home-page.html",
        "wiki_content/policies-and-support.html",
        "wiki_content/next-steps.html",
        "wiki_content/about-the-instructor.html",
        "wiki_content/canvas-resources-for-students.html",
        "wiki_content/canvas-resources-for-instructors.html",
        "course_settings/syllabus.html",
        "course_settings/canvas_export.txt",
    }
)


# ---------------------------------------------------------------------------
# Home-page selection
# ---------------------------------------------------------------------------


def _course_prefix_from_manifest(manifest_path: Path) -> str:
    """Return the lower-cased course-code prefix (e.g. ``"acc"``) or ``""``."""
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="replace")
        # Look for the langstring that holds the course title, e.g.
        # "ACC 2321 Federal Taxation - Online Master"
        m = re.search(
            r"<(?:imsmd:)?langstring[^>]*>\s*([A-Z]{2,6})\s+\d",
            text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).lower()
    except OSError:
        pass
    return ""


def _home_page_variant(course_prefix: str) -> str:
    """Return the template wiki_content basename for the home page.

    Args:
        course_prefix: Lower-cased course-code prefix, e.g. ``"acc"``.

    Returns:
        A basename like ``"home-page-bps.html"`` or ``"home-page.html"``.
    """
    division = _PREFIX_TO_DIVISION.get(course_prefix.lower(), "hs")
    return _DIVISION_HOME_PAGE[division]


def _inject_home_page(
    unpack_dir: Path,
    template_pages: dict[str, str],
    course_prefix: str,
    *,
    use_template_web_resources: bool = False,
) -> str | None:
    """Write the correct home-page variant into ``wiki_content/``.

    Selects the divisional home page, adds ``front_page=true`` meta, rewrites
    template asset URLs, and writes it to ``wiki_content/home-page.html``.
    Also writes ``course_settings/course_settings.xml`` with
    ``<default_view>wiki</default_view>`` so Canvas switches the course home
    from Modules view to the front Page.

    Args:
        unpack_dir: The extracted + processed D2L package directory.
        template_pages: Dict of ``{basename: html}`` loaded from the template.
        course_prefix: Lower-cased course-code prefix.

    Returns:
        The basename of the variant used, or ``None`` if the template page was
        not found.
    """
    variant_basename = _home_page_variant(course_prefix)
    page_html = template_pages.get(variant_basename)
    if not page_html:
        # Fallback to the default home page
        page_html = template_pages.get("home-page.html")
        variant_basename = "home-page.html"
    if not page_html:
        return None

    # Rewrite template asset URLs to be relative
    page_html = _rewrite_template_asset_urls(
        page_html,
        relative_path="wiki_content/home-page.html",
        use_template_web_resources=use_template_web_resources,
    )

    # Ensure front_page meta is present and set to true
    if "front_page" not in page_html.lower():
        page_html = page_html.replace(
            "</head>",
            '<meta name="front_page" content="true"/>\n</head>',
            1,
        )
    else:
        page_html = re.sub(
            r'(<meta\s+name=["\']front_page["\'][^>]*content=["\'])[^"\']*(["\'])',
            r"\g<1>true\2",
            page_html,
            flags=re.IGNORECASE,
        )

    dest = unpack_dir / "wiki_content" / "home-page.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page_html, encoding="utf-8")

    # Write course_settings/course_settings.xml so Canvas sets default_view=wiki
    _write_course_settings(unpack_dir)

    return variant_basename


def _write_course_settings(unpack_dir: Path) -> None:
    """Write ``course_settings/course_settings.xml`` with ``default_view=wiki``.

    Only writes if the file does not already exist.
    """
    cs_dir = unpack_dir / "course_settings"
    cs_path = cs_dir / "course_settings.xml"
    if cs_path.exists():
        return
    cs_dir.mkdir(parents=True, exist_ok=True)
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <course identifier="g_lms_migration_course"
          xmlns="http://canvas.instructure.com/xsd/cccv1p0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd">
          <default_view>wiki</default_view>
        </course>
        """
    )
    cs_path.write_text(xml, encoding="utf-8")


# ---------------------------------------------------------------------------
# Module meta XML generation
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _read_d2l_module_titles(unpack_dir: Path) -> list[str]:
    """Return top-level module titles from ``imsmanifest.xml`` in order.

    Each entry is the human-readable title of a top-level ``<item>`` in
    the D2L manifest ``<organization>``.  Shell/carryover modules are
    excluded — only the genuine course content modules are returned.
    """
    return _read_d2l_module_titles_for_mode(unpack_dir)


def _read_d2l_module_titles_for_mode(
    unpack_dir: Path,
    *,
    exclude_shell_titles: bool = True,
    exclude_exact_titles: Iterable[str] = (),
) -> list[str]:
    """Return top-level D2L module titles with configurable filtering."""
    manifest_path = next(unpack_dir.rglob("imsmanifest.xml"), None)
    if manifest_path is None:
        return []
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
    except ET.ParseError:
        return []

    org: ET.Element | None = None
    for el in root.iter():
        if _local_name(el.tag) == "organization":
            org = el
            break
    if org is None:
        return []

    excluded = {title.strip() for title in exclude_exact_titles if title.strip()}
    titles: list[str] = []
    for item in org:
        if _local_name(item.tag) != "item":
            continue
        title = ""
        for child in item:
            if _local_name(child.tag) == "title":
                title = (child.text or "").strip()
                break
        if not title:
            continue
        if exclude_shell_titles and title.lower() in _D2L_SHELL_MODULE_TITLES:
            continue
        if title in excluded:
            continue
        titles.append(title)
    return titles


def _make_module_id(seed: str) -> str:
    """Return a stable Canvas-style module identifier derived from *seed*."""
    return "g" + hashlib.md5(f"module:{seed}".encode()).hexdigest()


def _make_item_id(seed: str) -> str:
    """Return a stable Canvas-style item identifier derived from *seed*."""
    return "g" + hashlib.md5(f"item:{seed}".encode()).hexdigest()


def _build_module_element(
    ns: str,
    identifier: str,
    title: str,
    position: int,
    *,
    workflow_state: str = "active",
) -> ET.Element:
    """Build a single ``<module>`` element for ``module_meta.xml``."""
    m = ET.Element(f"{{{ns}}}module", attrib={"identifier": identifier})
    ET.SubElement(m, f"{{{ns}}}title").text = title
    ET.SubElement(m, f"{{{ns}}}workflow_state").text = workflow_state
    ET.SubElement(m, f"{{{ns}}}position").text = str(position)
    ET.SubElement(m, f"{{{ns}}}require_sequential_progress").text = "false"
    ET.SubElement(m, f"{{{ns}}}locked").text = "false"
    ET.SubElement(m, f"{{{ns}}}items")
    return m


def _module_title(module_el: ET.Element) -> str:
    for child in list(module_el):
        if _local_name(child.tag) == "title":
            return (child.text or "").strip()
    return ""


def _build_module_meta_xml(
    d2l_module_titles: list[str],
    *,
    ns: str = _MODULE_META_NS,
    template_shell_modules: dict[str, ET.Element] | None = None,
    template_modules_in_order: list[ET.Element] | None = None,
    include_default_template_shell: bool = True,
) -> str:
    """Build the ``module_meta.xml`` content string.

    Layout:
        position 1  — Instructor Module (Do Not Publish) [unpublished]
        position 2  — Start Here
        positions 3 … N  — D2L course content modules
        position N+1  — Course Conclusion [must match template title]

    Args:
        d2l_module_titles: Ordered list of D2L content module titles
            (shell/carryover modules already excluded).
        ns: XML namespace string.

    Returns:
        UTF-8 XML string suitable for writing to ``module_meta.xml``.
    """
    # Register namespace so ET serialises without "ns0:" prefix
    ET.register_namespace("", ns)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

    root = ET.Element(
        f"{{{ns}}}modules",
        attrib={
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": f"{ns} {_MODULE_META_XSD}",
        },
    )

    def _position_module(module_el: ET.Element, position: int) -> ET.Element:
        module_copy = _clone_element(module_el)
        position_el = module_copy.find(f"{{{ns}}}position")
        if position_el is None:
            position_el = ET.SubElement(module_copy, f"{{{ns}}}position")
        position_el.text = str(position)
        return module_copy

    shell_modules = template_shell_modules or {}

    if template_modules_in_order:
        template_titles = [
            _module_title(module_el)
            for module_el in template_modules_in_order
            if _module_title(module_el)
        ]
        conclusion_module = None
        ordered_template_modules: list[ET.Element] = []
        for module_el in template_modules_in_order:
            title = _module_title(module_el)
            if not title:
                continue
            if title == _TEMPLATE_CONCLUSION_TITLE:
                conclusion_module = module_el
                continue
            ordered_template_modules.append(module_el)

        position = 1
        for module_el in ordered_template_modules:
            root.append(_position_module(module_el, position))
            position += 1

        for title in d2l_module_titles:
            root.append(
                _build_module_element(
                    ns,
                    _make_module_id(title),
                    title,
                    position=position,
                )
            )
            position += 1

        if conclusion_module is not None:
            root.append(_position_module(conclusion_module, position))

        ET.indent(root, space="  ")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
            root, encoding="unicode"
        )

    if include_default_template_shell:
        instructor_module = shell_modules.get(_TEMPLATE_INSTRUCTOR_MODULE_TITLE)
        if instructor_module is not None:
            root.append(_position_module(instructor_module, 1))
        else:
            root.append(
                _build_module_element(
                    ns,
                    _TEMPLATE_INSTRUCTOR_MODULE_ID,
                    _TEMPLATE_INSTRUCTOR_MODULE_TITLE,
                    position=1,
                    workflow_state="unpublished",
                )
            )

        start_here_module = shell_modules.get(_TEMPLATE_START_HERE_TITLE)
        if start_here_module is not None:
            root.append(_position_module(start_here_module, 2))
        else:
            root.append(
                _build_module_element(
                    ns,
                    _TEMPLATE_START_HERE_ID,
                    _TEMPLATE_START_HERE_TITLE,
                    position=2,
                )
            )

        # 3 … N. D2L content modules
        for idx, title in enumerate(d2l_module_titles, start=3):
            root.append(
                _build_module_element(
                    ns,
                    _make_module_id(title),
                    title,
                    position=idx,
                )
            )

        # N+1. Course Conclusion
        conclusion_pos = len(d2l_module_titles) + 3
        conclusion_module = shell_modules.get(_TEMPLATE_CONCLUSION_TITLE)
        if conclusion_module is not None:
            root.append(_position_module(conclusion_module, conclusion_pos))
        else:
            root.append(
                _build_module_element(
                    ns,
                    _TEMPLATE_CONCLUSION_ID,
                    _TEMPLATE_CONCLUSION_TITLE,
                    position=conclusion_pos,
                )
            )
    else:
        for idx, title in enumerate(d2l_module_titles, start=1):
            root.append(
                _build_module_element(
                    ns,
                    _make_module_id(title),
                    title,
                    position=idx,
                )
            )

    # Pretty serialisation
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _write_module_meta(
    unpack_dir: Path,
    d2l_module_titles: list[str],
    *,
    template_shell_modules: dict[str, ET.Element] | None = None,
    template_modules_in_order: list[ET.Element] | None = None,
    include_default_template_shell: bool = True,
    overwrite: bool = False,
) -> None:
    """Generate and write ``course_settings/module_meta.xml``.

    By default this is idempotent. When ``overwrite`` is true, the existing
    file is replaced with a merged module ordering.
    """
    cs_dir = unpack_dir / "course_settings"
    meta_path = cs_dir / "module_meta.xml"
    if meta_path.exists() and not overwrite:
        return
    cs_dir.mkdir(parents=True, exist_ok=True)
    xml = _build_module_meta_xml(
        d2l_module_titles,
        template_shell_modules=template_shell_modules,
        template_modules_in_order=template_modules_in_order,
        include_default_template_shell=include_default_template_shell,
    )
    meta_path.write_text(xml, encoding="utf-8")


def _load_template_manifest_root(template_package: Path) -> ET.Element:
    with ZipFile(template_package, "r") as zf:
        return ET.fromstring(zf.read("imsmanifest.xml"))


def _extract_item_title(item: ET.Element) -> tuple[ET.Element | None, str]:
    for child in list(item):
        if _local_name(child.tag) == "title":
            return child, (child.text or "").strip()
    return None, ""


def _find_first_organization(root: ET.Element) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == "organization":
            return element
    return None


def _find_template_top_level_item(
    organization: ET.Element,
    title: str,
) -> ET.Element | None:
    for item in organization.iter():
        if _local_name(item.tag) != "item":
            continue
        _title_el, item_title = _extract_item_title(item)
        if item_title.strip() == title:
            return item
    return None


def _resource_elements_by_id(root: ET.Element) -> dict[str, ET.Element]:
    resources: dict[str, ET.Element] = {}
    for element in root.iter():
        if _local_name(element.tag) != "resource":
            continue
        identifier = (element.attrib.get("identifier") or "").strip()
        if identifier:
            resources[identifier] = element
    return resources


def _resource_identifiers_from_item(item: ET.Element) -> set[str]:
    resource_ids: set[str] = set()
    identifierref = (item.attrib.get("identifierref") or "").strip()
    if identifierref:
        resource_ids.add(identifierref)
    for child in list(item):
        if _local_name(child.tag) == "item":
            resource_ids.update(_resource_identifiers_from_item(child))
    return resource_ids


def _resource_dependency_closure(
    resources_by_id: dict[str, ET.Element],
    initial_ids: Iterable[str],
) -> set[str]:
    resolved: set[str] = set()
    pending = [resource_id for resource_id in initial_ids if resource_id]
    while pending:
        current = pending.pop()
        if current in resolved:
            continue
        resolved.add(current)
        resource = resources_by_id.get(current)
        if resource is None:
            continue
        for child in list(resource):
            if _local_name(child.tag) != "dependency":
                continue
            dependency_id = (child.attrib.get("identifierref") or "").strip()
            if dependency_id and dependency_id not in resolved:
                pending.append(dependency_id)
    return resolved


def _resource_file_hrefs(resource: ET.Element) -> set[str]:
    hrefs: set[str] = set()
    href = (resource.attrib.get("href") or "").strip()
    if href:
        hrefs.add(href.replace("\\", "/"))
    for child in list(resource):
        if _local_name(child.tag) != "file":
            continue
        file_href = (child.attrib.get("href") or "").strip()
        if file_href:
            hrefs.add(file_href.replace("\\", "/"))
    return hrefs


def _copy_template_paths(
    template_package: Path,
    unpack_dir: Path,
    relative_paths: Iterable[str],
) -> set[str]:
    copied: set[str] = set()
    with ZipFile(template_package, "r") as zf:
        names = set(zf.namelist())
        for rel_path in sorted({path.replace("\\", "/") for path in relative_paths if path}):
            if rel_path not in names:
                continue
            target = unpack_dir / Path(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(rel_path))
            copied.add(rel_path)
    return copied


def _rewrite_copied_template_filebase_refs(
    unpack_dir: Path,
    copied_paths: Iterable[str],
) -> None:
    text_suffixes = {".html", ".htm", ".xml"}
    for rel_path in copied_paths:
        path = unpack_dir / Path(rel_path)
        if path.suffix.lower() not in text_suffixes or not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rewritten = _rewrite_template_asset_urls(
            text,
            relative_path=rel_path,
            use_template_web_resources=True,
        )
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def _copy_template_web_resources(template_package: Path, unpack_dir: Path) -> set[str]:
    with ZipFile(template_package, "r") as zf:
        web_resource_paths = [
            name
            for name in zf.namelist()
            if name.startswith("web_resources/") and not name.endswith("/")
        ]
    return _copy_template_paths(template_package, unpack_dir, web_resource_paths)


def _copy_full_template_payload(template_package: Path, unpack_dir: Path) -> set[str]:
    """Copy every template package file except the root manifest."""
    with ZipFile(template_package, "r") as zf:
        template_paths = [
            name
            for name in zf.namelist()
            if name and not name.endswith("/") and name != "imsmanifest.xml"
        ]
    return _copy_template_paths(template_package, unpack_dir, template_paths)


def _template_resource_ids_by_href(root: ET.Element) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for resource_id, resource in _resource_elements_by_id(root).items():
        href = (resource.attrib.get("href") or "").strip().replace("\\", "/")
        if href:
            mapping[href] = resource_id
    return mapping


def _all_template_resource_ids(root: ET.Element) -> set[str]:
    return set(_resource_elements_by_id(root).keys())


def _template_course_settings_resource_ids(
    template_package: Path,
    template_root: ET.Element,
) -> set[str]:
    """Return template resource identifiers referenced by course settings files.

    Full-shell mode copies ``course_settings/course_settings.xml`` and
    ``course_settings/syllabus.html`` from the template. Those files can
    reference resources that are not part of the shell module items
    themselves, such as the course card image and the linked syllabus page.
    """
    resource_ids: set[str] = set()
    resource_ids_by_href = _template_resource_ids_by_href(template_root)

    with ZipFile(template_package, "r") as zf:
        try:
            course_settings_xml = zf.read("course_settings/course_settings.xml").decode(
                "utf-8", errors="replace"
            )
        except KeyError:
            course_settings_xml = ""

        if course_settings_xml:
            try:
                settings_root = ET.fromstring(course_settings_xml)
            except ET.ParseError:
                settings_root = None
            if settings_root is not None:
                for element in settings_root.iter():
                    if not _local_name(element.tag).endswith("identifier_ref"):
                        continue
                    ref = (element.text or "").strip()
                    if ref:
                        resource_ids.add(ref)

        try:
            syllabus_html = zf.read("course_settings/syllabus.html").decode(
                "utf-8", errors="replace"
            )
        except KeyError:
            syllabus_html = ""

    if syllabus_html:
        for match in re.findall(
            r"\$WIKI_REFERENCE\$/pages/([A-Za-z0-9_-]+)",
            syllabus_html,
            flags=re.IGNORECASE,
        ):
            resource_ids.add(match)
        for match in re.findall(
            r"\$IMS-CC-FILEBASE\$/([^\"'<>\\s]+)",
            syllabus_html,
            flags=re.IGNORECASE,
        ):
            href = f"web_resources/{match.lstrip('/')}".replace("\\", "/")
            resource_id = resource_ids_by_href.get(href)
            if resource_id:
                resource_ids.add(resource_id)

    return resource_ids


def _inject_template_resources_into_manifest(
    unpack_dir: Path,
    template_root: ET.Element,
    resource_ids: set[str],
) -> None:
    manifest_path = unpack_dir / "imsmanifest.xml"
    if not manifest_path.exists() or not resource_ids:
        return
    tree = ET.parse(manifest_path)
    root = tree.getroot()

    resources_el: ET.Element | None = None
    for element in root.iter():
        if _local_name(element.tag) == "resources":
            resources_el = element
            break
    if resources_el is None:
        return

    existing_ids = {
        (element.attrib.get("identifier") or "").strip()
        for element in resources_el
        if _local_name(element.tag) == "resource"
    }
    template_resources = _resource_elements_by_id(template_root)
    appended = False
    for resource_id in sorted(resource_ids):
        if resource_id in existing_ids:
            continue
        resource = template_resources.get(resource_id)
        if resource is None:
            continue
        resources_el.append(_clone_element(resource))
        appended = True

    if appended:
        tree.write(manifest_path, encoding="utf-8", xml_declaration=True)


def _extract_template_top_level_items(template_organization: ET.Element) -> list[ET.Element]:
    """Return the template's visible top-level items in order.

    The template manifest uses a single untitled wrapper item around the
    top-level modules. When that structure is present, flatten it so we can
    merge the visible modules directly into the destination organization.
    """
    items = [child for child in list(template_organization) if _local_name(child.tag) == "item"]
    if len(items) == 1:
        title = _extract_item_title(items[0])[1].strip()
        nested_items = [
            child for child in list(items[0]) if _local_name(child.tag) == "item"
        ]
        if not title and nested_items:
            return nested_items
    return items


def _inject_full_template_items_into_manifest(
    unpack_dir: Path,
    template_root: ET.Element,
) -> None:
    """Inject the full template-course top-level items into the D2L manifest."""
    manifest_path = unpack_dir / "imsmanifest.xml"
    if not manifest_path.exists():
        return
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    organization = _find_first_organization(root)
    template_organization = _find_first_organization(template_root)
    if organization is None or template_organization is None:
        return

    existing_titles = {
        _extract_item_title(item)[1].strip()
        for item in organization
        if _local_name(item.tag) == "item"
    }
    template_items = _extract_template_top_level_items(template_organization)
    if not template_items:
        return

    prepend_items: list[ET.Element] = []
    append_items: list[ET.Element] = []
    for item in template_items:
        title = _extract_item_title(item)[1].strip()
        if title in existing_titles:
            continue
        if title == _TEMPLATE_CONCLUSION_TITLE:
            append_items.append(_clone_element(item))
        else:
            prepend_items.append(_clone_element(item))

    if not prepend_items and not append_items:
        return

    existing_children = list(organization)
    for child in existing_children:
        organization.remove(child)
    for item in prepend_items:
        organization.append(item)
    for child in existing_children:
        organization.append(child)
    for item in append_items:
        organization.append(item)

    tree.write(manifest_path, encoding="utf-8", xml_declaration=True)


def _inject_template_shell_items_into_manifest(
    unpack_dir: Path,
    template_root: ET.Element,
) -> None:
    manifest_path = unpack_dir / "imsmanifest.xml"
    if not manifest_path.exists():
        return
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    organization = _find_first_organization(root)
    template_organization = _find_first_organization(template_root)
    if organization is None or template_organization is None:
        return

    existing_titles = {
        _extract_item_title(item)[1].strip()
        for item in organization
        if _local_name(item.tag) == "item"
    }

    added = False
    prepend_items: list[ET.Element] = []
    append_items: list[ET.Element] = []
    for title in _TEMPLATE_SHELL_MODULE_TITLES:
        if title in existing_titles:
            continue
        item = _find_template_top_level_item(template_organization, title)
        if item is None:
            continue
        if title == _TEMPLATE_CONCLUSION_TITLE:
            append_items.append(_clone_element(item))
        else:
            prepend_items.append(_clone_element(item))
        added = True

    if not added:
        return

    existing_children = list(organization)
    for child in existing_children:
        organization.remove(child)
    for item in prepend_items:
        organization.append(item)
    for child in existing_children:
        organization.append(child)
    for item in append_items:
        organization.append(item)

    tree.write(manifest_path, encoding="utf-8", xml_declaration=True)


def _load_template_shell_modules(template_package: Path) -> dict[str, ET.Element]:
    modules: dict[str, ET.Element] = {}
    with ZipFile(template_package, "r") as zf:
        root = ET.fromstring(zf.read("course_settings/module_meta.xml"))
    for module in list(root):
        title = ""
        for child in list(module):
            if _local_name(child.tag) == "title":
                title = (child.text or "").strip()
                break
        if title in _TEMPLATE_SHELL_MODULE_TITLES:
            modules[title] = _clone_element(module)
    return modules


def _load_template_modules_in_order(template_package: Path) -> list[ET.Element]:
    modules: list[ET.Element] = []
    with ZipFile(template_package, "r") as zf:
        root = ET.fromstring(zf.read("course_settings/module_meta.xml"))
    for module in list(root):
        if _local_name(module.tag) != "module":
            continue
        modules.append(_clone_element(module))
    return modules


def _load_template_shell_payload(
    template_package: Path,
) -> tuple[ET.Element, dict[str, ET.Element], set[str]]:
    template_root = _load_template_manifest_root(template_package)
    template_org = _find_first_organization(template_root)
    if template_org is None:
        return template_root, {}, set()

    selected_resource_ids: set[str] = set()
    for title in _TEMPLATE_SHELL_MODULE_TITLES:
        item = _find_template_top_level_item(template_org, title)
        if item is None:
            continue
        selected_resource_ids.update(_resource_identifiers_from_item(item))

    resource_ids_by_href = _template_resource_ids_by_href(template_root)
    for href in _EXTRA_TEMPLATE_RESOURCE_HREFS:
        resource_id = resource_ids_by_href.get(href)
        if resource_id:
            selected_resource_ids.add(resource_id)

    selected_resource_ids.update(
        _template_course_settings_resource_ids(template_package, template_root)
    )

    resources_by_id = _resource_elements_by_id(template_root)
    selected_resource_ids = _resource_dependency_closure(
        resources_by_id,
        selected_resource_ids,
    )
    return template_root, _load_template_shell_modules(template_package), selected_resource_ids


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_page(
    path: str,
    title: str,
    body: str = "",
) -> tuple[PageRole, int | None, str]:
    """Classify a D2L page and extract module metadata.

    Args:
        path: Relative path of the HTML file within the package.
        title: Page ``<title>`` text.
        body: Full HTML content of the page (optional).  When supplied,
              content-based heuristics are applied as a fallback for pages
              whose path does not match the standard module-intro pattern.

    Returns:
        ``(role, module_number, chapter_title)`` where the last two are only
        meaningful for :attr:`PageRole.MODULE_INTRO` pages.
    """
    path_lower = path.lower()
    title_lower = title.lower().strip()

    # Module intro: XX-ChapterName/Introduction and Objectives.html
    m = _MODULE_FOLDER_RE.match(path)
    if m and ("introduction" in path_lower or "objectives" in path_lower):
        module_number: int | None = int(m.group(1))
        chapter_title = m.group(2).replace("_", " ").replace("-", " ").title().strip()
        return PageRole.MODULE_INTRO, module_number, chapter_title

    if "learning activities" in title_lower or "learning activities" in path_lower:
        module_number = None
        chapter_title = title.strip()
        mod_match = re.search(r"\b(?:module|chapter|unit)\s*(\d+)", title, re.IGNORECASE)
        if mod_match:
            module_number = int(mod_match.group(1))
        return PageRole.LEARNING_ACTIVITIES, module_number, chapter_title

    if any(kw in title_lower for kw in _WELCOME_KEYWORDS) or (
        "welcome" in path_lower and "instructor" in path_lower
    ):
        return PageRole.WELCOME_INSTRUCTOR, None, ""

    # Content-based fallback: pages whose path doesn't match the standard
    # module-intro pattern but whose body clearly contains both an
    # Introduction heading and an Objectives list are treated as MODULE_INTRO.
    if body and _INTRO_HEADING_RE.search(body) and _OBJECTIVES_HEADING_RE.search(body):
        mod_match = re.search(
            r"\b(?:module|chapter|unit)\s*(\d+)", title, re.IGNORECASE
        )
        module_number = int(mod_match.group(1)) if mod_match else None
        chapter_title = (
            re.sub(
                r"\s*[:\-\u2013\u2014]\s*(?:introduction|objectives?).*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()
            or title
        )
        return PageRole.MODULE_INTRO, module_number, chapter_title

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


def _clone_element(element: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(element, encoding="unicode"))


def _relative_path_prefix(path: str) -> str:
    parent = Path(path).parent
    if str(parent) in {"", "."}:
        return ""
    return "../" * len(parent.parts)


def _template_asset_prefix(
    path: str,
    *,
    use_template_web_resources: bool = True,
) -> str:
    prefix = _relative_path_prefix(path)
    asset_root = (
        "web_resources/template-images/icons/"
        if use_template_web_resources
        else "template-images/icons/"
    )
    return f"{prefix}{asset_root}"


def _rewrite_template_asset_urls(
    html: str,
    *,
    relative_path: str,
    use_template_web_resources: bool = True,
) -> str:
    """Rewrite ``$IMS-CC-FILEBASE$/template-images/...`` to relative paths.

    Args:
        html: HTML text to process.
        relative_path: Relative file path within the generated package.
    """
    prefix = _relative_path_prefix(relative_path)
    if use_template_web_resources:
        prefix = f"{prefix}web_resources/"

    def _sub_full(m: re.Match) -> str:
        return prefix + m.group(1)

    return _TEMPLATE_FILEBASE_URL_RE.sub(_sub_full, html)


def _clean_d2l_scaffold(body: str) -> str:
    """Strip Bootstrap/D2L navigation scaffolding from a processed D2L body."""
    body = _PRINT_LINK_RE.sub("", body)
    body = _BANNER_IMG_RE.sub("", body)
    body = _FOOTER_RE.sub("", body)
    body = _SCRIPT_RE.sub("", body)
    body = _EMPTY_PARA_RE.sub("", body)
    return body.strip()


def _plain_text(html_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _page_pair_key(path: str, module_number: int | None) -> str:
    if module_number is not None:
        return f"module:{module_number}"
    stem = Path(path).stem
    suffix_match = re.search(r"(\d+)$", stem)
    return f"suffix:{suffix_match.group(1) if suffix_match else 'root'}"


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


def _extract_list_items(list_html: str) -> list[str]:
    return [match.group(1).strip() for match in _LIST_ITEM_RE.finditer(list_html)]


def _dedupe_list_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = _plain_text(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item.strip())
    return deduped


def _render_list_html(items: list[str]) -> str:
    deduped = _dedupe_list_items(items)
    if not deduped:
        return ""
    rendered = "\n".join(f"  <li>{item}</li>" for item in deduped)
    return "<ul>\n" + rendered + "\n</ul>"


def _extract_checklist_candidates(body: str) -> list[str]:
    items: list[str] = []
    heading_match = _CHECKLIST_HEADING_RE.search(body)
    if heading_match is not None:
        list_match = _LIST_RE.search(body, heading_match.end())
        if list_match is not None:
            items.extend(_extract_list_items(list_match.group(0)))

    phrase_match = re.search(
        r"to meet the learning objectives",
        body,
        flags=re.IGNORECASE,
    )
    if phrase_match is not None:
        list_match = _LIST_RE.search(body, phrase_match.end())
        if list_match is not None:
            items.extend(_extract_list_items(list_match.group(0)))
    return _dedupe_list_items(items)


def _learning_section_spec(marker_html: str) -> tuple[str, str] | None:
    src_match = re.search(
        r'\bsrc\s*=\s*(["\'])(?P<src>[^"\']+)\1',
        marker_html,
        flags=re.IGNORECASE,
    )
    basename = ""
    if src_match is not None:
        basename = Path(src_match.group("src").replace("\\", "/")).name.lower()
    label_match = re.search(
        r'\bdata-template-label\s*=\s*(["\'])(?P<label>[^"\']+)\1',
        marker_html,
        flags=re.IGNORECASE,
    )
    text = (
        (label_match.group("label") if label_match is not None else _plain_text(marker_html))
        .lower()
        .strip()
    )

    if "do this" in text or basename == "dothis.png":
        return "Do This", "paper.png"
    if "explore this" in text or basename == "explorethis.png":
        return "Explore This", "folder.png"
    if "review this" in text or basename == "reviewthis.png":
        return "Review This", "circle-arrow.png"
    if "view this" in text or basename == "viewthis.png":
        return "View This", "video.png"
    if re.search(r"\bview\b", text):
        return "View", "video.png"
    if re.search(r"\bread\b", text):
        return "Read", "book.png"
    if "additional resources" in text:
        return "Additional Resources", "folder.png"
    return None


def _clean_learning_section_html(fragment: str) -> str:
    cleaned = _LEARNING_SEPARATOR_RE.sub("", fragment)
    cleaned = re.sub(r"<hr[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = _EMPTY_BLOCK_RE.sub("", cleaned)
    cleaned = re.sub(r"</?div[^>]*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _extract_learning_sections(body: str) -> tuple[str, list[tuple[str, str, str]]]:
    matches = [
        match
        for match in _LEARNING_SECTION_MARKER_RE.finditer(body)
        if _learning_section_spec(match.group("marker")) is not None
    ]
    if not matches:
        return "", []

    preamble = _clean_learning_section_html(body[: matches[0].start()])
    sections: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        label_icon = _learning_section_spec(match.group("marker"))
        if label_icon is None:
            continue
        content_start = match.end()
        content_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(body)
        )
        content_html = _clean_learning_section_html(body[content_start:content_end])
        if not content_html:
            continue
        label, icon_basename = label_icon
        sections.append((label, icon_basename, content_html))
    return preamble, sections


def _extract_do_this_items_from_learning_activities(body: str) -> list[str]:
    _, sections = _extract_learning_sections(body)
    for label, _icon, content_html in sections:
        if label.lower() != "do this":
            continue
        list_match = _LIST_RE.search(content_html)
        if list_match is None:
            return []
        return _dedupe_list_items(_extract_list_items(list_match.group(0)))
    return []


# ---------------------------------------------------------------------------
# Module intro  template filling
# ---------------------------------------------------------------------------

# Template body for module intro shell — icons use template-course
# web_resources/template-images/*
# We build this once with a placeholder and fill sections at runtime.
# (No need to read the template HTML for every module — the body structure
#  is stable; only the icon filenames and content differ.)

def _build_module_intro_body(
    *,
    asset_prefix: str,
    intro_content: str,
    objectives_content: str,
    checklist_content: str,
) -> str:
    return f"""\
<h2 style="color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;"><img role="presentation" src="{asset_prefix}star.png" alt="" width="45" height="45" loading="lazy"><strong>Introduction</strong></h2>
{intro_content}
<hr>
<h2><img role="presentation" src="{asset_prefix}bullseye.png" alt="" width="45" height="45" loading="lazy"><strong><span style="color: #ac1a2f;">Module Objectives</span></strong></h2>
<p>By the end of this module, students will be able to:</p>
{objectives_content}
<hr>
<h2><img role="presentation" src="{asset_prefix}checkmark.png" alt="" width="45" height="45" loading="lazy"><span style="color: #ac1a2f;"><strong>Module Checklist</strong></span></h2>
{checklist_content}
"""


def _default_module_checklist_html() -> str:
    return """<p>Complete the items listed below as you work through this module:</p>
<ul>
  <li>Read all assigned content and review lecture materials.</li>
  <li>Complete the learning activities for this module.</li>
  <li>Submit all assignments before the posted due date.</li>
</ul>"""


def _fill_module_intro(
    d2l_html: str,
    module_number: int | None,
    chapter_title: str,
    path_seed: str,
    extra_checklist_items: list[str] | None = None,
    *,
    use_template_web_resources: bool = False,
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

    checklist_items = _extract_checklist_candidates(body)
    if extra_checklist_items:
        checklist_items.extend(extra_checklist_items)
    checklist_html = (
        _render_list_html(checklist_items)
        if checklist_items
        else _default_module_checklist_html()
    )

    new_body = _build_module_intro_body(
        asset_prefix=_template_asset_prefix(
            path_seed,
            use_template_web_resources=use_template_web_resources,
        ),
        intro_content=intro_content,
        objectives_content=objectives_content,
        checklist_content=checklist_html,
    )

    mod_str = f"Module {module_number}: " if module_number else ""
    new_title = f"{mod_str}{chapter_title}: Introduction and Objectives"

    result = _replace_title(d2l_html, new_title)
    result = _replace_body(result, new_body)
    return result


def _render_learning_title(title: str, asset_prefix: str) -> str:
    safe_title = html.escape(title or "Learning Activities")
    return (
        f'<h2 style="color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;">'
        f'<strong><img role="presentation" src="{asset_prefix}bookmark.png" alt="" width="45" loading="lazy">{safe_title}</strong></h2>'
    )


def _render_learning_section(label: str, icon_basename: str, content_html: str, asset_prefix: str) -> str:
    safe_label = html.escape(label)
    return (
        f'<h2><img role="presentation" src="{asset_prefix}{icon_basename}" alt="" width="45" loading="lazy">'
        f'<span style="color: #ac1a2f;"><strong>{safe_label}</strong></span></h2>\n'
        f"{content_html}"
    )


def _fill_learning_activities_page(
    d2l_html: str,
    *,
    path_seed: str,
    use_template_web_resources: bool = False,
) -> str | None:
    body = _clean_d2l_scaffold(_extract_body(d2l_html))
    preamble, sections = _extract_learning_sections(body)
    if not sections:
        return None

    title = _extract_title(d2l_html) or "Learning Activities"
    asset_prefix = _template_asset_prefix(
        path_seed,
        use_template_web_resources=use_template_web_resources,
    )
    parts = [_render_learning_title(title, asset_prefix)]
    if preamble:
        parts.append(preamble)
    for index, (label, icon_basename, content_html) in enumerate(sections):
        parts.append("<hr>")
        parts.append(
            _render_learning_section(
                label,
                icon_basename,
                content_html,
                asset_prefix,
            )
        )
    parts.append('<hr style="border-top: 8px solid #AC1A2F;">')

    result = _replace_body(d2l_html, "\n".join(parts))
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
    *,
    use_template_web_resources: bool = False,
) -> str:
    """Inject the D2L instructor bio into the about-the-instructor template."""
    bio_fragment = _clean_instructor_bio(_extract_body(welcome_d2l_html))

    # Rewrite icon URLs in the template (they still have $IMS-CC-FILEBASE$ tokens)
    result = _rewrite_template_asset_urls(
        template_html,
        relative_path="wiki_content/about-the-instructor.html",
        use_template_web_resources=use_template_web_resources,
    )

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
    *,
    intro_checklist_handling: str = "rebuild-when-confident",
    learning_activities_handling: str = "preserve",
    full_template_shell: bool = False,
    seeded_starter_course: bool = False,
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
    if full_template_shell and seeded_starter_course:
        raise ValueError(
            "Seeded starter course mode cannot be combined with full template shell packaging."
        )

    # Load template wiki pages (used as shells / for standalone additions)
    template_pages = _load_template_wiki_pages(template_package)
    use_template_web_resources = bool(full_template_shell)
    template_root: ET.Element | None = None
    template_shell_modules: dict[str, ET.Element] | None = None
    template_modules_in_order: list[ET.Element] | None = None
    if full_template_shell:
        template_root = _load_template_manifest_root(template_package)
        template_shell_modules = _load_template_shell_modules(template_package)
        template_modules_in_order = _load_template_modules_in_order(template_package)
        copied_paths = _copy_full_template_payload(template_package, unpack_dir)
        _rewrite_copied_template_filebase_refs(unpack_dir, copied_paths)
        if template_root is not None:
            _inject_template_resources_into_manifest(
                unpack_dir,
                template_root,
                _all_template_resource_ids(template_root),
            )
            _inject_full_template_items_into_manifest(unpack_dir, template_root)

    # Survey all HTML files
    html_files = sorted(unpack_dir.rglob("*.html")) + sorted(unpack_dir.rglob("*.htm"))
    scanned_pages: list[ScannedPage] = []
    for html_file in html_files:
        rel = str(html_file.relative_to(unpack_dir).as_posix())
        content = html_file.read_text(encoding="utf-8", errors="replace")
        title = _extract_title(content)
        role, module_number, chapter_title = classify_page(rel, title, body=content)
        scanned_pages.append(
            ScannedPage(
                path=rel,
                html_file=html_file,
                content=content,
                title=title,
                role=role,
                module_number=module_number,
                chapter_title=chapter_title,
            )
        )

    learning_pages_by_key = {
        _page_pair_key(page.path, page.module_number): page
        for page in scanned_pages
        if page.role == PageRole.LEARNING_ACTIVITIES
    }

    welcome_path: str | None = None

    for page in scanned_pages:
        rel = page.path
        content = page.content
        role = page.role
        module_number = page.module_number
        chapter_title = page.chapter_title
        html_file = page.html_file

        if full_template_shell and rel.startswith("wiki_content/"):
            result.pages.append(
                MergedPageRecord(
                    original_path=rel,
                    role=PageRole.STANDALONE,
                    action="passthrough",
                )
            )
            continue

        if role == PageRole.MODULE_INTRO:
            if intro_checklist_handling == "preserve":
                result.pages.append(
                    MergedPageRecord(
                        original_path=rel,
                        role=role,
                        action="passthrough",
                        module_number=module_number,
                        chapter_title=chapter_title,
                    )
                )
                continue

            learning_page = learning_pages_by_key.get(_page_pair_key(rel, module_number))
            extra_checklist_items = (
                _extract_do_this_items_from_learning_activities(
                    _extract_body(learning_page.content)
                )
                if learning_page is not None
                else []
            )
            new_html = _fill_module_intro(
                d2l_html=content,
                module_number=module_number,
                chapter_title=chapter_title,
                path_seed=rel,
                extra_checklist_items=extra_checklist_items,
                use_template_web_resources=use_template_web_resources,
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

        elif role == PageRole.LEARNING_ACTIVITIES:
            if learning_activities_handling == "rebuild-when-confident":
                rebuilt = _fill_learning_activities_page(
                    content,
                    path_seed=rel,
                    use_template_web_resources=use_template_web_resources,
                )
                if rebuilt:
                    html_file.write_text(rebuilt, encoding="utf-8")
                    result.pages.append(
                        MergedPageRecord(
                            original_path=rel,
                            role=role,
                            action="template_wrapped",
                            module_number=module_number,
                            chapter_title=chapter_title,
                        )
                    )
                    continue

            result.pages.append(
                MergedPageRecord(
                    original_path=rel,
                    role=role,
                    action="passthrough",
                    module_number=module_number,
                    chapter_title=chapter_title,
                )
            )

        elif role == PageRole.WELCOME_INSTRUCTOR:
            if seeded_starter_course:
                result.pages.append(
                    MergedPageRecord(
                        original_path=rel,
                        role=role,
                        action="passthrough",
                    )
                )
                continue
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
    if (
        not seeded_starter_course
        and welcome_path is not None
        and "about-the-instructor.html" in template_pages
    ):
        about_html = _fill_about_instructor(
            welcome_d2l_html=welcome_content,  # type: ignore[possibly-undefined]
            template_html=template_pages["about-the-instructor.html"],
            use_template_web_resources=use_template_web_resources,
        )
        about_dest = (
            unpack_dir / "wiki_content" / "about-the-instructor.html"
            if full_template_shell
            else unpack_dir / "CourseOverview" / "About the Instructor.html"
        )
        about_dest.parent.mkdir(parents=True, exist_ok=True)
        about_dest.write_text(about_html, encoding="utf-8")
        result.added_template_pages.append(
            "wiki_content/about-the-instructor.html"
            if full_template_shell
            else "CourseOverview/About the Instructor.html"
        )

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
    for dest_rel in (
        [] if full_template_shell or seeded_starter_course else _STANDALONE_TEMPLATE_PAGES
    ):
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
        page_html = _rewrite_template_asset_urls(
            page_html,
            relative_path=dest_rel,
            use_template_web_resources=use_template_web_resources,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page_html, encoding="utf-8")
        result.added_template_pages.append(dest_rel)

    # ── Home page selection ────────────────────────────────────────────────
    # Detect the course-code prefix from the D2L manifest so we can pick the
    # correct divisional home-page template.
    manifest_path = next(unpack_dir.rglob("imsmanifest.xml"), None)
    course_prefix = _course_prefix_from_manifest(manifest_path) if manifest_path else ""
    home_variant = None
    if not seeded_starter_course:
        home_variant = _inject_home_page(
            unpack_dir,
            template_pages,
            course_prefix,
            use_template_web_resources=use_template_web_resources,
        )
    if home_variant:
        result.added_template_pages.append(
            f"wiki_content/home-page.html ({home_variant})"
        )

    # ── Module ordering (Canvas module_meta.xml) ───────────────────────────
    # Read the D2L manifest to get the ordered list of content modules, then
    # build module_meta.xml that places them between the template shell modules.
    d2l_modules = _read_d2l_module_titles_for_mode(
        unpack_dir,
        exclude_shell_titles=not full_template_shell,
        exclude_exact_titles=(
            {_module_title(module_el) for module_el in (template_modules_in_order or [])}
            if full_template_shell
            else ()
        ),
    )
    _write_module_meta(
        unpack_dir,
        d2l_modules,
        template_shell_modules=template_shell_modules,
        template_modules_in_order=template_modules_in_order if full_template_shell else None,
        include_default_template_shell=not seeded_starter_course,
        overwrite=full_template_shell,
    )

    return result

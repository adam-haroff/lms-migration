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
    ``course_settings/module_meta.xml`` is written into the package.  It places
    the template shell modules (Instructor Module, Start Here) first, the D2L
    content modules in the middle, and the Course Conclusion module last.
    Canvas uses this file when importing a Canvas Common Cartridge to define
    module titles, positions, and published states.  D2L modules whose title
    matches a template shell are excluded (they map to the template positions).

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
  rewritten to ``../TemplateAssets/{basename}`` so they resolve once Canvas
  imports the package.  The ``TemplateAssets/`` folder is already materialised
  by ``materialize_template_assets()`` earlier in the pipeline.
"""

from __future__ import annotations

import hashlib
import re
import textwrap
import xml.etree.ElementTree as ET
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
    page_html = _rewrite_template_asset_urls(page_html, depth=1)

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
        # Skip D2L shell/carryover modules — they map to template positions
        if title.lower() in _D2L_SHELL_MODULE_TITLES:
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


def _build_module_meta_xml(
    d2l_module_titles: list[str],
    *,
    ns: str = _MODULE_META_NS,
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

    # 1. Instructor Module
    root.append(
        _build_module_element(
            ns,
            _TEMPLATE_INSTRUCTOR_MODULE_ID,
            _TEMPLATE_INSTRUCTOR_MODULE_TITLE,
            position=1,
            workflow_state="unpublished",
        )
    )

    # 2. Start Here
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
    root.append(
        _build_module_element(
            ns,
            _TEMPLATE_CONCLUSION_ID,
            _TEMPLATE_CONCLUSION_TITLE,
            position=conclusion_pos,
        )
    )

    # Pretty serialisation
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _write_module_meta(unpack_dir: Path, d2l_module_titles: list[str]) -> None:
    """Generate and write ``course_settings/module_meta.xml``.

    Only writes if the file does not already exist (idempotent).
    """
    cs_dir = unpack_dir / "course_settings"
    meta_path = cs_dir / "module_meta.xml"
    if meta_path.exists():
        return
    cs_dir.mkdir(parents=True, exist_ok=True)
    xml = _build_module_meta_xml(d2l_module_titles)
    meta_path.write_text(xml, encoding="utf-8")


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
        role, module_number, chapter_title = classify_page(rel, title, body=content)

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

    # ── Home page selection ────────────────────────────────────────────────
    # Detect the course-code prefix from the D2L manifest so we can pick the
    # correct divisional home-page template.
    manifest_path = next(unpack_dir.rglob("imsmanifest.xml"), None)
    course_prefix = _course_prefix_from_manifest(manifest_path) if manifest_path else ""
    home_variant = _inject_home_page(unpack_dir, template_pages, course_prefix)
    if home_variant:
        result.added_template_pages.append(f"wiki_content/home-page.html ({home_variant})")

    # ── Module ordering (Canvas module_meta.xml) ───────────────────────────
    # Read the D2L manifest to get the ordered list of content modules, then
    # build module_meta.xml that places them between the template shell modules.
    d2l_modules = _read_d2l_module_titles(unpack_dir)
    _write_module_meta(unpack_dir, d2l_modules)

    return result

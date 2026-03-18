from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


_DOC_PAGE_KEYS = {
    "image_customizations": "template-image-customizations",
    "introduction_instructions": "template-introduction-and-instructions",
    "learning_activities_template": "module-learning-activities-template",
    "lesson_template": "module-lesson-title-template",
    "home_page": "wiki_content/home-page.html",
    "syllabus_online": "wiki_content/syllabus-2.html",
    "syllabus_f2f": "wiki_content/syllabus-f2f.html",
    "about_instructor": "wiki_content/about-the-instructor.html",
    "policies_support": "wiki_content/policies-and-support.html",
}
_DATE_SUFFIX_RE = re.compile(r"(?P<date>20\d{6})")
_DOCX_TEXT_RE = re.compile(r"\s+")
_XML_NS = {}


def resolve_default_template_package(workspace_root: Path) -> Path | None:
    template_dir = workspace_root / "resources" / "examples" / "template"
    if not template_dir.exists():
        return None

    candidates = [
        path
        for path in template_dir.glob("elearn-standard-template-export*.imscc")
        if path.is_file()
    ]
    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[int, str]:
        match = _DATE_SUFFIX_RE.search(path.stem)
        date_value = int(match.group("date")) if match is not None else 0
        return (date_value, path.name.lower())

    return max(candidates, key=sort_key)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value, flags=re.IGNORECASE)
    text = html.unescape(text).replace("\xa0", " ")
    return _DOCX_TEXT_RE.sub(" ", text).strip()


def _load_template_pages(template_package: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    with ZipFile(template_package, "r") as zf:
        for name in zf.namelist():
            lowered = name.lower()
            if not lowered.endswith(".html"):
                continue
            pages[name] = zf.read(name).decode("utf-8", errors="ignore")
    return pages


def _find_page_by_key(pages: dict[str, str], key: str) -> str:
    for path, content in pages.items():
        if key in path.lower():
            return content
    return ""


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _extract_item_title(item: ET.Element) -> str:
    for child in list(item):
        if _local_name(child.tag) == "title":
            return (child.text or "").strip()
    return ""


def _manifest_children_by_top_level(template_package: Path) -> dict[str, list[str]]:
    with ZipFile(template_package, "r") as zf:
        if "imsmanifest.xml" not in zf.namelist():
            return {}
        root = ET.fromstring(zf.read("imsmanifest.xml"))

    children_by_top_level: dict[str, list[str]] = {}
    for organization in [node for node in root.iter() if _local_name(node.tag) == "organization"]:
        top_level_items = [child for child in list(organization) if _local_name(child.tag) == "item"]
        if (
            len(top_level_items) == 1
            and not _extract_item_title(top_level_items[0])
            and any(_local_name(child.tag) == "item" for child in list(top_level_items[0]))
        ):
            top_level_items = [child for child in list(top_level_items[0]) if _local_name(child.tag) == "item"]
        for item in top_level_items:
            title = _extract_item_title(item)
            if not title:
                continue
            children = []
            for child_item in [child for child in list(item) if _local_name(child.tag) == "item"]:
                child_title = _extract_item_title(child_item)
                if child_title:
                    children.append(child_title)
            children_by_top_level[title] = children
    return children_by_top_level


def extract_template_standards(template_package: Path | None) -> dict:
    if template_package is None or not template_package.exists():
        return {
            "template_package": "",
            "visual": {},
            "shell": {},
            "content": {},
            "warnings": [],
        }

    pages = _load_template_pages(template_package)
    image_page = _find_page_by_key(pages, _DOC_PAGE_KEYS["image_customizations"])
    intro_page = _find_page_by_key(pages, _DOC_PAGE_KEYS["introduction_instructions"])
    learning_page = _find_page_by_key(pages, _DOC_PAGE_KEYS["learning_activities_template"])
    lesson_page = _find_page_by_key(pages, _DOC_PAGE_KEYS["lesson_template"])
    home_page = _find_page_by_key(pages, _DOC_PAGE_KEYS["home_page"])
    syllabus_online = _find_page_by_key(pages, _DOC_PAGE_KEYS["syllabus_online"])
    syllabus_f2f = _find_page_by_key(pages, _DOC_PAGE_KEYS["syllabus_f2f"])
    about_instructor = _find_page_by_key(pages, _DOC_PAGE_KEYS["about_instructor"])
    policies_support = _find_page_by_key(pages, _DOC_PAGE_KEYS["policies_support"])
    all_text = "\n".join(_strip_html(content) for content in pages.values())
    children_by_top_level = _manifest_children_by_top_level(template_package)

    color_match = re.search(r"\bac1a2f\b", image_page, flags=re.IGNORECASE)
    width_match = re.search(r"\b45px\b", image_page, flags=re.IGNORECASE)
    primary_heading_rule_match = re.search(
        r"border-bottom:\s*10px\s+solid\s+#ac1a2f",
        "\n".join(pages.values()),
        flags=re.IGNORECASE,
    )
    thick_divider_match = re.search(
        r"border-top:\s*8px\s+solid\s+#ac1a2f",
        "\n".join(pages.values()),
        flags=re.IGNORECASE,
    )
    home_gray_rule_match = re.search(
        r"border-bottom:\s*2px\s+solid\s+#cccccc",
        home_page,
        flags=re.IGNORECASE,
    )
    home_gray_bg_match = re.search(
        r"background:\s*#eeeeee",
        home_page,
        flags=re.IGNORECASE,
    )
    title_match = re.search(
        r"video title.*?transcript.*?timestamp.*?citation",
        _strip_html(learning_page),
        flags=re.IGNORECASE,
    )
    syllabus_online_text = _strip_html(syllabus_online)
    syllabus_f2f_text = _strip_html(syllabus_f2f)
    home_page_text = _strip_html(home_page)
    about_instructor_text = _strip_html(about_instructor)
    intro_text = _strip_html(intro_page)
    lesson_text = _strip_html(lesson_page)

    start_here_items = children_by_top_level.get("Start Here", [])
    instructor_items = children_by_top_level.get("Instructor Module (Do Not Publish)", [])
    lesson_module_items = next(
        (
            children
            for title, children in children_by_top_level.items()
            if title.lower().startswith("module 1:")
        ),
        [],
    )
    conclusion_items = next(
        (
            children
            for title, children in children_by_top_level.items()
            if title.lower().startswith("course conclusion")
        ),
        [],
    )

    course_credentials_in_shell = any(
        child.lower() == "course credentials"
        for children in children_by_top_level.values()
        for child in children
    )
    instructions_reference_course_credentials = "course credentials" in all_text.lower()
    course_overview_survey_replaced = "replaces the course overview survey" in intro_text.lower()
    paste_without_formatting_guidance_present = (
        "paste without formatting" in lesson_text.lower()
        or "paste text without formatting" in lesson_text.lower()
    )

    warnings: list[str] = []
    if instructions_reference_course_credentials and not course_credentials_in_shell:
        warnings.append(
            "Template instructions still mention Course Credentials, but the current template shell does not include that page."
        )

    return {
        "template_package": str(template_package),
        "visual": {
            "heading_icon_width_px": 45 if width_match is not None else None,
            "decorative_icon_alt_expected": "decorative" in image_page.lower(),
            "sinclair_red_hex": color_match.group(0).lower() if color_match is not None else "",
            "icon_text_can_be_copied_separately": "two separate elements" in image_page.lower(),
            "primary_page_heading_rule": "border-bottom: 10px solid #ac1a2f"
            if primary_heading_rule_match is not None
            else "",
            "thick_red_divider_rule": "border-top: 8px solid #ac1a2f"
            if thick_divider_match is not None
            else "",
            "internal_separator_rule": "unstyled <hr>"
            if "<hr" in "\n".join(pages.values()).lower()
            else "",
            "home_page_section_rule": "border-bottom: 2px solid #cccccc"
            if home_gray_rule_match is not None
            else "",
            "home_page_header_background": "#eeeeee" if home_gray_bg_match is not None else "",
        },
        "shell": {
            "start_here_items": start_here_items,
            "instructor_module_items": instructor_items,
            "lesson_module_items": lesson_module_items,
            "course_conclusion_items": conclusion_items,
            "course_credentials_in_shell": course_credentials_in_shell,
            "instructions_reference_course_credentials": instructions_reference_course_credentials,
        },
        "content": {
            "view_requires_video_title_transcript_timestamp_citation": title_match is not None,
            "paste_without_formatting_guidance_present": paste_without_formatting_guidance_present,
            "accordion_guidance_present": "accordion" in all_text.lower(),
            "start_here_replaces_course_overview_survey": course_overview_survey_replaced,
            "home_page_ai_notice_present": "this course is open to ai usage" in home_page_text.lower(),
            "home_page_ai_notice_conditional": "if your course policy allows ai use" in home_page_text.lower(),
            "home_page_links": [
                label
                for label in (
                    "Syllabus",
                    "Policies and Support",
                    "Course Q&A",
                    "AI Excellence Institute",
                )
                if label.lower() in home_page_text.lower()
            ],
            "syllabus_has_table_of_contents": "syllabus table of contents" in syllabus_online_text.lower(),
            "syllabus_has_return_to_toc_links": "return to table of contents" in syllabus_online_text.lower(),
            "syllabus_has_ai_disclosure_section": "use of artificial intelligence in creating this course" in syllabus_online_text.lower(),
            "syllabus_variants_present": [
                label
                for label, text in (
                    ("Online", syllabus_online_text),
                    ("Face-to-Face", syllabus_f2f_text),
                )
                if text
            ],
            "about_instructor_belonging_language_present": "sense of belonging" in about_instructor_text.lower(),
            "policies_support_page_present": bool(policies_support.strip()),
        },
        "warnings": warnings,
    }

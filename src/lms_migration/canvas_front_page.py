from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .canvas_api import (
    fetch_course_pages,
    normalize_base_url,
    set_course_front_page,
    update_course_default_view,
)
from .template_merger import home_page_variant_basename, home_page_variant_title


def _extract_course_prefix(course_code: str) -> str:
    match = re.search(r"[A-Za-z]+", course_code.strip())
    return match.group(0).lower() if match else ""


def _normalize_title(value: str) -> str:
    normalized = value.strip().replace("\xa0", " ")
    normalized = re.sub(r"\s*-\s*", " - ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.casefold()


def _expected_page_slug(course_prefix: str) -> str:
    basename = home_page_variant_basename(course_prefix)
    if basename.endswith(".html"):
        basename = basename[:-5]
    return basename


def _score_home_page_candidate(
    page: dict[str, Any], *, expected_title: str, expected_slug: str
) -> int:
    title = str(page.get("title") or "").strip()
    url = str(page.get("url") or "").strip()
    score = 0
    matched = False
    if url == expected_slug:
        score += 100
        matched = True
    elif url.startswith(f"{expected_slug}-"):
        score += 80
        matched = True

    if _normalize_title(title) == _normalize_title(expected_title):
        score += 90
        matched = True
    elif _normalize_title(title).startswith(f"{_normalize_title(expected_title)} "):
        score += 60
        matched = True

    if not matched:
        return 0

    if bool(page.get("published", False)):
        score += 10
    if bool(page.get("front_page", False)):
        score += 5
    return score


def _pick_home_page(
    pages: list[dict[str, Any]], *, course_prefix: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    expected_title = home_page_variant_title(course_prefix)
    expected_slug = _expected_page_slug(course_prefix)
    fallback_title = "Home Page"
    fallback_slug = "home-page"

    scored: list[tuple[int, dict[str, Any]]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        scored.append(
            (
                _score_home_page_candidate(
                    page,
                    expected_title=expected_title,
                    expected_slug=expected_slug,
                ),
                page,
            )
        )

    best = max(scored, key=lambda item: item[0], default=(0, None))
    if best[1] is not None and best[0] > 0:
        return best[1], {
            "expected_title": expected_title,
            "expected_slug": expected_slug,
            "fallback_title": fallback_title,
            "fallback_slug": fallback_slug,
            "used_fallback": False,
        }

    fallback = max(
        (
            (
                _score_home_page_candidate(
                    page,
                    expected_title=fallback_title,
                    expected_slug=fallback_slug,
                ),
                page,
            )
            for page in pages
            if isinstance(page, dict)
        ),
        key=lambda item: item[0],
        default=(0, None),
    )
    if fallback[1] is not None and fallback[0] > 0:
        return fallback[1], {
            "expected_title": expected_title,
            "expected_slug": expected_slug,
            "fallback_title": fallback_title,
            "fallback_slug": fallback_slug,
            "used_fallback": True,
        }

    return None, {
        "expected_title": expected_title,
        "expected_slug": expected_slug,
        "fallback_title": fallback_title,
        "fallback_slug": fallback_slug,
        "used_fallback": False,
    }


def auto_set_course_front_page(
    *,
    base_url: str,
    course_id: str,
    token: str,
    course_code: str,
    output_json_path: Path,
    dry_run: bool = False,
) -> Path:
    normalized_base = normalize_base_url(base_url)
    pages = fetch_course_pages(
        base_url=normalized_base,
        course_id=course_id,
        token=token,
    )
    course_prefix = _extract_course_prefix(course_code)
    chosen_page, selection = _pick_home_page(pages, course_prefix=course_prefix)

    changed = False
    selected_page_url = ""
    selected_page_title = ""
    if chosen_page is not None:
        selected_page_url = str(chosen_page.get("url") or "").strip()
        selected_page_title = str(chosen_page.get("title") or "").strip()
        if selected_page_url and not dry_run:
            set_course_front_page(
                base_url=normalized_base,
                course_id=course_id,
                page_url=selected_page_url,
                token=token,
                publish=True,
            )
            update_course_default_view(
                base_url=normalized_base,
                course_id=course_id,
                token=token,
                default_view="wiki",
            )
        changed = bool(selected_page_url) and not dry_run

    payload = {
        "course_id": str(course_id),
        "course_code": course_code,
        "course_prefix": course_prefix,
        "dry_run": dry_run,
        "summary": {
            "page_selected": bool(chosen_page),
            "front_page_set": changed,
            "used_fallback": bool(selection.get("used_fallback", False)),
        },
        "selection": {
            **selection,
            "selected_page_url": selected_page_url,
            "selected_page_title": selected_page_title,
        },
        "pages_scanned": [
            {
                "url": str(page.get("url") or "").strip(),
                "title": str(page.get("title") or "").strip(),
                "published": bool(page.get("published", False)),
                "front_page": bool(page.get("front_page", False)),
            }
            for page in pages
            if isinstance(page, dict)
        ],
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set the correct divisional home page as the Canvas Front Page."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--course-code", required=True)
    parser.add_argument("--output-json", required=True, dest="output_json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    auto_set_course_front_page(
        base_url=args.base_url,
        course_id=str(args.course_id),
        token=args.token,
        course_code=str(args.course_code),
        output_json_path=Path(args.output_json),
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request


class CanvasAPIError(RuntimeError):
    """Raised when a Canvas API request fails."""


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise CanvasAPIError("Canvas base URL is required.")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def fetch_content_migrations(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/content_migrations"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course(
    *,
    base_url: str,
    course_id: str,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}"
    payload, _ = _request_json(url=url, token=token)
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas API response for course.")
    return payload


def fetch_migration_issues(
    *,
    base_url: str,
    course_id: str,
    migration_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/content_migrations/{migration_id}/migration_issues"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_files(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/files"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_pages(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/pages"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_modules(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/modules"
    first_url = _build_url(base, path, {"per_page": per_page, "include[]": "items"})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_assignments(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/assignments"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_discussion_topics(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/discussion_topics"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_announcements(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = "/api/v1/announcements"
    first_url = _build_url(
        base,
        path,
        {
            "per_page": per_page,
            "context_codes[]": f"course_{course_id}",
            "active_only": "false",
            "latest_only": "false",
        },
    )
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_course_page(
    *,
    base_url: str,
    course_id: str,
    page_url: str,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    page_part = parse.quote(page_url.strip(), safe="")
    url = f"{base}/api/v1/courses/{course_id}/pages/{page_part}"
    payload, _ = _request_json(url=url, token=token)
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas page response format.")
    return payload


def update_course_page_body(
    *,
    base_url: str,
    course_id: str,
    page_url: str,
    body_html: str,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    page_part = parse.quote(page_url.strip(), safe="")
    url = f"{base}/api/v1/courses/{course_id}/pages/{page_part}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data={"wiki_page[body]": body_html},
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas page update response format.")
    return payload


def _fetch_paginated_list(*, first_url: str, token: str) -> list[dict[str, Any]]:
    if not token.strip():
        raise CanvasAPIError("Canvas API token is required.")

    results: list[dict[str, Any]] = []
    url: str | None = first_url

    while url:
        payload, headers = _request_json(url=url, token=token)
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).strip().lower()
            if status == "unauthenticated":
                raise CanvasAPIError("Unauthenticated. Check token and permissions.")
            if payload.get("errors"):
                raise CanvasAPIError(f"Canvas API returned errors: {payload['errors']}")
            raise CanvasAPIError("Unexpected Canvas API response format (expected list).")

        if not isinstance(payload, list):
            raise CanvasAPIError("Unexpected Canvas API response type.")

        for item in payload:
            if isinstance(item, dict):
                results.append(item)

        url = _parse_next_link(headers.get("Link"))

    return results


def _request_json(
    *,
    url: str,
    token: str,
    method: str = "GET",
    form_data: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/json",
    }
    encoded_data = None
    if form_data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        encoded_data = parse.urlencode(form_data, doseq=True).encode("utf-8")

    req = request.Request(
        url,
        data=encoded_data,
        headers=headers,
        method=method,
    )

    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return [], resp.headers
            return json.loads(raw), resp.headers
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = f"Canvas API HTTP {exc.code}"
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if parsed.get("status") == "unauthenticated":
                    message = "Unauthenticated. Check token and permissions."
                elif parsed.get("errors"):
                    message = f"{message}: {parsed['errors']}"
                elif parsed.get("message"):
                    message = f"{message}: {parsed['message']}"
                else:
                    message = f"{message}: {body[:200]}"
            else:
                message = f"{message}: {body[:200]}"
        raise CanvasAPIError(message) from exc
    except error.URLError as exc:
        raise CanvasAPIError(f"Could not connect to Canvas API: {exc.reason}") from exc


def _build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    query = parse.urlencode(params or {}, doseq=True)
    if query:
        return f"{base_url}{path}?{query}"
    return f"{base_url}{path}"


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None

    for part in link_header.split(","):
        section = part.strip()
        if not section:
            continue
        fragments = [frag.strip() for frag in section.split(";")]
        if not fragments:
            continue
        url_part = fragments[0]
        rel_parts = [frag for frag in fragments[1:] if frag.startswith("rel=")]
        if not rel_parts:
            continue
        rel_value = rel_parts[0].split("=", 1)[1].strip('"')
        if rel_value != "next":
            continue
        if url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None

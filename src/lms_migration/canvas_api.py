from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import re
from typing import Any
from urllib import error, parse, request
import uuid


class CanvasAPIError(RuntimeError):
    """Raised when a Canvas API request fails."""


def normalize_base_url(base_url: str) -> str:
    from urllib.parse import urlparse, urlunparse

    value = base_url.strip().rstrip("/")
    if not value:
        raise CanvasAPIError("Canvas base URL is required.")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    if value.startswith("http://"):
        raise CanvasAPIError(
            "Canvas base URL must use HTTPS. Plain HTTP is rejected to "
            "prevent API tokens from being transmitted in cleartext."
        )
    # Strip any path component (e.g. /courses/15610) — only scheme + host is needed.
    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


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


def update_course_default_view(
    *,
    base_url: str,
    course_id: str,
    token: str,
    default_view: str = "wiki",
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data={"course[default_view]": default_view},
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas course update response format.")
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


def fetch_course_folders(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/v1/courses/{course_id}/folders"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def create_course_folder(
    *,
    base_url: str,
    course_id: str,
    token: str,
    name: str,
    parent_folder_id: str | int | None = None,
    parent_folder_path: str | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    if parent_folder_id is not None:
        url = f"{base}/api/v1/folders/{parent_folder_id}/folders"
    else:
        url = f"{base}/api/v1/courses/{course_id}/folders"
    form_data: dict[str, Any] = {"name": name}
    if parent_folder_path:
        form_data["parent_folder_path"] = parent_folder_path
    payload, _ = _request_json(
        url=url,
        token=token,
        method="POST",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas folder creation response format.")
    return payload


def move_course_file(
    *,
    base_url: str,
    file_id: str | int,
    token: str,
    parent_folder_id: str | int,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/files/{file_id}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data={"parent_folder_id": str(parent_folder_id)},
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas file update response format.")
    return payload


def delete_canvas_file(
    *,
    base_url: str,
    file_id: str | int,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/files/{file_id}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="DELETE",
    )
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and not payload:
        return {}
    raise CanvasAPIError("Unexpected Canvas file delete response format.")


def delete_canvas_folder(
    *,
    base_url: str,
    folder_id: str | int,
    token: str,
    force: bool = False,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = _build_url(
        base,
        f"/api/v1/folders/{folder_id}",
        {"force": "true"} if force else None,
    )
    payload, _ = _request_json(
        url=url,
        token=token,
        method="DELETE",
    )
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and not payload:
        return {}
    raise CanvasAPIError("Unexpected Canvas folder delete response format.")


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


def create_course_module(
    *,
    base_url: str,
    course_id: str,
    token: str,
    name: str,
    position: int | None = None,
    published: bool | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/modules"
    form_data: dict[str, Any] = {"module[name]": name}
    if position is not None:
        form_data["module[position]"] = str(position)
    if published is not None:
        form_data["module[published]"] = "true" if published else "false"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="POST",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas module creation response format.")
    return payload


def create_course_module_item(
    *,
    base_url: str,
    course_id: str,
    module_id: str | int,
    token: str,
    item_type: str,
    title: str | None = None,
    page_url: str | None = None,
    content_id: str | int | None = None,
    position: int | None = None,
    indent: int | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/modules/{module_id}/items"
    form_data: dict[str, Any] = {"module_item[type]": item_type}
    if title is not None:
        form_data["module_item[title]"] = title
    if page_url is not None:
        form_data["module_item[page_url]"] = page_url
    if content_id is not None:
        form_data["module_item[content_id]"] = str(content_id)
    if position is not None:
        form_data["module_item[position]"] = str(position)
    if indent is not None:
        form_data["module_item[indent]"] = str(indent)
    payload, _ = _request_json(
        url=url,
        token=token,
        method="POST",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas module item creation response format.")
    return payload


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


def fetch_course_assignment(
    *,
    base_url: str,
    course_id: str,
    assignment_id: str | int,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}"
    payload, _ = _request_json(url=url, token=token)
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas assignment response format.")
    return payload


def fetch_new_quizzes(
    *,
    base_url: str,
    course_id: str,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/quiz/v1/courses/{course_id}/quizzes"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def fetch_new_quiz_items(
    *,
    base_url: str,
    course_id: str,
    assignment_id: str | int,
    token: str,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    base = normalize_base_url(base_url)
    path = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items"
    first_url = _build_url(base, path, {"per_page": per_page})
    return _fetch_paginated_list(first_url=first_url, token=token)


def update_new_quiz_item_body(
    *,
    base_url: str,
    course_id: str,
    assignment_id: str | int,
    item_id: str | int,
    item_body_html: str,
    token: str,
    entry_type: str | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = (
        f"{base}/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items/{item_id}"
    )
    form_data: dict[str, Any] = {"item[entry][item_body]": item_body_html}
    if entry_type:
        form_data["item[entry_type]"] = entry_type
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PATCH",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected New Quiz item update response format.")
    return payload


def update_course_assignment_description(
    *,
    base_url: str,
    course_id: str,
    assignment_id: str | int,
    description_html: str,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data={"assignment[description]": description_html},
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas assignment update response format.")
    return payload


def update_course_assignment(
    *,
    base_url: str,
    course_id: str,
    assignment_id: str | int,
    token: str,
    description_html: str | None = None,
    published: bool | None = None,
    points_possible: float | int | None = None,
    submission_types: list[str] | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}"
    form_data: dict[str, Any] = {}
    if description_html is not None:
        form_data["assignment[description]"] = description_html
    if published is not None:
        form_data["assignment[published]"] = "true" if published else "false"
    if points_possible is not None:
        form_data["assignment[points_possible]"] = str(points_possible)
    if submission_types is not None:
        form_data["assignment[submission_types][]"] = [
            str(value) for value in submission_types
        ]
    if not form_data:
        raise CanvasAPIError("At least one assignment field must be provided.")
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas assignment update response format.")
    return payload


def upload_course_file(
    *,
    base_url: str,
    course_id: str,
    folder_id: str | int,
    file_path: str | Path,
    token: str,
    on_duplicate: str = "rename",
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    local_path = Path(file_path)
    if not local_path.exists():
        raise CanvasAPIError(f"File does not exist: {local_path}")
    mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    slot_url = f"{base}/api/v1/folders/{folder_id}/files"
    slot_payload, _ = _request_json(
        url=slot_url,
        token=token,
        method="POST",
        form_data={
            "name": local_path.name,
            "size": str(local_path.stat().st_size),
            "content_type": mime_type,
            "on_duplicate": on_duplicate,
        },
    )
    if not isinstance(slot_payload, dict):
        raise CanvasAPIError("Unexpected Canvas file-upload slot response format.")

    upload_url = str(slot_payload.get("upload_url") or "").strip()
    upload_params = slot_payload.get("upload_params") or {}
    if not upload_url or not isinstance(upload_params, dict):
        raise CanvasAPIError("Canvas did not return a usable file upload URL.")

    location_url, direct_payload = _post_multipart_file_and_capture_location(
        url=upload_url,
        fields={str(k): str(v) for k, v in upload_params.items()},
        file_path=local_path,
    )
    if isinstance(direct_payload, dict) and direct_payload.get("id"):
        return direct_payload

    if not location_url:
        raise CanvasAPIError(
            f"Canvas did not return a completion URL for uploaded file {local_path.name}."
        )

    try:
        completed_payload, _ = _request_json(url=location_url, token=token)
    except CanvasAPIError:
        completed_payload, _ = _request_json(
            url=location_url,
            token=token,
            method="POST",
            form_data={},
        )
    if not isinstance(completed_payload, dict):
        raise CanvasAPIError("Unexpected Canvas file completion response format.")
    return completed_payload


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


def fetch_course_discussion_topic(
    *,
    base_url: str,
    course_id: str,
    topic_id: str | int,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
    payload, _ = _request_json(url=url, token=token)
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas discussion response format.")
    return payload


def update_discussion_topic_message(
    *,
    base_url: str,
    course_id: str,
    topic_id: str | int,
    message_html: str,
    token: str,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data={"message": message_html},
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas discussion update response format.")
    return payload


def update_discussion_topic(
    *,
    base_url: str,
    course_id: str,
    topic_id: str | int,
    token: str,
    message_html: str | None = None,
    published: bool | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
    form_data: dict[str, Any] = {}
    if message_html is not None:
        form_data["message"] = message_html
    if published is not None:
        form_data["published"] = "true" if published else "false"
    if not form_data:
        raise CanvasAPIError("At least one discussion field must be provided.")
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas discussion update response format.")
    return payload


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


def update_course_page(
    *,
    base_url: str,
    course_id: str,
    page_url: str,
    token: str,
    body_html: str | None = None,
    title: str | None = None,
    published: bool | None = None,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    page_part = parse.quote(page_url.strip(), safe="")
    url = f"{base}/api/v1/courses/{course_id}/pages/{page_part}"
    form_data: dict[str, Any] = {}
    if body_html is not None:
        form_data["wiki_page[body]"] = body_html
    if title is not None:
        form_data["wiki_page[title]"] = title
    if published is not None:
        form_data["wiki_page[published]"] = "true" if published else "false"
    if not form_data:
        raise CanvasAPIError("At least one page field must be provided.")
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas page update response format.")
    return payload


def set_course_front_page(
    *,
    base_url: str,
    course_id: str,
    page_url: str,
    token: str,
    publish: bool = True,
) -> dict[str, Any]:
    base = normalize_base_url(base_url)
    page_part = parse.quote(page_url.strip(), safe="")
    url = f"{base}/api/v1/courses/{course_id}/pages/{page_part}"
    form_data: dict[str, Any] = {"wiki_page[front_page]": "true"}
    if publish:
        form_data["wiki_page[published]"] = "true"
    payload, _ = _request_json(
        url=url,
        token=token,
        method="PUT",
        form_data=form_data,
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas front-page update response format.")
    return payload


def create_or_update_course_page(
    *,
    base_url: str,
    course_id: str,
    title: str,
    body_html: str,
    token: str,
    published: bool = False,
) -> dict[str, Any]:
    """Create a new wiki page, or update it if a page with that title already exists.

    Args:
        base_url: Canvas instance root URL.
        course_id: Numeric Canvas course ID.
        title: Human-readable page title (also determines the URL slug).
        body_html: Full ``<body>`` inner HTML for the page.
        token: Canvas API bearer token.
        published: Whether to publish the page immediately (default False).

    Returns:
        The Canvas page object returned by the API.
    """
    base = normalize_base_url(base_url)
    # Derive the slug Canvas will assign: lowercase, spaces→dashes (approximate)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    form_data: dict[str, Any] = {
        "wiki_page[title]": title,
        "wiki_page[body]": body_html,
        "wiki_page[published]": "true" if published else "false",
    }

    # Try PUT first (update if exists, 404 if not)
    put_url = f"{base}/api/v1/courses/{course_id}/pages/{parse.quote(slug, safe='')}"
    try:
        payload, _ = _request_json(
            url=put_url, token=token, method="PUT", form_data=form_data
        )
        if isinstance(payload, dict):
            return payload
    except CanvasAPIError as exc:
        if "404" not in str(exc):
            raise

    # Page doesn't exist – create via POST
    post_url = f"{base}/api/v1/courses/{course_id}/pages"
    payload, _ = _request_json(
        url=post_url, token=token, method="POST", form_data=form_data
    )
    if not isinstance(payload, dict):
        raise CanvasAPIError("Unexpected Canvas page creation response format.")
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
            raise CanvasAPIError(
                "Unexpected Canvas API response format (expected list)."
            )

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


def _encode_multipart(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n'
                f"\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )
    file_bytes = file_path.read_bytes()
    file_name = file_path.name
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n"
            "\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _post_multipart_file_and_capture_location(
    *,
    url: str,
    fields: dict[str, str],
    file_path: Path,
) -> tuple[str, dict[str, Any] | None]:
    body, content_type = _encode_multipart(fields, "file", file_path)
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    opener = request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload: dict[str, Any] | None = None
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    payload = parsed
            location = str(resp.headers.get("Location") or "").strip()
            return location, payload
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303):
            location = str(exc.headers.get("Location") or "").strip()
            return location, None
        body_text = exc.read().decode("utf-8", errors="replace")
        raise CanvasAPIError(f"File upload HTTP {exc.code}: {body_text[:200]}") from exc
    except error.URLError as exc:
        raise CanvasAPIError(f"File upload network error: {exc.reason}") from exc


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

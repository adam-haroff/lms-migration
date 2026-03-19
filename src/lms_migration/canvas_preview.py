"""canvas_preview.py — Upload a converted .imscc to an existing Canvas sandbox
course and return page preview URLs for visual review before instructor delivery.

Primary mode — existing course (most users):
    Credentials are read from environment variables (or a .env file):

        CANVAS_SANDBOX_URL          e.g. https://yourschool.instructure.com
        CANVAS_SANDBOX_TOKEN        Personal access token
        CANVAS_SANDBOX_COURSE_ID    ID of your existing sandbox course

    The course ID is a number visible in the Canvas URL:
        /courses/12345  →  CANVAS_SANDBOX_COURSE_ID=12345
    Update this value in .env whenever your sandbox is reset.

Secondary mode — create/delete temporary course (requires account-admin rights):
    Set CANVAS_SANDBOX_ACCOUNT_ID instead of CANVAS_SANDBOX_COURSE_ID.
    A throw-away unpublished course is created, used, and deleted automatically.

Security contract
-----------------
* Credentials are read from environment only — never from CLI arguments.
* All API calls use HTTPS only; http:// is rejected at URL-normalisation.
* The token is never written to logs, reports, or exception messages.
* Existing os.environ values always take precedence over any .env file.
* In create-mode the temporary course is deleted after preview (or on failure)
  unless --keep-sandbox is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

from .canvas_api import CanvasAPIError, create_or_update_course_page, normalize_base_url

# ─── Errors ───────────────────────────────────────────────────────────────────


class CanvasPreviewError(RuntimeError):
    """Raised for preview-pipeline failures.  Token is always redacted."""


_BODY_CONTENT_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)


# ─── Credential helpers ───────────────────────────────────────────────────────


def _load_dotenv(path: Path) -> None:
    """Load ``KEY=VALUE`` pairs from *path* into :data:`os.environ`.

    * Lines starting with ``#`` and blank lines are skipped.
    * Quoted values (single or double-quoted) are unquoted.
    * Existing ``os.environ`` entries are **not** overwritten — shell
      environment always takes precedence over the file.

    Args:
        path: Path to the ``.env`` file.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
            raw = raw[1:-1]
        if key and key not in os.environ:
            os.environ[key] = raw


def _require_env(key: str) -> str:
    """Return ``os.environ[key]``, raising :exc:`CanvasPreviewError` if absent.

    The key *name* appears in the error; the value is never printed.

    Args:
        key: Environment variable name.

    Returns:
        Non-empty stripped value.

    Raises:
        :exc:`CanvasPreviewError` if the variable is absent or empty.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        raise CanvasPreviewError(
            f"Required environment variable {key!r} is not set. "
            "Set it in the shell or provide a --env file."
        )
    return value


def _redact(message: str, token: str) -> str:
    """Replace every occurrence of *token* in *message* with ``[REDACTED]``."""
    if not token:
        return message
    return message.replace(token, "[REDACTED]")


# ─── Low-level API helpers ────────────────────────────────────────────────────


def _api_get(url: str, token: str) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CanvasPreviewError(
            _redact(f"Canvas GET HTTP {exc.code}: {body[:200]}", token)
        ) from exc
    except error.URLError as exc:
        raise CanvasPreviewError(f"Network error: {exc.reason}") from exc


def _api_post_form(
    url: str,
    token: str,
    data: dict[str, str],
    method: str = "POST",
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = parse.urlencode(data).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise CanvasPreviewError(
            _redact(f"Canvas {method} HTTP {exc.code}: {body_text[:200]}", token)
        ) from exc
    except error.URLError as exc:
        raise CanvasPreviewError(f"Network error: {exc.reason}") from exc


def _api_delete(
    url: str,
    token: str,
    data: dict[str, str] | None = None,
) -> None:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    body = None
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = parse.urlencode(data).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="DELETE")
    try:
        with request.urlopen(req, timeout=45) as resp:
            resp.read()
    except error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise CanvasPreviewError(
            _redact(f"Canvas DELETE HTTP {exc.code}: {body_text[:200]}", token)
        ) from exc
    except error.URLError as exc:
        raise CanvasPreviewError(f"Network error: {exc.reason}") from exc


# ─── Multipart file upload (no auth header) ───────────────────────────────────


def _encode_multipart(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> tuple[bytes, str]:
    """Encode *fields* + one binary file into ``multipart/form-data``.

    Args:
        fields: Ordered text fields to prefix before the file part.
        file_field: Form field name for the file (typically ``"file"``).
        file_path: Local path to the file to include.

    Returns:
        ``(body_bytes, content_type_header_value)`` where the Content-Type
        includes the boundary required by the server to parse the body.
    """
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
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _post_multipart_no_auth(
    *,
    url: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> Any:
    """POST multipart form data to *url* **without** an Authorization header.

    Used for the Canvas pre-signed upload step, where credentials are already
    embedded in the signed URL or *fields* (e.g. S3 upload params).

    Args:
        url: Destination URL (HTTPS).
        fields: Text fields from the Canvas ``pre_attachment.upload_params`` response.
        file_field: Name of the file form field (typically ``"file"``).
        file_path: Local path to the package file.

    Returns:
        Decoded JSON response, or an empty dict for empty / non-JSON responses.
    """
    body, content_type = _encode_multipart(fields, file_field, file_path)
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except error.HTTPError as exc:
        # Canvas file upload may return 301/302 on success; treat those as OK.
        if exc.code in (301, 302):
            return {}
        body_text = exc.read().decode("utf-8", errors="replace")
        raise CanvasPreviewError(
            f"File upload HTTP {exc.code}: {body_text[:200]}"
        ) from exc
    except error.URLError as exc:
        raise CanvasPreviewError(f"File upload network error: {exc.reason}") from exc


# ─── Sandbox course lifecycle ─────────────────────────────────────────────────


def create_sandbox_course(
    *,
    base_url: str,
    account_id: str,
    token: str,
) -> str:
    """Create a throw-away unpublished course in the sandbox account.

    Args:
        base_url: Canvas base URL (HTTPS only; enforced by :func:`normalize_base_url`).
        account_id: Canvas account ID string or ``"self"``.
        token: API token.

    Returns:
        The new course ID as a string.

    Raises:
        :exc:`CanvasPreviewError` on failure.
    """
    base = normalize_base_url(base_url)
    account_slug = parse.quote(str(account_id), safe="")
    url = f"{base}/api/v1/accounts/{account_slug}/courses"
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    data = {
        "course[name]": f"LMS-Migration Preview {ts}",
        "course[course_code]": f"PREVIEW-{ts}",
        "course[workflow_state]": "unpublished",
    }
    result = _api_post_form(url, token, data)
    course_id = str(result.get("id", ""))
    if not course_id or course_id == "None":
        raise CanvasPreviewError(
            f"Unexpected course creation response: {list(result.keys())}"
        )
    return course_id


def delete_sandbox_course(
    *,
    base_url: str,
    course_id: str,
    token: str,
) -> None:
    """Permanently delete a sandbox course.

    Args:
        base_url: Canvas base URL.
        course_id: Course ID to delete.
        token: API token.
    """
    base = normalize_base_url(base_url)
    url = f"{base}/api/v1/courses/{parse.quote(str(course_id), safe='')}"
    _api_delete(url, token, data={"event": "delete"})


# ─── Content migration ────────────────────────────────────────────────────────


def initiate_migration(
    *,
    base_url: str,
    course_id: str,
    zip_path: Path,
    token: str,
) -> tuple[str, str, dict[str, str]]:
    """Start a content migration and obtain a pre-signed file upload slot.

    Calls ``POST /api/v1/courses/{id}/content_migrations`` with
    ``pre_attachment`` fields to request a file upload URL from Canvas.

    Args:
        base_url: Canvas base URL.
        course_id: Target course ID.
        zip_path: Local path to the ``.imscc`` / ``.zip`` package.
        token: API token.

    Returns:
        ``(migration_id, upload_url, upload_params)`` where *upload_params*
        are the form fields to include in the multipart POST to *upload_url*.

    Raises:
        :exc:`CanvasPreviewError` if Canvas does not return a pre-attachment slot.
    """
    base = normalize_base_url(base_url)
    url = (
        f"{base}/api/v1/courses/{parse.quote(str(course_id), safe='')}"
        "/content_migrations"
    )
    size = zip_path.stat().st_size
    data = {
        "migration_type": "canvas_cartridge_importer",
        "pre_attachment[name]": zip_path.name,
        "pre_attachment[size]": str(size),
    }
    result = _api_post_form(url, token, data)
    migration_id = str(result.get("id", ""))
    if not migration_id or migration_id == "None":
        raise CanvasPreviewError(
            f"Unexpected migration creation response: {list(result.keys())}"
        )
    pre = result.get("pre_attachment", {})
    upload_url = pre.get("upload_url", "")
    upload_params: dict[str, str] = {
        k: str(v) for k, v in pre.get("upload_params", {}).items()
    }
    if not upload_url:
        raise CanvasPreviewError(
            "Canvas did not return a pre_attachment upload_url. "
            "Verify the account has content migration permissions."
        )
    return migration_id, upload_url, upload_params


def upload_migration_file(
    *,
    upload_url: str,
    upload_params: dict[str, str],
    zip_path: Path,
) -> None:
    """POST the migration package to the pre-signed upload URL.

    No ``Authorization`` header is sent — credentials are embedded in
    *upload_params* (typically S3 form-policy fields).

    Args:
        upload_url: URL returned by :func:`initiate_migration`.
        upload_params: Form fields from :func:`initiate_migration`.
        zip_path: Local path to the package file.
    """
    _post_multipart_no_auth(
        url=upload_url,
        fields=upload_params,
        file_field="file",
        file_path=zip_path,
    )


def poll_migration(
    *,
    base_url: str,
    course_id: str,
    migration_id: str,
    token: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    """Poll until the migration reaches a terminal workflow state.

    Args:
        base_url: Canvas base URL.
        course_id: Canvas course ID.
        migration_id: Migration ID from :func:`initiate_migration`.
        token: API token.
        timeout_seconds: Maximum seconds to wait.
        poll_interval: Seconds between each status check.
        progress_callback: Optional callable invoked with each status message.

    Returns:
        The final ``workflow_state`` string (e.g. ``"completed"``).

    Raises:
        :exc:`CanvasPreviewError` if the migration fails or the timeout elapses.
    """

    def _p(msg: str) -> None:
        print(msg, flush=True)
        if progress_callback:
            progress_callback(msg)

    base = normalize_base_url(base_url)
    url = (
        f"{base}/api/v1/courses/{parse.quote(str(course_id), safe='')}/"
        f"content_migrations/{parse.quote(str(migration_id), safe='')}"
    )
    terminal_states = {"completed", "failed", "waiting_for_select"}
    deadline = time.monotonic() + timeout_seconds
    elapsed = 0
    while True:
        result = _api_get(url, token)
        state = str(result.get("workflow_state", "")).lower()
        if state in terminal_states:
            if state == "failed":
                raise CanvasPreviewError(
                    f"Migration {migration_id} failed. "
                    "Check Canvas migration issues for details."
                )
            return state
        if time.monotonic() > deadline:
            raise CanvasPreviewError(
                f"Migration did not complete within {timeout_seconds}s "
                f"(last state: {state!r})."
            )
        _p(f"  … Migration status: {state} (waited {elapsed}s)")
        time.sleep(poll_interval)
        elapsed += poll_interval


def fetch_preview_page_urls(
    *,
    base_url: str,
    course_id: str,
    token: str,
) -> list[str]:
    """Return full viewer URLs for every page in the sandbox course.

    Args:
        base_url: Canvas base URL.
        course_id: Canvas course ID.
        token: API token.

    Returns:
        List of ``https://.../courses/{id}/pages/{slug}`` URLs.
    """
    base = normalize_base_url(base_url)
    url = (
        f"{base}/api/v1/courses/{parse.quote(str(course_id), safe='')}"
        "/pages?per_page=100"
    )
    pages_json = _api_get(url, token)
    if not isinstance(pages_json, list):
        raise CanvasPreviewError(
            f"Unexpected pages API response type: {type(pages_json).__name__}"
        )
    urls: list[str] = []
    for page in pages_json:
        slug = page.get("url", "")
        if slug:
            urls.append(f"{base}/courses/{course_id}/pages/{slug}")
    return urls


# ─── High-level orchestrator ──────────────────────────────────────────────────


@dataclass
class PreviewResult:
    """Collected results from a single preview run."""

    course_id: str
    base_url: str
    page_urls: list[str] = field(default_factory=list)
    migration_issues: list[dict[str, Any]] = field(default_factory=list)
    kept_sandbox: bool = False


def run_preview(
    zip_path: Path,
    *,
    base_url: str,
    token: str,
    course_id: str | None = None,
    account_id: str | None = None,
    keep_sandbox: bool = False,
    timeout_seconds: int = 300,
    template_zip_path: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> PreviewResult:
    """Upload *zip_path* to a Canvas sandbox course, run the migration, and
    return page preview URLs.

    **Primary mode** (``course_id`` set): imports into an existing course that
    you own.  The course is *never* deleted — it is your persistent sandbox.
    Update ``CANVAS_SANDBOX_COURSE_ID`` in ``.env`` whenever you reset it.

    **Secondary mode** (``account_id`` set, requires account-admin rights):
    creates a temporary unpublished course, imports, then deletes it (unless
    ``keep_sandbox=True``).  The temp course is also deleted on failure.

    Exactly one of *course_id* and *account_id* must be provided.

    Args:
        zip_path: Path to the ``.imscc`` or converted ``.zip`` package.
        base_url: Canvas base URL (domain only, HTTPS required).
        token: Canvas API token.  Never logged or echoed.
        course_id: Existing sandbox course ID (primary mode).
        account_id: Canvas account ID for temp-course creation (secondary mode).
        keep_sandbox: Secondary mode only — retain the temp course after preview.
        timeout_seconds: Maximum seconds to wait for migration to complete.
        template_zip_path: If provided, this ``.imscc`` (the Canvas template
            package) is imported into the course FIRST, before the D2L package.
            This preserves all template modules — including faculty guidance
            modules — which are skipped when using inject_template_pages alone.

    Returns:
        :class:`PreviewResult` containing page URLs and any migration issues.

    Raises:
        :exc:`CanvasPreviewError` on any failure, with token redacted.
    """
    if not zip_path.exists():
        raise CanvasPreviewError(f"Package file not found: {zip_path}")
    if not course_id and not account_id:
        raise CanvasPreviewError(
            "Provide either course_id (existing sandbox) or account_id "
            "(create temp course)."
        )

    def _p(msg: str) -> None:
        print(msg, flush=True)
        if progress_callback:
            progress_callback(msg)

    base = normalize_base_url(base_url)

    # created_course_id is set only when WE create a temp course.  We never
    # delete a course we did not create.
    created_course_id: str | None = None
    effective_course_id: str

    try:
        if course_id:
            effective_course_id = course_id
            # Verify the course is reachable before spending time on upload.
            course_url = (
                f"{base}/api/v1/courses/"
                f"{parse.quote(str(effective_course_id), safe='')}"
            )
            _p(f"Verifying sandbox course {effective_course_id} ({course_url}) …")
            try:
                info = _api_get(course_url, token)
            except CanvasPreviewError as exc:
                raise CanvasPreviewError(
                    f"Cannot access course {effective_course_id}. "
                    "Check that CANVAS_SANDBOX_COURSE_ID is correct and the "
                    f"token has at least Teacher/Designer access. Detail: {exc}"
                ) from exc
            _p(
                f"  Course found: {info.get('name', '(unnamed)')} "
                f"[{info.get('workflow_state', '?')}]"
            )
        else:
            assert account_id  # guaranteed by guard above
            _p("Creating temporary sandbox course …")
            effective_course_id = create_sandbox_course(
                base_url=base, account_id=account_id, token=token
            )
            created_course_id = effective_course_id
            _p(f"  Course ID: {effective_course_id}")

        _p("Initiating content migration …")
        # ── Step 1 (optional): import template package first ─────────────────
        # When a template_zip_path is provided the full Canvas template course
        # (including faculty guidance modules) is imported before the converted
        # D2L package.  This is the canonical workflow: template first, then
        # the D2L content lands on top.
        if template_zip_path is not None:
            if not template_zip_path.exists():
                raise CanvasPreviewError(
                    f"Template package not found: {template_zip_path}"
                )
            tmpl_size_kb = template_zip_path.stat().st_size // 1024
            _p(
                f"[1/2] Importing template package ({tmpl_size_kb:,} KB): "
                f"{template_zip_path.name} …"
            )
            tmpl_mid, tmpl_upload_url, tmpl_upload_params = initiate_migration(
                base_url=base,
                course_id=effective_course_id,
                zip_path=template_zip_path,
                token=token,
            )
            _p(f"  Template migration ID: {tmpl_mid}")
            upload_migration_file(
                upload_url=tmpl_upload_url,
                upload_params=tmpl_upload_params,
                zip_path=template_zip_path,
            )
            _p("  Template upload complete. Waiting for migration …")
            tmpl_state = poll_migration(
                base_url=base,
                course_id=effective_course_id,
                migration_id=tmpl_mid,
                token=token,
                timeout_seconds=timeout_seconds,
                progress_callback=progress_callback,
            )
            _p(f"  Template migration state: {tmpl_state}")
            _p("[2/2] Importing converted D2L package …")
        # ── Step 2: import the converted D2L package ─────────────────────────
        migration_id, upload_url, upload_params = initiate_migration(
            base_url=base,
            course_id=effective_course_id,
            zip_path=zip_path,
            token=token,
        )
        _p(f"  Migration ID: {migration_id}")

        size_kb = zip_path.stat().st_size // 1024
        _p(f"Uploading package ({size_kb:,} KB) …")
        upload_migration_file(
            upload_url=upload_url,
            upload_params=upload_params,
            zip_path=zip_path,
        )
        _p("  Upload complete.")

        _p(f"Waiting for migration to complete (timeout {timeout_seconds}s) …")
        final_state = poll_migration(
            base_url=base,
            course_id=effective_course_id,
            migration_id=migration_id,
            token=token,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
        )
        _p(f"  Migration state: {final_state}")

        _p("Fetching page URLs …")
        page_urls = fetch_preview_page_urls(
            base_url=base, course_id=effective_course_id, token=token
        )
        _p(f"  {len(page_urls)} page(s) found.")

        # Collect migration issues for the report.
        issues_url = (
            f"{base}/api/v1/courses/"
            f"{parse.quote(str(effective_course_id), safe='')}/"
            f"content_migrations/"
            f"{parse.quote(str(migration_id), safe='')}"
            "/migration_issues?per_page=100"
        )
        raw_issues = _api_get(issues_url, token)
        migration_issues: list[dict[str, Any]] = (
            raw_issues if isinstance(raw_issues, list) else []
        )

        # In primary mode the course is always kept; in secondary mode honour
        # the keep_sandbox flag.
        kept = True if course_id else keep_sandbox

        result = PreviewResult(
            course_id=effective_course_id,
            base_url=base,
            page_urls=page_urls,
            migration_issues=migration_issues,
            kept_sandbox=kept,
        )

        if created_course_id and not keep_sandbox:
            _p("Deleting temporary sandbox course …")
            delete_sandbox_course(
                base_url=base, course_id=created_course_id, token=token
            )
            _p("  Deleted.")
        elif created_course_id and keep_sandbox:
            _p(
                f"  Temporary course {created_course_id} retained (--keep-sandbox). "
                "Delete it manually in Canvas when you are done."
            )

        return result

    except (CanvasPreviewError, CanvasAPIError) as exc:
        # Best-effort cleanup: only delete a course WE created.
        if created_course_id and not keep_sandbox:
            try:
                delete_sandbox_course(
                    base_url=base, course_id=created_course_id, token=token
                )
            except Exception:
                print(
                    f"Warning: could not delete temporary course {created_course_id} "
                    "after error. Delete it manually in Canvas.",
                    file=sys.stderr,
                )
        raise CanvasPreviewError(_redact(str(exc), token)) from exc


# ─── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lms-canvas-preview",
        description=(
            "Upload a converted .imscc package to a Canvas sandbox course and\n"
            "return preview page URLs for visual review.\n\n"
            "Credentials are read from environment variables (never CLI args):\n"
            "  CANVAS_SANDBOX_URL          Canvas base URL (domain only)\n"
            "  CANVAS_SANDBOX_TOKEN        Canvas API token\n"
            "  CANVAS_SANDBOX_COURSE_ID    Existing sandbox course ID  [primary]\n"
            "  CANVAS_SANDBOX_ACCOUNT_ID   Account ID to create a temp course  [secondary]\n\n"
            "Primary mode: set CANVAS_SANDBOX_COURSE_ID to import into an existing\n"
            "course. Update this value in .env whenever your sandbox is reset.\n\n"
            "Secondary mode: set CANVAS_SANDBOX_ACCOUNT_ID (requires account-admin\n"
            "rights) to create and delete a temporary course automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Path to the .imscc or .zip Canvas package to preview.",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Optional .env file to load credentials from (KEY=VALUE format). "
            "Existing shell environment variables always take precedence."
        ),
    )
    parser.add_argument(
        "--course-id",
        type=str,
        default=None,
        metavar="ID",
        help=(
            "Existing sandbox course ID. Overrides CANVAS_SANDBOX_COURSE_ID env var. "
            "Find it in the Canvas URL: /courses/12345 → 12345."
        ),
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default=None,
        metavar="ID",
        help=(
            "Canvas account ID to create a temporary course under (secondary mode). "
            "Overrides CANVAS_SANDBOX_ACCOUNT_ID env var."
        ),
    )
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        default=False,
        help="In create-mode: do not delete the temporary course after preview.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Maximum seconds to wait for migration to complete (default: 300).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        metavar="FILE",
        help="Optional path to write the preview result as JSON.",
    )
    parser.add_argument(
        "--template-zip",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Path to the Canvas template .imscc package. When provided, the template "
            "is imported into the course FIRST (preserving all template modules including "
            "faculty guidance modules), then the converted D2L package is imported on top. "
            "This is the canonical workflow — template first, D2L content second."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Load .env *before* reading env vars so that shell env still takes priority.
    if args.env:
        env_path = Path(args.env)
        if not env_path.exists():
            print(f"Error: --env file not found: {env_path}", file=sys.stderr)
            sys.exit(1)
        _load_dotenv(env_path)

    try:
        base_url = _require_env("CANVAS_SANDBOX_URL")
        token = _require_env("CANVAS_SANDBOX_TOKEN")
    except CanvasPreviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Resolve course ID: CLI flag > env var > account-id fallback.
    course_id = (
        args.course_id or os.environ.get("CANVAS_SANDBOX_COURSE_ID", "").strip() or None
    )
    account_id = (
        args.account_id
        or os.environ.get("CANVAS_SANDBOX_ACCOUNT_ID", "").strip()
        or None
    )

    if not course_id and not account_id:
        print(
            "Error: set CANVAS_SANDBOX_COURSE_ID in .env (or --course-id) to use "
            "an existing sandbox course.\n"
            "Alternatively set CANVAS_SANDBOX_ACCOUNT_ID (or --account-id) to "
            "create a temporary course (requires account-admin rights).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = run_preview(
            args.zip_path,
            base_url=base_url,
            token=token,
            course_id=course_id,
            account_id=account_id,
            keep_sandbox=args.keep_sandbox,
            timeout_seconds=args.timeout,
            template_zip_path=args.template_zip,
        )
    except CanvasPreviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nPreview page URLs:")
    for url in result.page_urls:
        print(f"  {url}")

    if result.migration_issues:
        print(f"\nMigration issues ({len(result.migration_issues)}):")
        for issue in result.migration_issues:
            print(f"  [{issue.get('issue_type', '?')}] {issue.get('description', '')}")

    if args.output_json:
        payload = {
            "course_id": result.course_id,
            "base_url": result.base_url,
            "page_urls": result.page_urls,
            "migration_issues": result.migration_issues,
            "kept_sandbox": result.kept_sandbox,
        }
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nPreview result JSON: {args.output_json}")


if __name__ == "__main__":
    main()

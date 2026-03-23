"""Tests for canvas_preview.py — Canvas sandbox preview orchestrator (no network calls)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from lms_migration.canvas_preview import (
    CanvasPreviewError,
    PreviewResult,
    _encode_multipart,
    _load_dotenv,
    _redact,
    _require_env,
    create_sandbox_course,
    delete_sandbox_course,
    fetch_preview_page_urls,
    initiate_migration,
    poll_migration,
    run_preview,
    upload_migration_file,
)


# ─── _load_dotenv ─────────────────────────────────────────────────────────────


class TestLoadDotenv:
    def test_loads_key_value_pairs(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_TEST_KEY_A=hello\nMY_TEST_KEY_B=world\n")
        try:
            _load_dotenv(env_file)
            assert os.environ.get("MY_TEST_KEY_A") == "hello"
            assert os.environ.get("MY_TEST_KEY_B") == "world"
        finally:
            os.environ.pop("MY_TEST_KEY_A", None)
            os.environ.pop("MY_TEST_KEY_B", None)

    def test_skips_comments_and_blank_lines(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nMY_TEST_KEY_C=value\n")
        try:
            _load_dotenv(env_file)
            assert os.environ.get("MY_TEST_KEY_C") == "value"
        finally:
            os.environ.pop("MY_TEST_KEY_C", None)

    def test_strips_double_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('MY_TEST_KEY_D="quoted value"\n')
        try:
            _load_dotenv(env_file)
            assert os.environ.get("MY_TEST_KEY_D") == "quoted value"
        finally:
            os.environ.pop("MY_TEST_KEY_D", None)

    def test_strips_single_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_TEST_KEY_E='single quoted'\n")
        try:
            _load_dotenv(env_file)
            assert os.environ.get("MY_TEST_KEY_E") == "single quoted"
        finally:
            os.environ.pop("MY_TEST_KEY_E", None)

    def test_does_not_overwrite_existing_env(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_TEST_KEY_F=file_value\n")
        os.environ["MY_TEST_KEY_F"] = "shell_value"
        try:
            _load_dotenv(env_file)
            assert os.environ.get("MY_TEST_KEY_F") == "shell_value"
        finally:
            os.environ.pop("MY_TEST_KEY_F", None)

    def test_skips_lines_without_equals(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("NOEQUALS\nMY_TEST_KEY_G=ok\n")
        try:
            _load_dotenv(env_file)
            assert os.environ.get("NOEQUALS") is None
            assert os.environ.get("MY_TEST_KEY_G") == "ok"
        finally:
            os.environ.pop("MY_TEST_KEY_G", None)


# ─── _require_env ─────────────────────────────────────────────────────────────


class TestRequireEnv:
    def test_returns_value_when_set(self):
        os.environ["MY_TEST_REQUIRE_KEY"] = "  sometoken  "
        try:
            result = _require_env("MY_TEST_REQUIRE_KEY")
            assert result == "sometoken"
        finally:
            os.environ.pop("MY_TEST_REQUIRE_KEY", None)

    def test_raises_when_absent(self):
        os.environ.pop("MY_TEST_REQUIRE_ABSENT", None)
        with pytest.raises(CanvasPreviewError, match="MY_TEST_REQUIRE_ABSENT"):
            _require_env("MY_TEST_REQUIRE_ABSENT")

    def test_raises_when_empty_string(self):
        os.environ["MY_TEST_REQUIRE_EMPTY"] = "   "
        try:
            with pytest.raises(CanvasPreviewError, match="MY_TEST_REQUIRE_EMPTY"):
                _require_env("MY_TEST_REQUIRE_EMPTY")
        finally:
            os.environ.pop("MY_TEST_REQUIRE_EMPTY", None)


# ─── _redact ──────────────────────────────────────────────────────────────────


class TestRedact:
    def test_replaces_token(self):
        result = _redact("Error: token abc123 is invalid", "abc123")
        assert result == "Error: token [REDACTED] is invalid"

    def test_replaces_multiple_occurrences(self):
        result = _redact("abc123 and abc123 again", "abc123")
        assert result == "[REDACTED] and [REDACTED] again"

    def test_empty_token_returns_unchanged(self):
        result = _redact("some message", "")
        assert result == "some message"

    def test_no_occurrence_returns_unchanged(self):
        result = _redact("no token here", "secrettoken")
        assert result == "no token here"


# ─── _encode_multipart ────────────────────────────────────────────────────────


class TestEncodeMultipart:
    def test_body_contains_fields(self, tmp_path: Path):
        f = tmp_path / "package.zip"
        f.write_bytes(b"fake zip content")
        body, content_type = _encode_multipart(
            {"key": "value", "other": "data"}, "file", f
        )
        assert b"fake zip content" in body
        assert b"value" in body
        assert b"data" in body
        assert b"package.zip" in body

    def test_content_type_has_boundary(self, tmp_path: Path):
        f = tmp_path / "test.zip"
        f.write_bytes(b"data")
        _, content_type = _encode_multipart({}, "file", f)
        assert "multipart/form-data" in content_type
        assert "boundary=" in content_type

    def test_empty_fields(self, tmp_path: Path):
        f = tmp_path / "empty.zip"
        f.write_bytes(b"\x00\x01\x02")
        body, _ = _encode_multipart({}, "file", f)
        assert b"\x00\x01\x02" in body


# ─── create_sandbox_course ────────────────────────────────────────────────────


class TestCreateSandboxCourse:
    def test_success(self):
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {"id": 12345, "name": "LMS-Migration Preview 20260101-000000"}
            result = create_sandbox_course(
                base_url="https://canvas.example.com",
                account_id="1",
                token="tok",
            )
        assert result == "12345"

    def test_missing_id_raises(self):
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {"name": "no id here"}
            with pytest.raises(CanvasPreviewError, match="[Uu]nexpected"):
                create_sandbox_course(
                    base_url="https://canvas.example.com",
                    account_id="1",
                    token="tok",
                )

    def test_correct_url_built(self):
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {"id": 99}
            create_sandbox_course(
                base_url="https://canvas.example.com",
                account_id="self",
                token="tok",
            )
        call_url = mock_post.call_args[0][0]
        assert "/api/v1/accounts/" in call_url
        assert "self" in call_url
        assert "/courses" in call_url


# ─── delete_sandbox_course ────────────────────────────────────────────────────


class TestDeleteSandboxCourse:
    def test_calls_api_delete_with_event(self):
        with patch("lms_migration.canvas_preview._api_delete") as mock_del:
            delete_sandbox_course(
                base_url="https://canvas.example.com",
                course_id="42",
                token="tok",
            )
        mock_del.assert_called_once()
        call_url = mock_del.call_args[0][0]
        assert "/api/v1/courses/42" in call_url
        call_data = mock_del.call_args[1].get("data") or mock_del.call_args[0][2]
        assert call_data.get("event") == "delete"


# ─── initiate_migration ───────────────────────────────────────────────────────


class TestInitiateMigration:
    def test_success(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip")
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {
                "id": "77",
                "pre_attachment": {
                    "upload_url": "https://s3.amazonaws.com/bucket",
                    "upload_params": {"key": "abc", "Content-Type": "application/zip"},
                },
            }
            mid, upload_url, params = initiate_migration(
                base_url="https://canvas.example.com",
                course_id="42",
                zip_path=zip_file,
                token="tok",
            )
        assert mid == "77"
        assert upload_url == "https://s3.amazonaws.com/bucket"
        assert params == {"key": "abc", "Content-Type": "application/zip"}

    def test_missing_migration_id_raises(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip")
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {"workflow_state": "created"}
            with pytest.raises(CanvasPreviewError, match="[Uu]nexpected"):
                initiate_migration(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    zip_path=zip_file,
                    token="tok",
                )

    def test_missing_upload_url_raises(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip")
        with patch("lms_migration.canvas_preview._api_post_form") as mock_post:
            mock_post.return_value = {
                "id": "77",
                "pre_attachment": {"upload_params": {}},  # no upload_url
            }
            with pytest.raises(CanvasPreviewError, match="upload_url"):
                initiate_migration(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    zip_path=zip_file,
                    token="tok",
                )


# ─── upload_migration_file ────────────────────────────────────────────────────


class TestUploadMigrationFile:
    def test_calls_post_multipart(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip")
        with patch("lms_migration.canvas_preview._post_multipart_no_auth") as mock_upload:
            mock_upload.return_value = {}
            upload_migration_file(
                upload_url="https://s3.amazonaws.com/bucket",
                upload_params={"key": "abc"},
                zip_path=zip_file,
            )
        mock_upload.assert_called_once()
        kwargs = mock_upload.call_args[1]
        assert kwargs["url"] == "https://s3.amazonaws.com/bucket"
        assert kwargs["fields"] == {"key": "abc"}
        assert kwargs["file_field"] == "file"
        assert kwargs["file_path"] == zip_file


# ─── poll_migration ───────────────────────────────────────────────────────────


class TestPollMigration:
    def test_returns_completed(self):
        with (
            patch("lms_migration.canvas_preview._api_get") as mock_get,
            patch("lms_migration.canvas_preview.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep.return_value = None
            mock_get.return_value = {"workflow_state": "completed"}
            result = poll_migration(
                base_url="https://canvas.example.com",
                course_id="42",
                migration_id="77",
                token="tok",
                timeout_seconds=300,
            )
        assert result == "completed"

    def test_raises_on_failed(self):
        with (
            patch("lms_migration.canvas_preview._api_get") as mock_get,
            patch("lms_migration.canvas_preview.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep.return_value = None
            mock_get.return_value = {"workflow_state": "failed"}
            with pytest.raises(CanvasPreviewError, match="[Ff]ailed"):
                poll_migration(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    migration_id="77",
                    token="tok",
                    timeout_seconds=300,
                )

    def test_raises_on_timeout(self):
        with (
            patch("lms_migration.canvas_preview._api_get") as mock_get,
            patch("lms_migration.canvas_preview.time") as mock_time,
        ):
            # First monotonic call sets deadline (1000 + 30 = 1030),
            # second check exceeds deadline (2000 > 1030).
            mock_time.monotonic.side_effect = [1000.0, 2000.0]
            mock_time.sleep.return_value = None
            mock_get.return_value = {"workflow_state": "running"}
            with pytest.raises(CanvasPreviewError, match="[Tt]imeout|not complete"):
                poll_migration(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    migration_id="77",
                    token="tok",
                    timeout_seconds=30,
                )

    def test_calls_progress_callback(self):
        messages: list[str] = []
        with (
            patch("lms_migration.canvas_preview._api_get") as mock_get,
            patch("lms_migration.canvas_preview.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep.return_value = None
            # First call: running; second call: completed
            mock_get.side_effect = [
                {"workflow_state": "running"},
                {"workflow_state": "completed"},
            ]
            mock_time.monotonic.side_effect = [
                1000.0,   # initial deadline
                999.0,    # first timeout check: 999 < 1300 → continue
                1000.0,   # still before deadline
            ]
            poll_migration(
                base_url="https://canvas.example.com",
                course_id="42",
                migration_id="77",
                token="tok",
                timeout_seconds=300,
                progress_callback=messages.append,
            )
        assert any("running" in m for m in messages)

    def test_waiting_for_select_state_returns(self):
        with (
            patch("lms_migration.canvas_preview._api_get") as mock_get,
            patch("lms_migration.canvas_preview.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep.return_value = None
            mock_get.return_value = {"workflow_state": "waiting_for_select"}
            result = poll_migration(
                base_url="https://canvas.example.com",
                course_id="42",
                migration_id="77",
                token="tok",
                timeout_seconds=300,
            )
        assert result == "waiting_for_select"


# ─── fetch_preview_page_urls ──────────────────────────────────────────────────


class TestFetchPreviewPageUrls:
    def test_returns_page_viewer_urls(self):
        pages = [
            {"url": "week-1-intro", "title": "Week 1"},
            {"url": "syllabus", "title": "Syllabus"},
        ]
        with patch("lms_migration.canvas_preview._api_get") as mock_get:
            mock_get.return_value = pages
            result = fetch_preview_page_urls(
                base_url="https://canvas.example.com",
                course_id="42",
                token="tok",
            )
        assert len(result) == 2
        assert "https://canvas.example.com/courses/42/pages/week-1-intro" in result
        assert "https://canvas.example.com/courses/42/pages/syllabus" in result

    def test_skips_pages_without_slug(self):
        pages = [{"url": "valid", "title": "A"}, {"title": "No slug"}]
        with patch("lms_migration.canvas_preview._api_get") as mock_get:
            mock_get.return_value = pages
            result = fetch_preview_page_urls(
                base_url="https://canvas.example.com",
                course_id="42",
                token="tok",
            )
        assert len(result) == 1

    def test_non_list_response_raises(self):
        with patch("lms_migration.canvas_preview._api_get") as mock_get:
            mock_get.return_value = {"error": "something"}
            with pytest.raises(CanvasPreviewError, match="[Uu]nexpected"):
                fetch_preview_page_urls(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    token="tok",
                )


# ─── run_preview ─────────────────────────────────────────────────────────────


_PATCH_API_GET = "lms_migration.canvas_preview._api_get"
_PATCH_CREATE = "lms_migration.canvas_preview.create_sandbox_course"
_PATCH_DELETE = "lms_migration.canvas_preview.delete_sandbox_course"
_PATCH_INITIATE = "lms_migration.canvas_preview.initiate_migration"
_PATCH_UPLOAD = "lms_migration.canvas_preview.upload_migration_file"
_PATCH_POLL = "lms_migration.canvas_preview.poll_migration"
_PATCH_FETCH = "lms_migration.canvas_preview.fetch_preview_page_urls"


class TestRunPreview:
    def test_raises_when_zip_not_found(self, tmp_path: Path):
        with pytest.raises(CanvasPreviewError, match="not found"):
            run_preview(
                tmp_path / "missing.zip",
                base_url="https://canvas.example.com",
                token="tok",
                course_id="42",
            )

    def test_raises_when_neither_course_nor_account(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"data")
        with pytest.raises(CanvasPreviewError, match="course_id|account_id"):
            run_preview(
                zip_file,
                base_url="https://canvas.example.com",
                token="tok",
            )

    def test_primary_mode_success(self, tmp_path: Path):
        """Primary mode: existing course_id — verifies, migrates, returns result."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip data")

        with (
            patch(_PATCH_API_GET) as mock_get,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH) as mock_fetch,
        ):
            # _api_get is called twice: course verification + migration issues
            mock_get.side_effect = [
                {"id": "42", "name": "Test Course", "workflow_state": "available"},
                [],  # migration issues (empty)
            ]
            mock_init.return_value = ("77", "https://s3.bucket.example.com", {})
            mock_poll.return_value = "completed"
            mock_fetch.return_value = [
                "https://canvas.example.com/courses/42/pages/week-1"
            ]

            result = run_preview(
                zip_file,
                base_url="https://canvas.example.com",
                token="tok",
                course_id="42",
            )

        assert isinstance(result, PreviewResult)
        assert result.course_id == "42"
        assert result.page_urls == ["https://canvas.example.com/courses/42/pages/week-1"]
        assert result.migration_issues == []
        assert result.kept_sandbox is True  # primary mode always keeps

    def test_secondary_mode_creates_and_deletes_course(self, tmp_path: Path):
        """Secondary mode: account_id — creates temp course, migrates, deletes it."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip data")

        with (
            patch(_PATCH_API_GET) as mock_get,
            patch(_PATCH_CREATE) as mock_create,
            patch(_PATCH_DELETE) as mock_delete,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH) as mock_fetch,
        ):
            mock_create.return_value = "999"
            mock_get.return_value = []  # migration issues
            mock_init.return_value = ("77", "https://s3.bucket.example.com", {})
            mock_poll.return_value = "completed"
            mock_fetch.return_value = []

            result = run_preview(
                zip_file,
                base_url="https://canvas.example.com",
                token="tok",
                account_id="1",
                keep_sandbox=False,
            )

        mock_create.assert_called_once()
        mock_delete.assert_called_once()
        assert result.course_id == "999"
        assert result.kept_sandbox is False

    def test_secondary_mode_keep_sandbox(self, tmp_path: Path):
        """Secondary mode with keep_sandbox=True: course is NOT deleted."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip data")

        with (
            patch(_PATCH_API_GET) as mock_get,
            patch(_PATCH_CREATE) as mock_create,
            patch(_PATCH_DELETE) as mock_delete,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH) as mock_fetch,
        ):
            mock_create.return_value = "888"
            mock_get.return_value = []
            mock_init.return_value = ("55", "https://upload.example.com", {})
            mock_poll.return_value = "completed"
            mock_fetch.return_value = []

            result = run_preview(
                zip_file,
                base_url="https://canvas.example.com",
                token="tok",
                account_id="1",
                keep_sandbox=True,
            )

        mock_delete.assert_not_called()
        assert result.kept_sandbox is True

    def test_secondary_mode_cleans_up_on_failure(self, tmp_path: Path):
        """On migration failure in secondary mode the temp course is deleted."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"fake zip data")

        with (
            patch(_PATCH_API_GET),
            patch(_PATCH_CREATE) as mock_create,
            patch(_PATCH_DELETE) as mock_delete,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH),
        ):
            mock_create.return_value = "777"
            mock_init.return_value = ("55", "https://upload.example.com", {})
            mock_poll.side_effect = CanvasPreviewError("Migration 55 failed.")

            with pytest.raises(CanvasPreviewError, match="[Ff]ail"):
                run_preview(
                    zip_file,
                    base_url="https://canvas.example.com",
                    token="tok",
                    account_id="1",
                    keep_sandbox=False,
                )

        mock_delete.assert_called_once()

    def test_with_template_zip_imports_template_first(self, tmp_path: Path):
        """When template_zip_path is provided it is imported before the D2L package."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"d2l content")
        tmpl_file = tmp_path / "template.imscc"
        tmpl_file.write_bytes(b"template content")

        with (
            patch(_PATCH_API_GET) as mock_get,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH) as mock_fetch,
        ):
            mock_get.side_effect = [
                {"id": "42", "name": "Course", "workflow_state": "available"},
                [],  # migration issues
            ]
            # Two migrations: template then D2L
            mock_init.side_effect = [
                ("tmpl_mid", "https://s3.bucket.example.com/tmpl", {}),
                ("d2l_mid", "https://s3.bucket.example.com/d2l", {}),
            ]
            mock_poll.return_value = "completed"
            mock_fetch.return_value = []

            run_preview(
                zip_file,
                base_url="https://canvas.example.com",
                token="tok",
                course_id="42",
                template_zip_path=tmpl_file,
            )

        # initiate_migration called twice, poll_migration called twice
        assert mock_init.call_count == 2
        assert mock_poll.call_count == 2
        # First initiate called with template file
        first_call = mock_init.call_args_list[0]
        assert first_call[1]["zip_path"] == tmpl_file

    def test_template_zip_not_found_raises(self, tmp_path: Path):
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"data")

        with (
            patch(_PATCH_API_GET) as mock_get,
        ):
            mock_get.return_value = {"id": "42", "name": "Course", "workflow_state": "available"}
            with pytest.raises(CanvasPreviewError, match="[Tt]emplate"):
                run_preview(
                    zip_file,
                    base_url="https://canvas.example.com",
                    token="tok",
                    course_id="42",
                    template_zip_path=tmp_path / "missing_template.imscc",
                )

    def test_token_redacted_in_error_message(self, tmp_path: Path):
        """CanvasPreviewError messages must not expose the token."""
        zip_file = tmp_path / "course.zip"
        zip_file.write_bytes(b"data")
        secret = "super_secret_canvas_token_xyz"

        with (
            patch(_PATCH_API_GET) as mock_get,
            patch(_PATCH_INITIATE) as mock_init,
            patch(_PATCH_UPLOAD),
            patch(_PATCH_POLL) as mock_poll,
            patch(_PATCH_FETCH),
        ):
            mock_get.side_effect = [
                {"id": "42", "name": "Course", "workflow_state": "available"},
            ]
            mock_init.return_value = ("55", "https://upload.example.com", {})
            mock_poll.side_effect = CanvasPreviewError(
                f"Migration failed: token={secret}"
            )

            with pytest.raises(CanvasPreviewError) as exc_info:
                run_preview(
                    zip_file,
                    base_url="https://canvas.example.com",
                    token=secret,
                    course_id="42",
                )

        assert secret not in str(exc_info.value)

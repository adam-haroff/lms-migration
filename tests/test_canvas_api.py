"""Tests for canvas_api.py — Canvas REST API client (no network calls)."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from urllib.error import HTTPError, URLError

from lms_migration.canvas_api import (
    CanvasAPIError,
    _build_url,
    _fetch_paginated_list,
    _parse_next_link,
    _request_json,
    create_course_module,
    create_course_module_item,
    create_or_update_course_page,
    fetch_course,
    fetch_course_assignment,
    fetch_course_discussion_topic,
    fetch_course_pages,
    normalize_base_url,
    set_course_front_page,
    update_course_assignment,
    update_course_default_view,
    update_course_page,
    update_discussion_topic,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _fake_response(body: bytes, headers: dict | None = None) -> MagicMock:
    """Return a context-manager-compatible mock suitable for urlopen."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = headers if headers is not None else {}
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, body: bytes) -> HTTPError:
    from http.client import HTTPMessage

    return HTTPError(
        "https://canvas.example.com/api/v1/test",
        code,
        "Error",
        HTTPMessage(),
        io.BytesIO(body),
    )


# ─── normalize_base_url ───────────────────────────────────────────────────────


class TestNormalizeBaseUrl:
    def test_strips_path(self):
        assert (
            normalize_base_url("https://canvas.example.com/courses/123")
            == "https://canvas.example.com"
        )

    def test_strips_trailing_slash(self):
        assert (
            normalize_base_url("https://canvas.example.com/")
            == "https://canvas.example.com"
        )

    def test_adds_https_when_no_scheme(self):
        assert normalize_base_url("canvas.example.com") == "https://canvas.example.com"

    def test_rejects_http(self):
        with pytest.raises(CanvasAPIError, match="HTTPS"):
            normalize_base_url("http://canvas.example.com")

    def test_rejects_empty(self):
        with pytest.raises(CanvasAPIError, match="required"):
            normalize_base_url("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(CanvasAPIError, match="required"):
            normalize_base_url("   ")

    def test_valid_https_unchanged(self):
        assert (
            normalize_base_url("https://canvas.example.com")
            == "https://canvas.example.com"
        )

    def test_strips_port_path(self):
        result = normalize_base_url("https://canvas.example.com:443/api/v1/test")
        # Scheme + netloc retained, path stripped
        assert result.startswith("https://canvas.example.com")
        assert "/api" not in result


# ─── _parse_next_link ─────────────────────────────────────────────────────────


class TestParseNextLink:
    def test_none_header(self):
        assert _parse_next_link(None) is None

    def test_empty_string(self):
        assert _parse_next_link("") is None

    def test_single_next_link(self):
        header = '<https://canvas.example.com/api/v1/courses?page=2&per_page=100>; rel="next"'
        result = _parse_next_link(header)
        assert result == "https://canvas.example.com/api/v1/courses?page=2&per_page=100"

    def test_only_first_and_last(self):
        header = (
            '<https://canvas.example.com/api/v1/courses?page=1>; rel="first", '
            '<https://canvas.example.com/api/v1/courses?page=5>; rel="last"'
        )
        assert _parse_next_link(header) is None

    def test_multi_part_extracts_next(self):
        header = (
            '<https://canvas.example.com/api/v1/courses?page=1>; rel="current", '
            '<https://canvas.example.com/api/v1/courses?page=2>; rel="next", '
            '<https://canvas.example.com/api/v1/courses?page=5>; rel="last"'
        )
        result = _parse_next_link(header)
        assert result == "https://canvas.example.com/api/v1/courses?page=2"

    def test_no_angle_brackets_returns_none(self):
        # Malformed link — no angle brackets around URL
        assert _parse_next_link('https://canvas.example.com/page=2; rel="next"') is None


# ─── _build_url ───────────────────────────────────────────────────────────────


class TestBuildUrl:
    def test_no_params(self):
        result = _build_url("https://canvas.example.com", "/api/v1/courses")
        assert result == "https://canvas.example.com/api/v1/courses"

    def test_with_params(self):
        result = _build_url(
            "https://canvas.example.com", "/api/v1/courses", {"per_page": 100}
        )
        assert result == "https://canvas.example.com/api/v1/courses?per_page=100"

    def test_none_params(self):
        result = _build_url("https://canvas.example.com", "/api/v1/courses", None)
        assert result == "https://canvas.example.com/api/v1/courses"

    def test_empty_params(self):
        result = _build_url("https://canvas.example.com", "/api/v1/courses", {})
        assert result == "https://canvas.example.com/api/v1/courses"

    def test_list_param_doseq(self):
        result = _build_url(
            "https://canvas.example.com", "/path", {"include[]": ["items", "details"]}
        )
        assert "include" in result
        assert "items" in result
        assert "details" in result


# ─── _request_json ────────────────────────────────────────────────────────────


class TestRequestJson:
    def test_get_returns_list(self):
        body = json.dumps([{"id": 1}, {"id": 2}]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body)
            result, _ = _request_json(
                url="https://canvas.example.com/api/v1/test", token="tok"
            )
        assert result == [{"id": 1}, {"id": 2}]

    def test_get_returns_dict(self):
        body = json.dumps({"id": 42, "name": "Course"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body)
            result, _ = _request_json(
                url="https://canvas.example.com/api/v1/course/1", token="tok"
            )
        assert result == {"id": 42, "name": "Course"}

    def test_empty_body_returns_empty_list(self):
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(b"   ")
            result, _ = _request_json(
                url="https://canvas.example.com/api/v1/test", token="tok"
            )
        assert result == []

    def test_http_error_404_raises(self):
        exc = _http_error(404, json.dumps({"message": "Not Found"}).encode())
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = exc
            with pytest.raises(CanvasAPIError, match="404"):
                _request_json(url="https://canvas.example.com/api/v1/test", token="tok")

    def test_http_error_unauthenticated_body(self):
        body = json.dumps(
            {
                "status": "unauthenticated",
                "errors": [{"message": "Invalid access token."}],
            }
        ).encode()
        exc = _http_error(401, body)
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = exc
            with pytest.raises(CanvasAPIError, match="[Uu]nauthenticated"):
                _request_json(url="https://canvas.example.com/api/v1/test", token="tok")

    def test_http_error_500_includes_message(self):
        body = json.dumps({"message": "Internal error occurred"}).encode()
        exc = _http_error(500, body)
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = exc
            with pytest.raises(CanvasAPIError, match="500"):
                _request_json(url="https://canvas.example.com/api/v1/test", token="tok")

    def test_url_error_raises(self):
        exc = URLError("Connection refused")
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = exc
            with pytest.raises(CanvasAPIError, match="[Cc]onnect"):
                _request_json(url="https://canvas.example.com/api/v1/test", token="tok")

    def test_post_sends_encoded_form_data(self):
        body = json.dumps({"id": 99}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body)
            _request_json(
                url="https://canvas.example.com/api/v1/test",
                token="tok",
                method="POST",
                form_data={"wiki_page[title]": "Hello"},
            )
        req = mock_open.call_args[0][0]
        assert req.data is not None
        assert req.get_method() == "POST"

    def test_authorization_bearer_header_set(self):
        body = json.dumps([]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body)
            _request_json(
                url="https://canvas.example.com/api/v1/test", token="mytoken123"
            )
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer mytoken123"


# ─── _fetch_paginated_list ────────────────────────────────────────────────────


class TestFetchPaginatedList:
    def test_empty_token_raises(self):
        with pytest.raises(CanvasAPIError, match="[Tt]oken"):
            _fetch_paginated_list(
                first_url="https://canvas.example.com/api/v1/test", token="  "
            )

    def test_single_page_no_link_header(self):
        body = json.dumps([{"id": 1}, {"id": 2}]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = _fetch_paginated_list(
                first_url="https://canvas.example.com/api/v1/test", token="tok"
            )
        assert result == [{"id": 1}, {"id": 2}]

    def test_follows_pagination(self):
        page1 = json.dumps([{"id": 1}, {"id": 2}]).encode()
        page2 = json.dumps([{"id": 3}]).encode()
        headers1 = {
            "Link": '<https://canvas.example.com/api/v1/test?page=2>; rel="next"'
        }
        headers2: dict[str, str] = {}

        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = [
                _fake_response(page1, headers1),
                _fake_response(page2, headers2),
            ]
            result = _fetch_paginated_list(
                first_url="https://canvas.example.com/api/v1/test", token="tok"
            )
        assert result == [{"id": 1}, {"id": 2}, {"id": 3}]
        assert mock_open.call_count == 2

    def test_unauthenticated_dict_raises(self):
        body = json.dumps({"status": "unauthenticated"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            with pytest.raises(CanvasAPIError, match="[Uu]nauthenticated"):
                _fetch_paginated_list(
                    first_url="https://canvas.example.com/api/v1/test", token="tok"
                )

    def test_dict_with_errors_raises(self):
        body = json.dumps({"errors": [{"message": "Access denied"}]}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            with pytest.raises(CanvasAPIError, match="[Ee]rror"):
                _fetch_paginated_list(
                    first_url="https://canvas.example.com/api/v1/test", token="tok"
                )

    def test_unexpected_dict_type_raises(self):
        body = json.dumps({"some_other_key": "value"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            with pytest.raises(CanvasAPIError):
                _fetch_paginated_list(
                    first_url="https://canvas.example.com/api/v1/test", token="tok"
                )

    def test_skips_non_dict_items(self):
        # List contains a mix of dicts and other types — only dicts are kept
        body = json.dumps([{"id": 1}, "not_a_dict", {"id": 3}, 42]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = _fetch_paginated_list(
                first_url="https://canvas.example.com/api/v1/test", token="tok"
            )
        assert result == [{"id": 1}, {"id": 3}]


# ─── fetch_course ─────────────────────────────────────────────────────────────


class TestFetchCourse:
    def test_success(self):
        body = json.dumps({"id": 42, "name": "Test Course"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = fetch_course(
                base_url="https://canvas.example.com", course_id="42", token="tok"
            )
        assert result == {"id": 42, "name": "Test Course"}

    def test_non_dict_response_raises(self):
        body = json.dumps([{"id": 42}]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            with pytest.raises(CanvasAPIError, match="[Uu]nexpected"):
                fetch_course(
                    base_url="https://canvas.example.com", course_id="42", token="tok"
                )


class TestUpdateCourseDefaultView:
    def test_puts_wiki_default_view(self):
        body = json.dumps({"id": 42, "default_view": "wiki"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = update_course_default_view(
                base_url="https://canvas.example.com",
                course_id="42",
                token="tok",
            )
        assert result == {"id": 42, "default_view": "wiki"}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PUT"
        assert b"course%5Bdefault_view%5D=wiki" in req.data


# ─── fetch_course_pages ───────────────────────────────────────────────────────


class TestFetchCoursePages:
    def test_returns_list(self):
        body = json.dumps([{"url": "week-1", "title": "Week 1"}]).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = fetch_course_pages(
                base_url="https://canvas.example.com", course_id="42", token="tok"
            )
        assert result == [{"url": "week-1", "title": "Week 1"}]


class TestFetchSingleObjects:
    def test_fetch_course_assignment_returns_dict(self):
        body = json.dumps({"id": 10, "name": "Essay"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = fetch_course_assignment(
                base_url="https://canvas.example.com",
                course_id="42",
                assignment_id=10,
                token="tok",
            )
        assert result == {"id": 10, "name": "Essay"}

    def test_fetch_course_discussion_topic_returns_dict(self):
        body = json.dumps({"id": 9, "title": "Prompt"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = fetch_course_discussion_topic(
                base_url="https://canvas.example.com",
                course_id="42",
                topic_id=9,
                token="tok",
            )
        assert result == {"id": 9, "title": "Prompt"}


class TestCreateCourseModule:
    def test_posts_name_position_and_published(self):
        body = json.dumps({"id": 50, "name": "Module 1"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = create_course_module(
                base_url="https://canvas.example.com",
                course_id="42",
                token="tok",
                name="Module 1",
                position=1,
                published=False,
            )
        assert result == {"id": 50, "name": "Module 1"}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "POST"
        assert "courses/42/modules" in req.full_url
        assert b"module%5Bname%5D=Module+1" in req.data
        assert b"module%5Bposition%5D=1" in req.data
        assert b"module%5Bpublished%5D=false" in req.data


class TestCreateCourseModuleItem:
    def test_posts_page_item_fields(self):
        body = json.dumps({"id": 61, "title": "Module 1: Introduction and Checklist"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = create_course_module_item(
                base_url="https://canvas.example.com",
                course_id="42",
                module_id=50,
                token="tok",
                item_type="Page",
                title="Module 1: Introduction and Checklist",
                page_url="module-1-introduction-and-checklist",
                indent=1,
            )
        assert result["id"] == 61
        req = mock_open.call_args[0][0]
        assert req.get_method() == "POST"
        assert "courses/42/modules/50/items" in req.full_url
        assert b"module_item%5Btype%5D=Page" in req.data
        assert b"module_item%5Bpage_url%5D=module-1-introduction-and-checklist" in req.data
        assert b"module_item%5Bindent%5D=1" in req.data


# ─── create_or_update_course_page ────────────────────────────────────────────


class TestCreateOrUpdateCoursePage:
    def test_put_success(self):
        body = json.dumps({"id": 1, "title": "Hello"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = create_or_update_course_page(
                base_url="https://canvas.example.com",
                course_id="42",
                title="Hello",
                body_html="<p>World</p>",
                token="tok",
            )
        assert result == {"id": 1, "title": "Hello"}
        assert mock_open.call_count == 1

    def test_put_404_falls_back_to_post(self):
        put_exc = _http_error(404, json.dumps({"message": "not found"}).encode())
        post_body = json.dumps({"id": 99, "title": "New Page"}).encode()

        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = [put_exc, _fake_response(post_body, {})]
            result = create_or_update_course_page(
                base_url="https://canvas.example.com",
                course_id="42",
                title="New Page",
                body_html="<p>Hello</p>",
                token="tok",
            )
        assert result == {"id": 99, "title": "New Page"}
        assert mock_open.call_count == 2

    def test_put_non_404_error_reraises(self):
        exc = _http_error(500, json.dumps({"message": "server error"}).encode())
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.side_effect = exc
            with pytest.raises(CanvasAPIError, match="500"):
                create_or_update_course_page(
                    base_url="https://canvas.example.com",
                    course_id="42",
                    title="New Page",
                    body_html="<p>Hello</p>",
                    token="tok",
                )

    def test_slug_derived_from_title(self):
        """The PUT URL must be built from a lowercased, hyphenated title slug."""
        body = json.dumps({"id": 10, "title": "My Test Page"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            create_or_update_course_page(
                base_url="https://canvas.example.com",
                course_id="42",
                title="My Test Page",
                body_html="<p>body</p>",
                token="tok",
            )
        req = mock_open.call_args[0][0]
        assert "my-test-page" in req.full_url

    def test_published_flag_true(self):
        body = json.dumps({"id": 1, "published": True}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            create_or_update_course_page(
                base_url="https://canvas.example.com",
                course_id="42",
                title="Published",
                body_html="<p>x</p>",
                token="tok",
                published=True,
            )
        req = mock_open.call_args[0][0]
        assert b"wiki_page%5Bpublished%5D=true" in req.data


class TestSetCourseFrontPage:
    def test_sets_front_page_and_publishes(self):
        body = json.dumps({"url": "home-page", "front_page": True}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = set_course_front_page(
                base_url="https://canvas.example.com",
                course_id="42",
                page_url="home-page-lcs",
                token="tok",
                publish=True,
            )
        assert result == {"url": "home-page", "front_page": True}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PUT"
        assert "home-page-lcs" in req.full_url
        assert b"wiki_page%5Bfront_page%5D=true" in req.data
        assert b"wiki_page%5Bpublished%5D=true" in req.data


class TestUpdateCourseAssignment:
    def test_updates_multiple_assignment_fields(self):
        body = json.dumps({"id": 5, "name": "Essay"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = update_course_assignment(
                base_url="https://canvas.example.com",
                course_id="42",
                assignment_id=5,
                token="tok",
                published=False,
                points_possible=25,
                submission_types=["online_upload"],
            )
        assert result == {"id": 5, "name": "Essay"}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PUT"
        assert "assignments/5" in req.full_url
        assert b"assignment%5Bpublished%5D=false" in req.data
        assert b"assignment%5Bpoints_possible%5D=25" in req.data
        assert b"assignment%5Bsubmission_types%5D%5B%5D=online_upload" in req.data

    def test_requires_at_least_one_field(self):
        with pytest.raises(CanvasAPIError, match="At least one assignment field"):
            update_course_assignment(
                base_url="https://canvas.example.com",
                course_id="42",
                assignment_id=5,
                token="tok",
            )


class TestUpdateDiscussionTopic:
    def test_updates_published_flag(self):
        body = json.dumps({"id": 9, "published": False}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = update_discussion_topic(
                base_url="https://canvas.example.com",
                course_id="42",
                topic_id=9,
                token="tok",
                published=False,
            )
        assert result == {"id": 9, "published": False}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PUT"
        assert "discussion_topics/9" in req.full_url
        assert b"published=false" in req.data


class TestUpdateCoursePage:
    def test_updates_body_and_publish_state(self):
        body = json.dumps({"url": "page-a"}).encode()
        with patch("lms_migration.canvas_api.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(body, {})
            result = update_course_page(
                base_url="https://canvas.example.com",
                course_id="42",
                page_url="page-a",
                token="tok",
                body_html="<p>Updated</p>",
                published=True,
            )
        assert result == {"url": "page-a"}
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PUT"
        assert "pages/page-a" in req.full_url
        assert b"wiki_page%5Bbody%5D=%3Cp%3EUpdated%3C%2Fp%3E" in req.data
        assert b"wiki_page%5Bpublished%5D=true" in req.data

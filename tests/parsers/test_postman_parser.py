"""Tests for the Postman collection parser."""

from __future__ import annotations

import pytest

from phantom_api.parsers.base import ParserError
from phantom_api.parsers.postman_parser import PostmanParser


def test_parses_top_level_and_nested_requests(collection_path):
    spec = PostmanParser(collection_path).parse()
    assert spec.source_type == "postman"
    paths = {(r.normalized_method(), r.path) for r in spec.routes}
    assert ("GET", "/widgets") in paths
    assert ("GET", "/widgets/{id}") in paths
    assert ("POST", "/widgets") in paths


def test_uses_saved_example_body(collection_path):
    spec = PostmanParser(collection_path).parse()
    get_list = next(r for r in spec.routes if r.path == "/widgets" and r.method == "GET")
    assert get_list.response_example == [{"id": 1, "name": "Widget A"}]


def test_status_code_from_example(collection_path):
    spec = PostmanParser(collection_path).parse()
    post = next(r for r in spec.routes if r.method == "POST")
    assert post.status_code == 201


def test_converts_path_variables(collection_path):
    spec = PostmanParser(collection_path).parse()
    assert any(r.path == "/widgets/{id}" for r in spec.routes)


def test_can_parse_detection(collection_path, users_json_path):
    assert PostmanParser(collection_path).can_parse() is True
    assert PostmanParser(users_json_path).can_parse() is False


def test_missing_item_array_raises(tmp_path):
    file = tmp_path / "bad.json"
    file.write_text('{"info": {"schema": "https://schema.getpostman.com/x"}}', encoding="utf-8")
    with pytest.raises(ParserError):
        PostmanParser(file).parse()


def test_extract_path_from_raw_string():
    parser = PostmanParser.__new__(PostmanParser)
    assert parser._extract_path("https://api.x.com/a/b?q=1") == "/a/b"
    assert parser._extract_path({"raw": "api.x.com/foo"}) == "/foo"
    assert parser._extract_path(None) is None


def test_matches_rejects_non_dict(tmp_path):
    file = tmp_path / "list.json"
    file.write_text("[1, 2]", encoding="utf-8")
    assert PostmanParser(file).can_parse() is False


def test_non_dict_top_level_raises(tmp_path):
    file = tmp_path / "scalar.json"
    file.write_text('"hi"', encoding="utf-8")
    with pytest.raises(ParserError):
        PostmanParser(file).parse()


def test_no_requests_raises(tmp_path):
    file = tmp_path / "empty.json"
    file.write_text(
        '{"info": {"name": "x", "schema": "getpostman.com"}, "item": [{"name": "folder", '
        '"item": []}]}',
        encoding="utf-8",
    )
    with pytest.raises(ParserError):
        PostmanParser(file).parse()


def test_request_without_url_is_skipped(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(
        '{"info": {"name": "x", "schema": "getpostman.com"}, "item": ['
        '{"name": "no url", "request": {"method": "GET"}},'
        '{"name": "ok", "request": {"method": "GET", "url": {"path": ["ok"]}}}'
        "]}",
        encoding="utf-8",
    )
    spec = PostmanParser(file).parse()
    assert {r.path for r in spec.routes} == {"/ok"}


def test_extract_path_invalid_type():
    parser = PostmanParser.__new__(PostmanParser)
    assert parser._extract_path(123) is None


def test_plain_text_body(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(
        '{"info": {"name": "x", "schema": "getpostman.com"}, "item": ['
        '{"name": "text", "request": {"method": "GET", "url": {"path": ["t"]}}, '
        '"response": [{"code": 200, "header": [{"key": "X-Trace", "value": "1"}], '
        '"body": "just text"}]}'
        "]}",
        encoding="utf-8",
    )
    spec = PostmanParser(file).parse()
    route = spec.routes[0]
    assert route.response_example == "just text"
    assert route.content_type == "text/plain"
    assert route.response_headers.get("X-Trace") == "1"


def test_response_without_examples(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(
        '{"info": {"name": "x", "schema": "getpostman.com"}, "item": ['
        '{"name": "none", "request": {"method": "GET", "url": {"path": ["n"]}}}'
        "]}",
        encoding="utf-8",
    )
    spec = PostmanParser(file).parse()
    assert spec.routes[0].status_code == 200
    assert spec.routes[0].response_example is None

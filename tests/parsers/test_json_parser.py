"""Tests for the JSON CRUD parser."""

from __future__ import annotations

import json

import pytest

from phantom_api.parsers.base import ParserError
from phantom_api.parsers.json_parser import JSONParser


def test_generates_crud_for_each_collection(users_json_path):
    spec = JSONParser(users_json_path).parse()
    assert spec.source_type == "json"
    paths = {(r.normalized_method(), r.path) for r in spec.routes}
    for collection in ("users", "posts"):
        assert ("GET", f"/{collection}") in paths
        assert ("POST", f"/{collection}") in paths
        assert ("GET", f"/{collection}/{{id}}") in paths
        assert ("PUT", f"/{collection}/{{id}}") in paths
        assert ("DELETE", f"/{collection}/{{id}}") in paths


def test_list_route_returns_full_array(users_json_path):
    spec = JSONParser(users_json_path).parse()
    get_users = next(r for r in spec.routes if r.path == "/users" and r.method == "GET")
    assert isinstance(get_users.response_example, list)
    assert len(get_users.response_example) == 2


def test_bare_array_uses_file_stem(tmp_path):
    file = tmp_path / "products.json"
    file.write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
    spec = JSONParser(file).parse()
    assert any(r.path == "/products" for r in spec.routes)


def test_single_object_becomes_collection(tmp_path):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"debug": True, "level": 5}), encoding="utf-8")
    spec = JSONParser(file).parse()
    assert spec.route_count() == 5


def test_infers_schema_types(users_json_path):
    spec = JSONParser(users_json_path).parse()
    post = next(r for r in spec.routes if r.path == "/users" and r.method == "POST")
    props = post.response_schema["properties"]
    assert props["id"]["type"] == "integer"
    assert props["name"]["type"] == "string"
    assert props["active"]["type"] == "boolean"


def test_scalar_root_raises(tmp_path):
    file = tmp_path / "scalar.json"
    file.write_text(json.dumps("hello"), encoding="utf-8")
    with pytest.raises(ParserError):
        JSONParser(file).parse()

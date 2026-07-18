"""Miscellaneous coverage tests for schema inference, handlers, and helpers."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from phantom_api.generators.example_extractor import ExampleExtractor
from phantom_api.models import MockRoute, MockSpec
from phantom_api.parsers.json_parser import JSONParser
from phantom_api.recorder.proxy import _validate_upstream
from phantom_api.server.app import ServerConfig, create_app


def test_json_schema_inference_all_types(tmp_path):
    payload = {
        "records": [
            {
                "price": 9.99,
                "tags": ["a", "b"],
                "empty": [],
                "meta": {"nested": 1},
                "missing": None,
            }
        ]
    }
    file = tmp_path / "data.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    spec = JSONParser(file).parse()
    post = next(r for r in spec.routes if r.method == "POST")
    props = post.response_schema["properties"]
    assert props["price"]["type"] == "number"
    assert props["tags"]["type"] == "array"
    assert props["empty"] == {"type": "array", "items": {"type": "string"}}
    assert props["meta"]["type"] == "object"
    assert props["missing"] == {"type": "string"}


def test_json_can_parse_array(tmp_path):
    file = tmp_path / "arr.json"
    file.write_text("[1, 2, 3]", encoding="utf-8")
    assert JSONParser(file).can_parse() is True


def test_example_extractor_schema_example():
    route = MockRoute(
        method="GET", path="/x", response_schema={"type": "object", "example": {"a": 1}}
    )
    assert ExampleExtractor.has_example(route) is True
    assert ExampleExtractor.extract(route) == {"a": 1}


def test_example_extractor_none():
    route = MockRoute(method="GET", path="/x", response_schema={"type": "object"})
    assert ExampleExtractor.has_example(route) is False


def test_plain_text_route_served():
    spec = MockSpec(source_type="postman")
    spec.routes.append(
        MockRoute(
            method="GET",
            path="/text",
            status_code=200,
            response_example="hello world",
            content_type="text/plain",
        )
    )
    client = TestClient(create_app(spec, ServerConfig(seed=1)))
    resp = client.get("/text")
    assert resp.status_code == 200
    assert resp.text == "hello world"
    assert resp.headers["content-type"].startswith("text/plain")


def test_validate_upstream_missing_host():
    import pytest

    with pytest.raises(ValueError):
        _validate_upstream("http://")


def test_validate_upstream_ok():
    assert _validate_upstream("https://api.example.com/") == "https://api.example.com"

"""Edge-case tests for base parser, middleware, and hot reload."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from phantom_api.parsers import detect_and_parse
from phantom_api.parsers.base import ParserError
from phantom_api.parsers.json_parser import JSONParser
from phantom_api.parsers.openapi_parser import OpenAPIParser
from phantom_api.server.app import ServerConfig, create_app

# --- base parser ---------------------------------------------------------------


def test_directory_is_not_a_file(tmp_path):
    with pytest.raises(ParserError):
        JSONParser(tmp_path).parse()


def test_file_too_large(tmp_path, monkeypatch):
    import phantom_api.parsers.base as base

    monkeypatch.setattr(base, "MAX_SPEC_BYTES", 4)
    big = tmp_path / "big.json"
    big.write_text("[1, 2, 3, 4, 5]", encoding="utf-8")
    with pytest.raises(ParserError):
        JSONParser(big).parse()


def test_invalid_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: : : :\n  - broken", encoding="utf-8")
    with pytest.raises(ParserError):
        OpenAPIParser(bad).parse()


def test_unknown_extension_json_fallback(tmp_path):
    file = tmp_path / "data.txt"
    file.write_text('[{"id": 1}]', encoding="utf-8")
    spec = JSONParser(file).parse()
    assert spec.route_count() == 5


def test_unknown_extension_yaml_fallback(tmp_path):
    file = tmp_path / "data.txt"
    file.write_text("- id: 1\n- id: 2\n", encoding="utf-8")
    spec = JSONParser(file).parse()
    assert spec.route_count() == 5


def test_detect_and_parse_unknown_raises(tmp_path):
    file = tmp_path / "weird.txt"
    file.write_text("just some text", encoding="utf-8")
    with pytest.raises(ParserError):
        detect_and_parse(str(file))


def test_get_parser_unknown_type():
    from phantom_api.parsers import get_parser

    with pytest.raises(ParserError):
        get_parser("graphql")


# --- middleware ----------------------------------------------------------------


def _client(spec_path, **cfg):
    spec = detect_and_parse(str(spec_path))
    return TestClient(create_app(spec, ServerConfig(seed=1, **cfg)))


def test_invalid_delay_value_ignored(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets?__delay=abc")
    assert resp.status_code == 200


def test_out_of_range_status_ignored(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets?__status=200")
    assert resp.status_code == 200


def test_non_numeric_status_ignored(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets", headers={"X-Mock-Status": "abc"})
    assert resp.status_code == 200


def test_default_delay_applied(petstore_path):
    client = _client(petstore_path, delay_ms=1)
    resp = client.get("/pets")
    assert resp.status_code == 200


def test_delay_header_override(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets", headers={"X-Mock-Delay": "1"})
    assert resp.status_code == 200


# --- hot reload ----------------------------------------------------------------


async def test_hot_reload_reparses(monkeypatch, petstore_path):
    from phantom_api.server import hot_reload as hr

    spec = detect_and_parse(str(petstore_path))
    app = create_app(spec)
    messages: list[str] = []

    async def fake_awatch(_path):
        yield {("modified", str(petstore_path))}

    monkeypatch.setattr(hr, "awatch", fake_awatch)
    hr.attach_hot_reload(app, petstore_path, None, on_reload=messages.append)

    for handler in app.router.on_startup:
        await handler()
    for _ in range(10):
        await asyncio.sleep(0)
    for handler in app.router.on_shutdown:
        await handler()

    assert any("reloaded" in m for m in messages)


async def test_hot_reload_handles_parse_error(monkeypatch, petstore_path):
    from phantom_api.server import hot_reload as hr

    spec = detect_and_parse(str(petstore_path))
    app = create_app(spec)
    messages: list[str] = []

    async def fake_awatch(_path):
        yield {("modified", str(petstore_path))}

    def boom(*_a, **_k):
        raise ParserError("broken spec")

    monkeypatch.setattr(hr, "awatch", fake_awatch)
    monkeypatch.setattr(hr, "detect_and_parse", boom)
    hr.attach_hot_reload(app, petstore_path, None, on_reload=messages.append)

    for handler in app.router.on_startup:
        await handler()
    for _ in range(10):
        await asyncio.sleep(0)
    for handler in app.router.on_shutdown:
        await handler()

    assert any("reload failed" in m for m in messages)

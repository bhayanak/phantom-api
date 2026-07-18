"""Tests for the OpenAPI / Swagger parser."""

from __future__ import annotations

import pytest

from phantom_api.parsers import detect_and_parse
from phantom_api.parsers.base import ParserError
from phantom_api.parsers.openapi_parser import OpenAPIParser


def test_parses_openapi_3(petstore_path):
    spec = OpenAPIParser(petstore_path).parse()
    assert spec.source_type == "openapi"
    assert spec.title == "Petstore"
    paths = {(r.normalized_method(), r.path) for r in spec.routes}
    assert ("GET", "/pets") in paths
    assert ("POST", "/pets") in paths
    assert ("GET", "/pets/{petId}") in paths
    assert ("DELETE", "/pets/{petId}") in paths


def test_openapi_resolves_refs(petstore_path):
    spec = OpenAPIParser(petstore_path).parse()
    get_pets = next(r for r in spec.routes if r.path == "/pets" and r.method == "GET")
    assert get_pets.response_schema["type"] == "array"
    item = get_pets.response_schema["items"]
    assert item["type"] == "object"
    assert "name" in item["properties"]


def test_openapi_status_codes(petstore_path):
    spec = OpenAPIParser(petstore_path).parse()
    post = next(r for r in spec.routes if r.path == "/pets" and r.method == "POST")
    assert post.status_code == 201
    delete = next(r for r in spec.routes if r.method == "DELETE")
    assert delete.status_code == 204


def test_parses_swagger_2(swagger_path):
    spec = OpenAPIParser(swagger_path).parse()
    assert spec.source_type == "swagger"
    paths = {(r.normalized_method(), r.path) for r in spec.routes}
    assert ("GET", "/users") in paths
    assert ("GET", "/users/{userId}") in paths


def test_can_parse_detection(petstore_path, users_json_path):
    assert OpenAPIParser(petstore_path).can_parse() is True
    assert OpenAPIParser(users_json_path).can_parse() is False


def test_detect_and_parse_auto(petstore_path):
    spec = detect_and_parse(str(petstore_path))
    assert spec.source_type == "openapi"


def test_missing_file_raises():
    with pytest.raises(ParserError):
        OpenAPIParser("does-not-exist.yaml").parse()


def test_explicit_type_override(petstore_path):
    spec = detect_and_parse(str(petstore_path), "openapi")
    assert spec.route_count() > 0


def test_matches_rejects_non_dict(tmp_path):
    file = tmp_path / "list.json"
    file.write_text("[1, 2, 3]", encoding="utf-8")
    assert OpenAPIParser(file).can_parse() is False


def test_non_dict_top_level_raises(tmp_path):
    file = tmp_path / "scalar.yaml"
    file.write_text("42", encoding="utf-8")
    with pytest.raises(ParserError):
        OpenAPIParser(file).parse()


def test_paths_not_mapping_raises(tmp_path):
    file = tmp_path / "spec.yaml"
    file.write_text("openapi: 3.0.0\ninfo:\n  title: x\npaths: broken\n", encoding="utf-8")
    with pytest.raises(ParserError):
        OpenAPIParser(file).parse()


def test_skips_non_http_and_non_dict(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    x-extension: ignore\n"
        "    get:\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
        "  /b: not-a-mapping\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert {(r.method, r.path) for r in spec.routes} == {("GET", "/a")}


def test_default_response_used(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get:\n"
        "      responses:\n"
        "        default:\n"
        "          description: fallback\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].status_code == 200


def test_non_2xx_response_used(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get:\n"
        "      responses:\n"
        "        '404':\n"
        "          description: missing\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].status_code == 404


def test_openapi_example_from_examples_map(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get:\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
        "          content:\n"
        "            application/json:\n"
        "              schema:\n                type: object\n"
        "              examples:\n"
        "                sample:\n"
        "                  value:\n                    hello: world\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].response_example == {"hello": "world"}


def test_openapi_non_json_media_type(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get:\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
        "          content:\n"
        "            text/plain:\n"
        "              schema:\n                type: string\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].content_type == "text/plain"


def test_swagger_example_used(tmp_path):
    content = {
        "swagger": "2.0",
        "info": {"title": "x", "version": "1"},
        "paths": {
            "/a": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"type": "object"},
                            "examples": {"application/json": {"hi": 1}},
                        }
                    }
                }
            }
        },
    }
    import json

    file = tmp_path / "swagger.json"
    file.write_text(json.dumps(content), encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].response_example == {"hi": 1}


def test_external_ref_is_empty():
    parser = OpenAPIParser.__new__(OpenAPIParser)
    assert parser._resolve_ref({"$ref": "http://external/Thing"}, {}) == {}


def test_missing_pointer_is_empty():
    parser = OpenAPIParser.__new__(OpenAPIParser)
    resolved = parser._resolve_ref({"$ref": "#/components/schemas/Nope"}, {"components": {}})
    assert resolved == {}


def test_cyclic_ref_is_safe():
    root = {"components": {"schemas": {"Node": {"$ref": "#/components/schemas/Node"}}}}
    parser = OpenAPIParser.__new__(OpenAPIParser)
    resolved = parser._resolve_ref({"$ref": "#/components/schemas/Node"}, root)
    assert resolved == {}


def test_method_value_not_dict_skipped(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get: not-a-mapping\n"
        "    post:\n"
        "      responses:\n"
        "        '200':\n          description: ok\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert {r.method for r in spec.routes} == {"POST"}


def test_only_non_digit_response(tmp_path):
    import json

    content = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "paths": {"/a": {"get": {"responses": {"x-custom": {"description": "weird"}}}}},
    }
    file = tmp_path / "spec.json"
    file.write_text(json.dumps(content), encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].status_code == 200


def test_response_def_not_dict(tmp_path):
    import json

    content = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "paths": {"/a": {"get": {"responses": {"200": "not-a-mapping"}}}},
    }
    file = tmp_path / "spec.json"
    file.write_text(json.dumps(content), encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].response_schema is None


def test_content_schema_without_example(tmp_path):
    content = (
        "openapi: 3.0.0\n"
        "info:\n  title: x\n"
        "paths:\n"
        "  /a:\n"
        "    get:\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
        "          content:\n"
        "            application/json:\n"
        "              schema:\n                type: object\n"
        "              examples: {}\n"
    )
    file = tmp_path / "spec.yaml"
    file.write_text(content, encoding="utf-8")
    spec = OpenAPIParser(file).parse()
    assert spec.routes[0].response_example is None

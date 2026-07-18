"""Integration tests for the built server (routing, middleware, admin)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from phantom_api.parsers import detect_and_parse
from phantom_api.server.app import ServerConfig, create_app


def _client(spec_path, **cfg):
    spec = detect_and_parse(str(spec_path))
    app = create_app(spec, ServerConfig(seed=1, **cfg))
    return TestClient(app)


def test_get_collection(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_path_param_route(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets/123")
    assert resp.status_code == 200
    body = resp.json()
    assert "name" in body


def test_post_returns_201(petstore_path):
    client = _client(petstore_path)
    resp = client.post("/pets")
    assert resp.status_code == 201


def test_delete_returns_204(petstore_path):
    client = _client(petstore_path)
    resp = client.delete("/pets/1")
    assert resp.status_code == 204
    assert resp.content == b""


def test_error_injection_via_query(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets?__status=503")
    assert resp.status_code == 503
    assert resp.json()["error"] is True


def test_error_injection_via_header(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets", headers={"X-Mock-Status": "404"})
    assert resp.status_code == 404


def test_admin_routes_endpoint(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/__admin/routes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] > 0
    assert data["source_type"] == "openapi"


def test_admin_stats_and_reset(petstore_path):
    client = _client(petstore_path)
    client.get("/pets")
    client.get("/pets")
    stats = client.get("/__admin/stats").json()
    assert stats["total_requests"] >= 2

    reset = client.post("/__admin/reset")
    assert reset.json()["reset"] is True
    stats_after = client.get("/__admin/stats").json()
    assert stats_after["total_requests"] == 0


def test_cors_headers_present(petstore_path):
    client = _client(petstore_path)
    resp = client.get("/pets", headers={"Origin": "http://localhost:5173"})
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_postman_example_served(collection_path):
    client = _client(collection_path)
    resp = client.get("/widgets")
    assert resp.status_code == 200
    assert resp.json() == [{"id": 1, "name": "Widget A"}]


def test_json_crud_served(users_json_path):
    client = _client(users_json_path)
    resp = client.get("/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

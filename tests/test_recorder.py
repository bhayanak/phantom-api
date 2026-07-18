"""Tests for record & replay: storage, proxy, and replayer."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from phantom_api.recorder import proxy as proxy_mod
from phantom_api.recorder.proxy import _validate_upstream, create_proxy_app
from phantom_api.recorder.replayer import build_replay_spec, create_replay_app
from phantom_api.recorder.storage import Interaction, RecordingStorage


def test_storage_save_and_load(tmp_path):
    storage = RecordingStorage(tmp_path)
    interaction = Interaction(method="GET", path="/things", status_code=200, body=[{"id": 1}])
    storage.save(interaction)
    assert storage.count() == 1
    loaded = storage.load_all()
    assert loaded[0].path == "/things"
    assert loaded[0].body == [{"id": 1}]


def test_storage_ignores_bad_files(tmp_path):
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    storage = RecordingStorage(tmp_path)
    assert storage.load_all() == []


def test_interaction_filename_is_hashed():
    interaction = Interaction(method="GET", path="/a/b")
    name = interaction.filename()
    assert name.startswith("GET_")
    assert name.endswith(".json")
    assert "/" not in name


def test_validate_upstream_rejects_bad_scheme():
    with pytest.raises(ValueError):
        _validate_upstream("ftp://example.com")
    with pytest.raises(ValueError):
        _validate_upstream("not-a-url")


def test_build_replay_spec(tmp_path):
    storage = RecordingStorage(tmp_path)
    storage.save(Interaction(method="GET", path="/users", body=[{"id": 1}]))
    storage.save(Interaction(method="POST", path="/users", status_code=201, body={"id": 2}))
    spec = build_replay_spec(tmp_path)
    assert spec.route_count() == 2


def test_replay_app_serves_recordings(tmp_path):
    storage = RecordingStorage(tmp_path)
    storage.save(Interaction(method="GET", path="/users", body=[{"id": 1}]))
    app = create_replay_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/users")
    assert resp.status_code == 200
    assert resp.json() == [{"id": 1}]


def test_proxy_records_upstream(tmp_path, monkeypatch):
    upstream = FastAPI()

    @upstream.get("/ping")
    def ping():
        return {"pong": True}

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs.pop("base_url", None)
        return real_client(transport=httpx.ASGITransport(app=upstream), base_url="http://upstream")

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", fake_client)

    storage = RecordingStorage(tmp_path)
    app = create_proxy_app("http://upstream", storage)
    client = TestClient(app)
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}
    assert storage.count() == 1
    recorded = storage.load_all()[0]
    assert recorded.path == "/ping"
    assert recorded.body == {"pong": True}


def test_proxy_records_text_upstream(tmp_path, monkeypatch):
    upstream = FastAPI()

    @upstream.get("/text")
    def text():
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse("hello")

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs.pop("base_url", None)
        return real_client(transport=httpx.ASGITransport(app=upstream), base_url="http://upstream")

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", fake_client)
    storage = RecordingStorage(tmp_path)
    client = TestClient(create_proxy_app("http://upstream", storage))
    resp = client.get("/text")
    assert resp.text == "hello"
    assert storage.load_all()[0].body == "hello"


def test_storage_count_missing_dir(tmp_path):
    storage = RecordingStorage(tmp_path / "nope")
    assert storage.count() == 0
    assert storage.load_all() == []


def test_storage_rejects_traversal(tmp_path):
    storage = RecordingStorage(tmp_path)
    with pytest.raises(ValueError):
        storage._safe_target("../escape.json")


def test_replay_deduplicates(tmp_path):
    storage = RecordingStorage(tmp_path)
    storage.save(Interaction(method="GET", path="/dup", query="a=1", body=1))
    storage.save(Interaction(method="GET", path="/dup", query="a=2", body=2))
    spec = build_replay_spec(tmp_path)
    assert spec.route_count() == 1


def test_decode_body_invalid_json():
    import httpx as _httpx

    from phantom_api.recorder.proxy import _decode_body

    response = _httpx.Response(200, text="not json")
    assert _decode_body(response, "application/json") == "not json"

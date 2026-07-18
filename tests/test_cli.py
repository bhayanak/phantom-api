"""Tests for the Typer CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from phantom_api import __version__
from phantom_api.cli import app, parse_delay

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_routes_command(petstore_path):
    result = runner.invoke(app, ["routes", str(petstore_path)])
    assert result.exit_code == 0
    assert "/pets" in result.stdout


def test_validate_command(users_json_path):
    result = runner.invoke(app, ["validate", str(users_json_path)])
    assert result.exit_code == 0
    assert "json" in result.stdout


def test_validate_bad_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('"scalar"', encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [("200ms", 200), ("1s", 1000), ("150", 150), ("2 s", 2000), ("50 ms", 50)],
)
def test_parse_delay(value, expected):
    assert parse_delay(value) == expected


def test_parse_delay_invalid():
    import typer

    with pytest.raises(typer.BadParameter):
        parse_delay("fast")


def test_serve_invokes_uvicorn(petstore_path, monkeypatch):
    calls = {}

    def fake_run(app_arg, **kwargs):
        calls["host"] = kwargs.get("host")
        calls["port"] = kwargs.get("port")

    monkeypatch.setattr("phantom_api.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["serve", str(petstore_path), "--port", "4111"])
    assert result.exit_code == 0
    assert calls["port"] == 4111
    assert calls["host"] == "127.0.0.1"


def test_replay_empty_dir_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["replay", str(tmp_path)])
    assert result.exit_code == 1


def test_record_invalid_upstream(monkeypatch):
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["record", "ftp://bad"])
    assert result.exit_code == 1


def test_serve_with_watch(petstore_path, monkeypatch):
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["serve", str(petstore_path), "--watch"])
    assert result.exit_code == 0


def test_serve_bad_delay(petstore_path, monkeypatch):
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["serve", str(petstore_path), "--delay", "fast"])
    assert result.exit_code != 0


def test_record_valid_upstream(monkeypatch, tmp_path):
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["record", "https://api.example.com", "-o", str(tmp_path / "rec")])
    assert result.exit_code == 0
    assert "Recording" in result.stdout


def test_replay_with_recordings(monkeypatch, tmp_path):
    from phantom_api.recorder.storage import Interaction, RecordingStorage

    storage = RecordingStorage(tmp_path)
    storage.save(Interaction(method="GET", path="/x", body={"ok": True}))
    monkeypatch.setattr("phantom_api.cli.uvicorn.run", lambda *a, **k: None)
    result = runner.invoke(app, ["replay", str(tmp_path)])
    assert result.exit_code == 0


def test_routes_bad_type(petstore_path):
    result = runner.invoke(app, ["routes", str(petstore_path), "--type", "graphql"])
    assert result.exit_code == 1

"""CLI paths that start a server or talk to a running one.

``uvicorn.run`` is replaced so the commands can be exercised without binding a
port, which is also how they behave in CI.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from phantom_api.cli import app

from .conftest import VCENTER_MOCK

runner = CliRunner()


@pytest.fixture
def captured_server(monkeypatch):
    """Capture what would have been served instead of binding a port."""
    captured: dict = {}

    def fake_run(application, **kwargs):
        captured["app"] = application
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    return captured


class TestServe:
    def test_corpus_only(self, tiny_corpus, captured_server):
        result = runner.invoke(
            app, ["mock", "serve", "--corpus", str(tiny_corpus), "--port", "9001"]
        )
        assert result.exit_code == 0
        assert captured_server["kwargs"]["port"] == 9001
        assert "corpus" in result.stdout

    def test_from_a_config_file(self, captured_server):
        result = runner.invoke(app, ["mock", "serve", str(VCENTER_MOCK / "phantom.yaml")])
        assert result.exit_code == 0
        assert "model" in result.stdout

    def test_cli_options_override_the_config(self, captured_server):
        result = runner.invoke(
            app,
            [
                "mock",
                "serve",
                str(VCENTER_MOCK / "phantom.yaml"),
                "--port",
                "9999",
                "--seed",
                "7",
                "--latency-ms",
                "25",
            ],
        )
        assert result.exit_code == 0
        assert captured_server["kwargs"]["port"] == 9999
        assert captured_server["app"].state.engine.pipeline.chaos.latency_ms == 25

    def test_tls_produces_a_certificate(self, tiny_corpus, captured_server):
        pytest.importorskip("cryptography")
        result = runner.invoke(
            app,
            ["mock", "serve", "--corpus", str(tiny_corpus), "--tls", "self-signed"],
        )
        assert result.exit_code == 0
        assert "ssl_certfile" in captured_server["kwargs"]
        assert "https://" in result.stdout

    def test_binding_beyond_loopback_warns(self, tiny_corpus, captured_server):
        result = runner.invoke(
            app,
            ["mock", "serve", "--corpus", str(tiny_corpus), "--host", "0.0.0.0"],
        )
        assert result.exit_code == 0
        assert "Warning" in result.stdout

    def test_a_broken_config_exits_non_zero(self, tmp_path, captured_server):
        bad = tmp_path / "bad.yaml"
        bad.write_text("mock:\n  responders:\n    - responder: corpus\n")
        result = runner.invoke(app, ["mock", "serve", str(bad)])
        assert result.exit_code == 1

    def test_record_starts_a_forwarding_server(self, tmp_path, captured_server):
        result = runner.invoke(
            app,
            [
                "mock",
                "record",
                "https://real.example.com",
                "--out",
                str(tmp_path / "corpus"),
                "--tls",
                "off",
                "--i-understand-this-hits-production",
            ],
        )
        assert result.exit_code == 0
        assert "unsanitised" in result.stdout
        assert "forward" in result.stdout


class TestLogAndVerify:
    def _patch(self, monkeypatch, payload: dict, *, post: bool = False):
        def fake(url, **kwargs):
            return httpx.Response(
                200, json=payload, request=httpx.Request("POST" if post else "GET", url)
            )

        monkeypatch.setattr("httpx.post" if post else "httpx.get", fake)

    def test_log_renders_entries(self, monkeypatch):
        self._patch(
            monkeypatch,
            {
                "count": 1,
                "entries": [
                    {
                        "operation": "Login",
                        "protocol": "soap",
                        "responder": "model",
                        "status": 200,
                        "duration_ms": 1.5,
                        "bytes": 120,
                    }
                ],
            },
        )
        result = runner.invoke(app, ["mock", "log"])
        assert result.exit_code == 0
        assert "Login" in result.stdout

    def test_verify_succeeds(self, monkeypatch):
        self._patch(monkeypatch, {"operation": "Login", "seen": 2, "satisfied": True}, post=True)
        result = runner.invoke(app, ["mock", "verify", "Login"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["satisfied"] is True

    def test_verify_fails_loudly(self, monkeypatch):
        """A failed assertion must exit non-zero so CI notices."""
        self._patch(monkeypatch, {"operation": "Login", "seen": 0, "satisfied": False}, post=True)
        result = runner.invoke(app, ["mock", "verify", "Login", "--at-least", "1"])
        assert result.exit_code == 1

    def test_verify_reports_an_unreachable_mock(self, monkeypatch):
        def fake(url, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("httpx.post", fake)
        result = runner.invoke(app, ["mock", "verify", "Login"])
        assert result.exit_code == 1
        assert "could not reach" in result.stdout

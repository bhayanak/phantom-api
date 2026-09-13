"""CLI, recorder, forwarding, routing and TLS."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from phantom_api.cli import app
from phantom_api.mock.config import MatchRule, ProtocolConfig, ResponderRule, SessionRule
from phantom_api.mock.record.recorder import Recorder
from phantom_api.mock.responders.corpus import CorpusResponder
from phantom_api.mock.responders.forward import ForwardResponder
from phantom_api.mock.router import (
    ProtocolBinding,
    ProtocolRouter,
    ResponderBinding,
    ResponderRouter,
    path_matches,
)
from phantom_api.mock.types import MockError, Operation, RawRequest

from .conftest import VCENTER_MOCK, soap_request

runner = CliRunner()


class TestPathMatching:
    @pytest.mark.parametrize(
        ("pattern", "path", "expected"),
        [
            ("/**", "/anything/deep", True),
            ("/rest/**", "/rest/a/b", True),
            ("/rest/**", "/rest", True),
            ("/rest/**", "/restaurant", False),
            ("/sdk", "/sdk", True),
            ("/sdk", "/sdk/extra", False),
            ("*", "/whatever", True),
        ],
    )
    def test_patterns(self, pattern, path, expected):
        assert path_matches(pattern, path) is expected


class TestRouters:
    def test_first_protocol_binding_wins(self):
        soap = ProtocolBinding(ProtocolConfig(pack="soap", paths=["/sdk"]), object())
        rest = ProtocolBinding(ProtocolConfig(pack="openapi", paths=["/**"]), object())
        router = ProtocolRouter([soap, rest])
        assert router.select(RawRequest("POST", "/sdk", "", {}, b"")) is soap
        assert router.select(RawRequest("GET", "/rest/x", "", {}, b"")) is rest

    def test_session_rule_is_selected_per_path(self):
        binding = ProtocolBinding(
            ProtocolConfig(
                pack="soap",
                paths=["/**"],
                session={
                    "/sdk": SessionRule(transport="cookie", name="a"),
                    "/pbm/**": SessionRule(transport="soap-header", name="b"),
                },
            ),
            object(),
        )
        assert binding.session_for("/sdk").name == "a"
        assert binding.session_for("/pbm/sdk").name == "b"
        assert binding.session_for("/other") is None

    def test_operation_patterns(self):
        rule = ResponderRule(
            match=MatchRule(operation=["Retrieve*", "re:^Query"]), responder="model"
        )
        binding = ResponderBinding(rule=rule, responder=object())
        assert binding.matches(Operation("RetrieveProperties", "soap"))
        assert binding.matches(Operation("QueryOptions", "soap"))
        assert not binding.matches(Operation("Login", "soap"))

    def test_protocol_filter(self):
        rule = ResponderRule(match=MatchRule(protocol="openapi"), responder="corpus")
        binding = ResponderBinding(rule=rule, responder=object())
        assert binding.matches(Operation("x", "openapi"))
        assert not binding.matches(Operation("x", "soap"))

    def test_wildcard_matches_everything(self):
        rule = ResponderRule(match="*", responder="corpus")  # type: ignore[arg-type]
        assert ResponderBinding(rule=rule, responder=object()).matches(Operation("x", "soap"))

    def test_candidates_preserve_declaration_order(self):
        first = ResponderBinding(ResponderRule(match=MatchRule(operation=["A"])), object())
        second = ResponderBinding(ResponderRule(match="*"), object())  # type: ignore[arg-type]
        router = ResponderRouter([first, second])
        assert router.candidates(Operation("A", "soap")) == [first, second]
        assert router.candidates(Operation("B", "soap")) == [second]


class TestRecorder:
    def test_writes_a_replayable_corpus(self, tmp_path):
        recorder = Recorder(tmp_path / "corpus")
        op = Operation(
            name="Ping", protocol="soap", request=soap_request('<Ping xmlns="urn:demo"/>')
        )
        recorder.record(op, 200, "<PingResponse/>")

        manifest = json.loads((tmp_path / "corpus" / "manifest.json").read_text())
        assert manifest["sanitised"] is False
        assert len(manifest["exchanges"]) == 1
        assert CorpusResponder(tmp_path / "corpus").exchanges

    def test_appends_across_sessions(self, tmp_path):
        op = Operation(name="Ping", protocol="soap", request=soap_request("<Ping/>"))
        Recorder(tmp_path / "c").record(op, 200, "<a/>")
        second = Recorder(tmp_path / "c")
        second.record(op, 200, "<b/>")
        assert len(second) == 2

    def test_unsafe_labels_do_not_escape_the_directory(self, tmp_path):
        recorder = Recorder(tmp_path / "c")
        op = Operation(name="../../etc/passwd", protocol="soap", request=soap_request("<X/>"))
        entry = recorder.record(op, 200, "<a/>")
        assert ".." not in entry["request"]
        assert (tmp_path / "c" / entry["request"]).exists()


class TestForwardResponder:
    def test_requires_a_target(self):
        with pytest.raises(ValueError, match="target"):
            ForwardResponder("")

    def test_proxies_and_records(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<Upstream/>")

        responder = ForwardResponder("https://upstream.example", record_to=tmp_path / "c")
        responder._client = httpx.Client(transport=httpx.MockTransport(handler))
        op = Operation(name="Ping", protocol="soap", request=soap_request("<Ping/>"))
        response = responder.respond(op)
        assert response.body == b"<Upstream/>"
        assert len(json.loads((tmp_path / "c" / "manifest.json").read_text())["exchanges"]) == 1
        responder.close()

    def test_unreachable_upstream_is_a_bad_gateway(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        responder = ForwardResponder("https://upstream.example")
        responder._client = httpx.Client(transport=httpx.MockTransport(handler))
        op = Operation(name="Ping", protocol="soap", request=soap_request("<Ping/>"))
        with pytest.raises(MockError) as excinfo:
            responder.respond(op)
        assert excinfo.value.status == 502
        responder.close()

    def test_needs_the_original_request(self):
        responder = ForwardResponder("https://upstream.example")
        with pytest.raises(MockError, match="original request"):
            responder.respond(Operation(name="Ping", protocol="soap"))
        responder.close()


class TestTls:
    def test_generates_a_usable_certificate(self, tmp_path):
        cryptography = pytest.importorskip("cryptography")
        from phantom_api.mock.tls import generate_self_signed

        material = generate_self_signed("mock.example.com", out_dir=tmp_path)
        assert material.cert_path.exists()
        assert material.key_path.exists()
        assert material.ephemeral is False

        cert = cryptography.x509.load_pem_x509_certificate(material.cert_path.read_bytes())
        names = cert.extensions.get_extension_for_class(
            cryptography.x509.SubjectAlternativeName
        ).value.get_values_for_type(cryptography.x509.DNSName)
        assert "mock.example.com" in names
        assert "localhost" in names

    def test_the_private_key_is_not_world_readable(self, tmp_path):
        pytest.importorskip("cryptography")
        from phantom_api.mock.tls import generate_self_signed

        material = generate_self_signed("localhost", out_dir=tmp_path)
        assert (material.key_path.stat().st_mode & 0o077) == 0


class TestCli:
    def test_list_packs_shows_both_kinds(self):
        result = runner.invoke(app, ["mock", "list-packs"])
        assert result.exit_code == 0
        assert "soap" in result.stdout
        assert "vcenter" in result.stdout

    def test_validate_accepts_the_shipped_config(self):
        result = runner.invoke(app, ["mock", "validate", str(VCENTER_MOCK / "phantom.yaml")])
        assert result.exit_code == 0
        assert "OK" in result.stdout

    def test_validate_rejects_a_broken_config(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("mock:\n  responders:\n    - responder: corpus\n")
        result = runner.invoke(app, ["mock", "validate", str(bad)])
        assert result.exit_code == 1

    def test_inspect_lists_operations(self, tiny_corpus):
        result = runner.invoke(app, ["mock", "inspect", str(tiny_corpus)])
        assert result.exit_code == 0
        assert "Ping" in result.stdout

    def test_inspect_rejects_a_non_corpus(self, tmp_path):
        result = runner.invoke(app, ["mock", "inspect", str(tmp_path)])
        assert result.exit_code == 1

    def test_serve_without_arguments_fails_clearly(self):
        result = runner.invoke(app, ["mock", "serve"])
        assert result.exit_code == 1
        assert "configuration file or --corpus" in result.stdout

    def test_record_requires_explicit_acknowledgement(self, tmp_path):
        """Recording sends real traffic to a live system; that must be deliberate."""
        result = runner.invoke(
            app, ["mock", "record", "https://real.example.com", "--out", str(tmp_path / "c")]
        )
        assert result.exit_code == 1
        assert "--i-understand-this-hits-production" in result.stdout

    def test_record_refuses_loopback(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "mock",
                "record",
                "https://127.0.0.1:8443",
                "--out",
                str(tmp_path / "c"),
                "--i-understand-this-hits-production",
            ],
        )
        assert result.exit_code == 1
        assert "loopback" in result.stdout

    @pytest.mark.parametrize("kind", ["corpus", "template", "model"])
    def test_init_writes_a_starter_config(self, tmp_path, kind):
        out = tmp_path / f"{kind}.yaml"
        result = runner.invoke(app, ["mock", "init", kind, "--out", str(out)])
        assert result.exit_code == 0
        assert out.exists()

        from phantom_api.mock.config import load_config

        assert load_config(out).name

    def test_init_refuses_to_overwrite(self, tmp_path):
        out = tmp_path / "c.yaml"
        out.write_text("existing")
        result = runner.invoke(app, ["mock", "init", "corpus", "--out", str(out)])
        assert result.exit_code == 1

    def test_init_rejects_an_unknown_kind(self, tmp_path):
        result = runner.invoke(
            app, ["mock", "init", "telepathy", "--out", str(tmp_path / "x.yaml")]
        )
        assert result.exit_code == 1

    def test_log_reports_an_unreachable_mock(self):
        result = runner.invoke(app, ["mock", "log", "--url", "http://127.0.0.1:1"])
        assert result.exit_code == 1
        assert "could not reach" in result.stdout

    def test_serve_rejects_a_directory_without_a_manifest(self, tmp_path):
        result = runner.invoke(app, ["mock", "serve", "--corpus", str(tmp_path)])
        assert result.exit_code == 1
        assert "manifest.json" in result.stdout


class TestCorpusOnlyConfig:
    def test_replays_without_any_configuration(self, tiny_corpus):
        from fastapi.testclient import TestClient

        from phantom_api.mock.config import corpus_only_config
        from phantom_api.mock.engine import create_mock_app

        config = corpus_only_config(tiny_corpus, host="127.0.0.1", port=0, tls="off")
        client = TestClient(create_mock_app(config))
        response = client.post(
            "/sdk",
            content=Path(tiny_corpus / "soap" / "001-Ping.req.xml").read_text(),
            headers={"content-type": "text/xml"},
        )
        assert response.status_code == 200
        assert b"pong" in response.content

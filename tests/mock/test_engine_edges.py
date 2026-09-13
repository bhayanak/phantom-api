"""Engine composition, error paths and the edges the happy path never reaches."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from phantom_api.mock.config import (
    ChaosConfig,
    ListenConfig,
    MatchRule,
    MockConfig,
    ModelConfig,
    ProtocolConfig,
    ResponderRule,
)
from phantom_api.mock.engine import MockEngine, create_mock_app
from phantom_api.mock.packs.base import available, get_pack
from phantom_api.mock.protocols.openapi import OpenApiPack
from phantom_api.mock.types import MockError, NotHandled, Operation, RawRequest

from .conftest import SOAP_ENVELOPE, VCENTER_MOCK

HEADERS = {"content-type": "text/xml; charset=utf-8"}


def corpus_config(corpus: Path, **kwargs) -> MockConfig:
    return MockConfig(
        name="t",
        listen=ListenConfig(port=0, tls="off"),
        protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
        responders=[
            ResponderRule(match={}, responder="corpus", corpus=corpus, **kwargs)  # type: ignore[arg-type]
        ],
        base_dir=corpus.parent,
    )


class TestEngineErrorPaths:
    def test_unparseable_xml_is_a_fault_not_a_crash(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        response = client.post("/sdk", content="<not-xml", headers=HEADERS)
        assert response.status_code == 500
        assert "malformed" in response.text

    def test_an_xxe_attempt_is_refused(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        payload = '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        response = client.post("/sdk", content=payload, headers=HEADERS)
        assert response.status_code == 500
        assert "DOCTYPE" in response.text

    def test_oversized_bodies_are_rejected_before_parsing(self, tiny_corpus, monkeypatch):
        import phantom_api.mock.engine as engine_module

        monkeypatch.setattr(engine_module, "MAX_REQUEST_BODY_BYTES", 64)
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        response = client.post("/sdk", content="x" * 500, headers=HEADERS)
        assert response.status_code == 413

    def test_an_unmatched_operation_reports_clearly(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        body = SOAP_ENVELOPE.format(body='<Nope xmlns="urn:demo"/>')
        response = client.post("/sdk", content=body, headers=HEADERS)
        assert response.status_code == 500
        assert "no recorded exchange" in response.text

    def test_no_responder_configured_is_reported(self):
        config = MockConfig(
            name="t",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[
                ResponderRule(
                    match=MatchRule(operation=["OnlyThis"]),
                    responder="template",
                    operations={"OnlyThis": {"responses": [{"body": "x"}]}},
                )
            ],
        )
        client = TestClient(create_mock_app(config))
        body = SOAP_ENVELOPE.format(body='<Other xmlns="urn:demo"/>')
        response = client.post("/sdk", content=body, headers=HEADERS)
        assert response.status_code == 500
        assert "no responder configured" in response.text

    def test_dropped_connections_are_visible_as_a_status(self, tiny_corpus):
        config = corpus_config(tiny_corpus)
        config.chaos = ChaosConfig(drop_rate=1.0)
        client = TestClient(create_mock_app(config))
        body = SOAP_ENVELOPE.format(body='<Ping xmlns="urn:demo"/>')
        assert client.post("/sdk", content=body, headers=HEADERS).status_code == 444


class TestResponderComposition:
    def test_a_corpus_miss_falls_through_to_a_template(self, tiny_corpus):
        """This composition is the point: recorded first, synthesised after."""
        config = MockConfig(
            name="t",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[
                ResponderRule(
                    match={},
                    responder="corpus",
                    corpus=tiny_corpus,  # type: ignore[arg-type]
                    on_miss="synthesize",
                ),
                ResponderRule(
                    match={},
                    responder="template",  # type: ignore[arg-type]
                    operations={"*": {"responses": [{"body": "<made-up/>"}]}},
                ),
            ],
            base_dir=tiny_corpus.parent,
        )
        client = TestClient(create_mock_app(config))

        recorded = client.post(
            "/sdk",
            content=(tiny_corpus / "soap" / "001-Ping.req.xml").read_text(),
            headers=HEADERS,
        )
        assert b"pong" in recorded.content

        synthesised = client.post(
            "/sdk",
            content=SOAP_ENVELOPE.format(body='<Unrecorded xmlns="urn:demo"/>'),
            headers=HEADERS,
        )
        assert b"<made-up/>" in synthesised.content

    def test_corpus_then_forward_records_the_gap(self, tiny_corpus, tmp_path):
        config = MockConfig(
            name="t",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[
                ResponderRule(
                    match={},
                    responder="corpus",
                    corpus=tiny_corpus,  # type: ignore[arg-type]
                    on_miss="forward",
                ),
                ResponderRule(
                    match={},
                    responder="forward",  # type: ignore[arg-type]
                    target="https://upstream.example",
                    record_to=tmp_path / "grown",
                ),
            ],
            base_dir=tiny_corpus.parent,
        )
        app = create_mock_app(config)
        forward = app.state.engine._responders.bindings[-1].responder
        forward._client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<FromReal/>"))
        )
        client = TestClient(app)
        response = client.post(
            "/sdk",
            content=SOAP_ENVELOPE.format(body='<Unrecorded xmlns="urn:demo"/>'),
            headers=HEADERS,
        )
        assert b"<FromReal/>" in response.content
        grown = json.loads((tmp_path / "grown" / "manifest.json").read_text())
        assert grown["exchanges"][0]["label"] == "Unrecorded"

    def test_operation_scoped_rules_take_precedence(self, tiny_corpus):
        config = MockConfig(
            name="t",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[
                ResponderRule(
                    match=MatchRule(operation=["Ping"]),
                    responder="template",
                    operations={"Ping": {"responses": [{"body": "<override/>"}]}},
                ),
                ResponderRule(match={}, responder="corpus", corpus=tiny_corpus),  # type: ignore[arg-type]
            ],
            base_dir=tiny_corpus.parent,
        )
        client = TestClient(create_mock_app(config))
        response = client.post(
            "/sdk",
            content=(tiny_corpus / "soap" / "001-Ping.req.xml").read_text(),
            headers=HEADERS,
        )
        assert b"<override/>" in response.content


class TestEngineConstruction:
    def test_a_corpus_responder_needs_a_corpus_path(self):
        config = MockConfig(responders=[ResponderRule(responder="corpus")])
        with pytest.raises(ValueError, match="requires a 'corpus' path"):
            MockEngine(config)

    def test_a_model_responder_needs_a_pack(self):
        config = MockConfig(responders=[ResponderRule(responder="model")])
        with pytest.raises(ValueError, match=r"mock\.model\.pack"):
            MockEngine(config)

    def test_a_missing_scenario_is_reported(self, tmp_path):
        config = MockConfig(
            model=ModelConfig(pack="vcenter", scenario=tmp_path / "absent.yaml"),
            responders=[ResponderRule(responder="model")],
            base_dir=tmp_path,
        )
        with pytest.raises(FileNotFoundError, match="scenario not found"):
            MockEngine(config)

    def test_an_unknown_protocol_pack_is_reported(self):
        config = MockConfig(protocols=[ProtocolConfig(pack="telepathy")])
        with pytest.raises(KeyError, match="unknown protocol pack"):
            MockEngine(config)

    def test_describe_lists_every_responder(self, tiny_corpus):
        engine = MockEngine(corpus_config(tiny_corpus))
        assert engine.describe() == [f"corpus({tiny_corpus.name}, 2 exchanges, on_miss=fault)"]


class TestDomainPackRegistry:
    def test_the_bundled_pack_is_discoverable(self):
        assert "vcenter" in available()
        assert get_pack("vcenter").name == "vcenter"

    def test_an_unknown_pack_names_what_is_available(self):
        with pytest.raises(KeyError, match="unknown domain pack"):
            get_pack("nothing-like-this")


class TestOpenApiWithSpec:
    def test_a_spec_collapses_concrete_paths_onto_operations(self, tmp_path):
        spec = tmp_path / "api.json"
        spec.write_text(
            json.dumps(
                {
                    "openapi": "3.0.0",
                    "info": {"title": "t", "version": "1"},
                    "paths": {
                        "/pets/{petId}": {"get": {"responses": {"200": {"description": "ok"}}}}
                    },
                }
            )
        )
        pack = OpenApiPack()
        pack.load_spec(spec)
        op = pack.decode(RawRequest("GET", "/pets/42", "", {}, b""))
        assert op.name == "GET /pets/{petId}"
        assert op.params["path"] == {"petId": "42"}

    def test_a_json_body_is_decoded(self):
        request = RawRequest(
            "POST", "/pets", "", {"content-type": "application/json"}, b'{"name": "Rex"}'
        )
        assert OpenApiPack().decode(request).params["body"] == {"name": "Rex"}

    def test_a_non_json_body_is_kept_as_text(self):
        request = RawRequest("POST", "/pets", "", {}, b"not json")
        assert OpenApiPack().decode(request).params["body"] == "not json"

    def test_results_are_serialised_as_json(self):
        pack = OpenApiPack()
        op = pack.decode(RawRequest("GET", "/pets", "", {}, b""))
        assert json.loads(pack.encode({"a": 1}, op).body) == {"a": 1}


class TestControlPlaneWithoutAModel:
    def test_model_endpoints_report_that_there_is_no_model(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        assert client.get("/__phantom/inventory").status_code == 409
        assert client.post("/__phantom/events", json={"event": "X"}).status_code == 409

    def test_status_still_works(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        payload = client.get("/__phantom/status").json()
        assert payload["model"] is None
        assert payload["protocols"] == ["soap"]

    def test_reset_clears_the_log(self, tiny_corpus):
        client = TestClient(create_mock_app(corpus_config(tiny_corpus)))
        client.post(
            "/sdk",
            content=(tiny_corpus / "soap" / "001-Ping.req.xml").read_text(),
            headers=HEADERS,
        )
        assert client.get("/__phantom/log").json()["count"] == 1
        client.post("/__phantom/reset")
        assert client.get("/__phantom/log").json()["count"] == 0

    def test_the_control_plane_can_be_turned_off(self, tiny_corpus):
        config = corpus_config(tiny_corpus)
        config.control_plane.enabled = False
        client = TestClient(create_mock_app(config))
        # With no control plane the path falls through to the mock itself.
        assert client.get("/__phantom/status").status_code != 200


class TestModelControlPlaneEdges:
    @pytest.fixture
    def client(self, tmp_path, vcenter_scenario):
        import yaml

        from phantom_api.mock.config import SessionRule

        scenario = tmp_path / "s.yaml"
        scenario.write_text(yaml.safe_dump(vcenter_scenario))
        config = MockConfig(
            name="t",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[
                ProtocolConfig(
                    pack="soap",
                    paths=["/**"],
                    session={"/**": SessionRule(transport="cookie", name="sid")},
                )
            ],
            model=ModelConfig(pack="vcenter", scenario=scenario, seed=3),
            responders=[ResponderRule(match={}, responder="model")],  # type: ignore[arg-type]
            base_dir=tmp_path,
        )
        return TestClient(create_mock_app(config))

    def test_mutating_an_unknown_entity_is_a_404(self, client):
        response = client.post(
            "/__phantom/mutate", json={"action": "set", "entity": "nope", "path": "a"}
        )
        assert response.status_code == 404

    def test_set_without_a_path_is_rejected(self, client):
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        response = client.post("/__phantom/mutate", json={"action": "set", "entity": vm})
        assert response.status_code == 400

    def test_remove_via_the_control_plane(self, client):
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        assert (
            client.post("/__phantom/mutate", json={"action": "remove", "entity": vm}).json()[
                "removed"
            ]
            == vm
        )

    def test_the_event_log_is_readable(self, client):
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        client.post("/__phantom/events", json={"event": "VmPoweredOffEvent", "target": vm})
        events = client.get("/__phantom/events").json()
        assert events["count"] == 1
        assert events["events"][0]["type"] == "VmPoweredOffEvent"

    def test_sessions_are_visible(self, client):
        client.post(
            "/sdk",
            content=SOAP_ENVELOPE.format(
                body='<RetrieveServiceContent xmlns="urn:vim25">'
                '<_this type="ServiceInstance">ServiceInstance</_this>'
                "</RetrieveServiceContent>"
            ),
            headers=HEADERS,
        )
        assert client.get("/__phantom/sessions").json()["sessions"]


class TestResponderProtocolCompliance:
    """Every responder must defer, not crash, when it cannot answer."""

    def test_model_defers_on_an_unknown_operation(self, tmp_path, vcenter_scenario):
        import yaml

        from phantom_api.mock.packs.vcenter.pack import VCenterPack
        from phantom_api.mock.responders.model import ModelResponder

        scenario = tmp_path / "s.yaml"
        scenario.write_text(yaml.safe_dump(vcenter_scenario))
        engine = MockEngine(
            MockConfig(
                model=ModelConfig(pack="vcenter", scenario=scenario),
                responders=[ResponderRule(responder="model")],
                base_dir=tmp_path,
            )
        )
        responder: ModelResponder = engine._responders.bindings[0].responder
        with pytest.raises(NotHandled):
            responder.respond(Operation("NoSuchOperation", "soap"))
        assert VCenterPack().name == "vcenter"

    def test_mock_error_carries_its_kind_and_status(self):
        error = MockError("x", kind="not-found", status=404, detail_type="NotFound")
        assert (error.kind, error.status, error.detail_type) == ("not-found", 404, "NotFound")


class TestShippedVCenterMock:
    """The configuration in the repository must actually work."""

    pytestmark = pytest.mark.skipif(
        not (VCENTER_MOCK / "corpus" / "manifest.json").exists(),
        reason="examples/vcenter corpus is not present",
    )

    def test_it_starts_and_answers(self):
        from phantom_api.mock.config import load_config

        client = TestClient(create_mock_app(load_config(VCENTER_MOCK / "phantom.yaml")))
        response = client.post(
            "/sdk",
            content=(
                VCENTER_MOCK / "corpus" / "soap" / "001-RetrieveServiceContent.req.xml"
            ).read_text(),
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert "<apiVersion>" in response.text

    def test_rest_paths_route_to_the_corpus(self):
        from phantom_api.mock.config import load_config

        client = TestClient(create_mock_app(load_config(VCENTER_MOCK / "phantom.yaml")))
        response = client.get("/rest/vcenter/vm")
        assert response.status_code == 200
        assert "value" in response.text

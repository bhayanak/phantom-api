"""Tests for responder dispatch, corpus misses, and the derive --model command."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from phantom_api.cli import app
from phantom_api.mock.responders.corpus import CorpusResponder
from phantom_api.mock.responders.template import TemplateResponder
from phantom_api.mock.types import MockError, NotHandled, Operation, RawRequest

runner = CliRunner()


def rest_request(path: str, query: str = "") -> RawRequest:
    return RawRequest(method="GET", path=path, query=query, headers={}, body=b"")


def rest_op(name: str, path: str, query: str = "") -> Operation:
    return Operation(
        name=name,
        protocol="openapi",
        params={},
        headers={},
        session_id=None,
        request=rest_request(path, query),
        context={},
    )


def build_corpus(root, entries):
    corpus = root / "corpus"
    (corpus / "rest").mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (path, body) in enumerate(entries, start=1):
        stem = f"{index:03d}-op"
        (corpus / "rest" / f"{stem}.req.json").write_text("")
        (corpus / "rest" / f"{stem}.resp.json").write_text(body)
        manifest.append(
            {
                "stem": stem,
                "label": path,
                "transport": "rest",
                "status": 200,
                "method": "GET",
                "endpoint_path": path,
                "request": f"rest/{stem}.req.json",
                "response": f"rest/{stem}.resp.json",
            }
        )
    (corpus / "manifest.json").write_text(json.dumps({"exchanges": manifest}))
    return corpus


# -- corpus misses -----------------------------------------------------------


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(
        tmp_path,
        [("/api/things", '{"things":[]}'), ("/api/things?page=2", '{"things":[2]}')],
    )


def test_a_query_string_selects_the_right_recording(corpus):
    responder = CorpusResponder(corpus)

    first = responder.respond(rest_op("GET /api/things", "/api/things"))
    second = responder.respond(rest_op("GET /api/things", "/api/things", "page=2"))

    assert b'"things":[]' in first.body
    assert b"2" in second.body


def test_an_unrecorded_query_falls_back_to_the_path(corpus):
    """Better a close answer than none, when the path alone identifies it."""
    response = responder_for(corpus).respond(rest_op("GET /api/things", "/api/things", "page=99"))
    assert response.status == 200


def responder_for(corpus, **kwargs):
    return CorpusResponder(corpus, **kwargs)


def test_on_miss_fault_raises(corpus):
    with pytest.raises(MockError):
        responder_for(corpus).respond(rest_op("GET /api/absent", "/api/absent"))


def test_on_miss_not_found_is_a_404(corpus):
    with pytest.raises(MockError) as caught:
        responder_for(corpus, on_miss="not-found").respond(
            rest_op("GET /api/absent", "/api/absent")
        )
    assert caught.value.status == 404


def test_on_miss_synthesize_defers_to_the_next_rule(corpus):
    with pytest.raises(NotHandled):
        responder_for(corpus, on_miss="synthesize").respond(
            rest_op("GET /api/absent", "/api/absent")
        )


def test_on_miss_forward_defers_to_the_next_rule(corpus):
    with pytest.raises(NotHandled):
        responder_for(corpus, on_miss="forward").respond(rest_op("GET /api/absent", "/api/absent"))


def test_duplicate_recordings_are_cycled(tmp_path):
    """Repeated polling should not see the same answer for ever."""
    corpus = build_corpus(tmp_path, [("/api/poll", '{"n":1}'), ("/api/poll", '{"n":2}')])
    responder = CorpusResponder(corpus)

    seen = [responder.respond(rest_op("GET /api/poll", "/api/poll")).body for _ in range(4)]
    assert seen[0] != seen[1], "a second call must not repeat the first"
    assert seen[0] == seen[2], "and the cycle must come round"


def test_a_corpus_without_a_manifest_is_reported(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(FileNotFoundError):
        CorpusResponder(tmp_path / "nope")


def test_describe_reports_what_is_loaded(corpus):
    assert "2 exchanges" in CorpusResponder(corpus).describe()


# -- template dispatch -------------------------------------------------------


def soap_op(name: str, params: dict | None = None) -> Operation:
    return Operation(
        name=name,
        protocol="soap",
        params=params or {},
        headers={},
        session_id="s1",
        request=rest_request("/sdk"),
        context={},
    )


def rendered(result) -> object:
    """Templates return a protocol-neutral wrapper; the tests want the payload."""
    return getattr(result, "xml", result)


def test_first_is_the_default_strategy():
    responder = TemplateResponder({"Get": {"responses": [{"body": "one"}, {"body": "two"}]}})
    assert rendered(responder.respond(soap_op("Get"))) == "one"


def test_sequence_advances_one_response_per_call():
    responder = TemplateResponder(
        {"Poll": {"dispatch": "sequence", "responses": [{"body": "a"}, {"body": "b"}]}}
    )
    assert [rendered(responder.respond(soap_op("Poll"))) for _ in range(3)] == ["a", "b", "a"]


def test_random_is_deterministic_for_a_seed():
    spec = {"Pick": {"dispatch": "random", "responses": [{"body": "a"}, {"body": "b"}]}}
    first = rendered(TemplateResponder(spec, seed=7).respond(soap_op("Pick")))
    again = rendered(TemplateResponder(spec, seed=7).respond(soap_op("Pick")))
    assert first == again


def test_match_selects_on_a_condition():
    responder = TemplateResponder(
        {
            "Get": {
                "dispatch": "match",
                "responses": [
                    {"when": "params.id == '1'", "body": "one"},
                    {"body": "fallback"},
                ],
            }
        }
    )
    assert rendered(responder.respond(soap_op("Get", {"id": "1"}))) == "one"
    assert rendered(responder.respond(soap_op("Get", {"id": "9"}))) == "fallback"


def test_a_wildcard_operation_catches_anything():
    responder = TemplateResponder({"*": {"responses": [{"body": "any"}]}})
    assert rendered(responder.respond(soap_op("Whatever"))) == "any"


def test_a_bare_string_is_shorthand_for_one_response():
    assert rendered(TemplateResponder({"Get": "hello"}).respond(soap_op("Get"))) == "hello"


def test_an_operation_with_no_template_defers():
    with pytest.raises(NotHandled):
        TemplateResponder({"Other": "x"}).respond(soap_op("Get"))


def test_an_operation_with_no_responses_defers():
    with pytest.raises(NotHandled):
        TemplateResponder({"Get": {"responses": []}}).respond(soap_op("Get"))


def test_nothing_matching_defers():
    responder = TemplateResponder(
        {"Get": {"dispatch": "match", "responses": [{"when": "params.id == 'x'", "body": "a"}]}}
    )
    with pytest.raises(NotHandled):
        responder.respond(soap_op("Get", {"id": "y"}))


def test_a_broken_condition_is_reported_as_a_config_error():
    responder = TemplateResponder(
        {"Get": {"dispatch": "match", "responses": [{"when": "1 +", "body": "a"}]}}
    )
    with pytest.raises(MockError, match="when"):
        responder.respond(soap_op("Get"))


def test_a_fault_response_raises_in_the_protocol_s_terms():
    responder = TemplateResponder(
        {"Get": {"responses": [{"fault": {"kind": "not-found", "message": "gone"}}]}}
    )
    with pytest.raises(MockError) as caught:
        responder.respond(soap_op("Get"))
    assert caught.value.kind == "not-found"


def test_structured_bodies_are_rendered_throughout():
    """Every string in a nested body is a template, at any depth."""
    responder = TemplateResponder(
        {
            "GET /things": {
                "responses": [{"body": {"id": "{{ params.id }}", "tags": ["{{ params.id }}"]}}]
            }
        }
    )
    op = rest_op("GET /things", "/things")
    op.params["id"] = "42"

    assert responder.respond(op) == {"id": "42", "tags": ["42"]}


def test_describe_reports_the_operation_count():
    assert "2 operations" in TemplateResponder({"a": "x", "b": "y"}).describe()


# -- derive --model on the command line --------------------------------------


def test_derive_model_writes_an_inventory_and_a_scenario(tmp_path):
    corpus = build_corpus(
        tmp_path,
        [
            ("/api/servers", '{"servers":[{"id":1,"name":"a","account":{"id":2,"name":"x"}}]}'),
            ("/api/accounts", '{"accounts":[{"id":2,"name":"x","active":true}]}'),
        ],
    )
    out = tmp_path / "model"

    result = runner.invoke(
        app, ["mock", "derive", str(corpus), "--name", "demo", "--model", str(out)]
    )

    assert result.exit_code == 0, result.output
    inventory = json.loads((out / "topology" / "inventory.json").read_text())
    assert inventory["object_count"] == 2
    assert (out / "scenario" / "demo-scenario.yaml").exists()
    assert "Object graph" in result.output


def test_derive_model_says_so_when_there_is_nothing_to_model(tmp_path):
    corpus = build_corpus(tmp_path, [("/api/health", '{"status":"ok"}')])
    out = tmp_path / "model"

    result = runner.invoke(app, ["mock", "derive", str(corpus), "--model", str(out)])

    assert result.exit_code == 0
    assert "No objects with identity" in result.output
    assert not (out / "topology").exists()


def test_a_manifest_entry_whose_response_is_gone_is_skipped(tmp_path):
    corpus = build_corpus(tmp_path, [("/api/a", "{}"), ("/api/b", "{}")])
    (corpus / "rest" / "001-op.resp.json").unlink()

    assert len(CorpusResponder(corpus).exchanges) == 1


def test_a_soap_operation_is_read_from_the_envelope_not_the_label(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "soap" / "001.req.xml").write_text(
        '<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">'
        "<Body><RealName/></Body></Envelope>"
    )
    (corpus / "soap" / "001.resp.xml").write_text("<ok/>")
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "exchanges": [
                    {
                        "stem": "001",
                        "label": "MisleadingLabel",
                        "transport": "soap",
                        "status": 200,
                        "endpoint_path": "/sdk",
                        "request": "soap/001.req.xml",
                        "response": "soap/001.resp.xml",
                    }
                ]
            }
        )
    )

    assert CorpusResponder(corpus).exchanges[0].operation == "RealName"


def test_a_malformed_soap_request_falls_back_to_its_label(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "soap" / "001.req.xml").write_text("<broken")
    (corpus / "soap" / "001.resp.xml").write_text("<ok/>")
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "exchanges": [
                    {
                        "stem": "001",
                        "label": "Fallback",
                        "transport": "soap",
                        "status": 200,
                        "endpoint_path": "/sdk",
                        "request": "soap/001.req.xml",
                        "response": "soap/001.resp.xml",
                    }
                ]
            }
        )
    )

    assert CorpusResponder(corpus).exchanges[0].operation == "Fallback"


def test_a_configuration_can_be_loaded_and_served_from_a_file(tmp_path):
    """The path the CLI takes: a file on disk becomes a running application."""
    from phantom_api.mock.engine import load_and_create

    corpus = build_corpus(tmp_path, [("/api/a", '{"ok":true}')])
    config = tmp_path / "phantom.yaml"
    config.write_text(
        "mock:\n"
        "  name: demo\n"
        '  listen: { host: 127.0.0.1, port: 8443, tls: "off" }\n'
        "  protocols:\n"
        '    - pack: openapi\n      paths: ["/**"]\n'
        "  responders:\n"
        '    - match: "*"\n'
        "      responder: corpus\n"
        f"      corpus: {corpus}\n"
    )

    app = load_and_create(config)

    from fastapi.testclient import TestClient

    assert TestClient(app).get("/api/a").status_code == 200


def test_a_spec_lets_the_rest_pack_collapse_concrete_paths(tmp_path):
    """With a spec, `/pets/42` is recognised as `GET /pets/{petId}`."""
    import json as _json

    from phantom_api.mock.config import (
        ListenConfig,
        MockConfig,
        ProtocolConfig,
        ResponderRule,
    )
    from phantom_api.mock.engine import MockEngine

    spec = tmp_path / "spec.json"
    spec.write_text(
        _json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "t", "version": "1"},
                "paths": {"/pets/{petId}": {"get": {"responses": {"200": {"description": "ok"}}}}},
            }
        )
    )

    engine = MockEngine(
        MockConfig(
            name="t",
            listen=ListenConfig(),
            protocols=[ProtocolConfig(pack="openapi", paths=["/**"], spec=spec)],
            responders=[
                ResponderRule(
                    match={},  # type: ignore[arg-type]
                    responder="template",
                    operations={"GET /pets/{petId}": "matched"},
                )
            ],
            base_dir=tmp_path,
        )
    )

    response = engine.handle(
        RawRequest(method="GET", path="/pets/42", query="", headers={}, body=b"")
    )
    assert b"matched" in response.body

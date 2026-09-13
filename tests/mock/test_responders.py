"""Responders, sessions, cursors, chaos and the expression sandbox."""

from __future__ import annotations

import pytest

from phantom_api.mock.config import ChaosConfig
from phantom_api.mock.expressions import ExpressionError, evaluate, render
from phantom_api.mock.pipeline import ConnectionDropped, Pipeline
from phantom_api.mock.record.fingerprint import fingerprint, normalise
from phantom_api.mock.responders.corpus import CorpusResponder
from phantom_api.mock.responders.template import TemplateResponder
from phantom_api.mock.session import CursorLimitReached, CursorStore, SessionStore
from phantom_api.mock.types import MockError, NotHandled, Operation

from .conftest import soap_request


class TestExpressionSandbox:
    """Configuration is data; it must never become code."""

    def test_arithmetic_and_comparison(self):
        assert evaluate("1 + 2 * 3", {}) == 7
        assert evaluate("a > 2 and a < 10", {"a": 5}) is True

    def test_dict_access_by_attribute_or_subscript(self):
        ctx = {"params": {"id": "42", "nested": {"x": 1}}}
        assert evaluate("params.id", ctx) == "42"
        assert evaluate("params['nested']['x']", ctx) == 1

    def test_missing_key_is_none_not_an_error(self):
        assert evaluate("params.nope", {"params": {}}) is None

    def test_helpers_are_available(self):
        assert evaluate("upper('ab')", {}) == "AB"
        assert evaluate("default(x, 'fallback')", {"x": ""}) == "fallback"

    @pytest.mark.parametrize(
        "source",
        [
            "__import__('os').system('id')",
            "().__class__.__bases__",
            "open('/etc/passwd')",
            "[x for x in range(3)]",
            "lambda: 1",
        ],
    )
    def test_dangerous_constructs_are_refused(self, source):
        with pytest.raises(ExpressionError):
            evaluate(source, {})

    def test_unknown_name_is_refused(self):
        with pytest.raises(ExpressionError, match="unknown name"):
            evaluate("secrets", {})

    def test_render_substitutes_every_expression(self):
        assert render("<a>{{ x }}</a><b>{{ x + 1 }}</b>", {"x": 1}) == "<a>1</a><b>2</b>"

    def test_render_drops_none(self):
        assert render("[{{ missing }}]", {"missing": None}) == "[]"


class TestFingerprint:
    def test_volatile_values_do_not_affect_the_digest(self):
        a = fingerprint("Op", "<r><token>aaaa</token><v>1</v></r>")
        b = fingerprint("Op", "<r><token>bbbb</token><v>1</v></r>")
        assert a.digest == b.digest

    def test_different_parameters_give_different_digests(self):
        a = fingerprint("Op", "<r><v>1</v></r>")
        b = fingerprint("Op", "<r><v>2</v></r>")
        assert a.digest != b.digest

    def test_structural_digest_ignores_values(self):
        a = fingerprint("Op", "<r><v>1</v></r>")
        b = fingerprint("Op", "<r><v>2</v></r>")
        assert a.parameter_digest == b.parameter_digest

    def test_stripped_values_are_reported(self):
        _, stripped = normalise("<r><token>abc</token></r>")
        assert stripped["token"] == ["<token>abc</token>"]

    def test_keys_run_specific_to_general(self):
        keys = fingerprint("Op", "<r/>").keys()
        assert keys[-1] == "Op"
        assert len(keys) == 3


class TestCorpusResponder:
    def test_replays_a_recorded_exchange(self, tiny_corpus):
        responder = CorpusResponder(tiny_corpus)
        op = Operation(
            name="Ping", protocol="soap", request=soap_request('<Ping xmlns="urn:demo"/>')
        )
        response = responder.respond(op)
        assert response.status == 200
        assert b"pong" in response.body

    def test_miss_faults_by_default(self, tiny_corpus):
        responder = CorpusResponder(tiny_corpus)
        op = Operation(name="Unknown", protocol="soap", request=soap_request("<Unknown/>"))
        with pytest.raises(MockError, match="no recorded exchange"):
            responder.respond(op)

    def test_miss_can_defer_to_the_next_responder(self, tiny_corpus):
        responder = CorpusResponder(tiny_corpus, on_miss="synthesize")
        op = Operation(name="Unknown", protocol="soap", request=soap_request("<Unknown/>"))
        with pytest.raises(NotHandled):
            responder.respond(op)

    def test_rest_exchange_matches_on_path(self, tiny_corpus):
        from phantom_api.mock.types import RawRequest

        responder = CorpusResponder(tiny_corpus)
        op = Operation(
            name="GET /items",
            protocol="openapi",
            request=RawRequest("GET", "/items", "", {}, b""),
        )
        assert b'"value"' in responder.respond(op).body

    def test_missing_manifest_is_reported_clearly(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=r"manifest\.json"):
            CorpusResponder(tmp_path)


class TestTemplateResponder:
    def test_first_dispatch_returns_the_first_response(self):
        responder = TemplateResponder({"Op": {"responses": [{"body": "<a/>"}, {"body": "<b/>"}]}})
        assert responder.respond(Operation(name="Op", protocol="soap")).xml == "<a/>"

    def test_sequence_dispatch_cycles(self):
        responder = TemplateResponder(
            {"Op": {"dispatch": "sequence", "responses": [{"body": "1"}, {"body": "2"}]}}
        )
        op = Operation(name="Op", protocol="soap")
        assert [responder.respond(op).xml for _ in range(3)] == ["1", "2", "1"]

    def test_match_dispatch_picks_by_predicate(self):
        responder = TemplateResponder(
            {
                "Op": {
                    "dispatch": "match",
                    "responses": [
                        {"when": "params.id == '0'", "body": "zero"},
                        {"body": "other"},
                    ],
                }
            }
        )
        assert responder.respond(Operation("Op", "soap", {"id": "0"})).xml == "zero"
        assert responder.respond(Operation("Op", "soap", {"id": "9"})).xml == "other"

    def test_a_response_can_be_a_fault(self):
        responder = TemplateResponder(
            {"Op": {"responses": [{"fault": {"kind": "not-found", "message": "gone"}}]}}
        )
        with pytest.raises(MockError, match="gone"):
            responder.respond(Operation("Op", "soap"))

    def test_templates_see_request_parameters(self):
        responder = TemplateResponder({"Op": {"responses": [{"body": "<id>{{ params.id }}</id>"}]}})
        assert responder.respond(Operation("Op", "soap", {"id": "7"})).xml == "<id>7</id>"

    def test_json_responses_are_rendered_recursively(self):
        responder = TemplateResponder({"Op": {"responses": [{"json": {"id": "{{ params.id }}"}}]}})
        assert responder.respond(Operation("Op", "jsonrpc", {"id": "3"})) == {"id": "3"}

    def test_wildcard_operation_is_a_fallback(self):
        responder = TemplateResponder({"*": {"responses": [{"body": "any"}]}})
        assert responder.respond(Operation("Whatever", "soap")).xml == "any"

    def test_unknown_operation_defers(self):
        with pytest.raises(NotHandled):
            TemplateResponder({}).respond(Operation("Op", "soap"))


class TestSessions:
    def test_a_session_is_not_authenticated_until_told(self):
        store = SessionStore()
        assert store.create().authenticated is False

    def test_lookup_by_id(self):
        store = SessionStore()
        session = store.create(authenticated=True)
        assert store.get(session.id) is session

    def test_idle_sessions_expire(self):
        store = SessionStore(idle_timeout=-1)
        session = store.create()
        assert store.get(session.id) is None

    def test_the_store_is_bounded(self):
        store = SessionStore(max_sessions=3)
        for _ in range(10):
            store.create()
        assert len(store) <= 3

    def test_unknown_id_is_none(self):
        assert SessionStore().get("nope") is None


class TestCursors:
    def test_take_advances_and_reports_exhaustion(self):
        store = CursorStore()
        cursor = store.create("x", [1, 2, 3])
        assert cursor.take(2) == [1, 2]
        assert cursor.exhausted is False
        assert cursor.take(2) == [3]
        assert cursor.exhausted is True

    def test_reset_rewinds(self):
        cursor = CursorStore().create("x", [1, 2])
        cursor.take(2)
        cursor.reset()
        assert cursor.take(1) == [1]

    def test_least_recently_used_is_evicted(self):
        store = CursorStore(max_cursors=2)
        first = store.create("x", [1])
        store.create("x", [2])
        store.create("x", [3])
        assert store.get(first.token) is None

    def test_strict_limit_reproduces_the_real_server_fault(self):
        """Clients leak collectors; real servers cap them. Reproducing that finds bugs."""
        store = CursorStore(strict_limit=2)
        store.create("events", [], session_id="s")
        store.create("events", [], session_id="s")
        with pytest.raises(CursorLimitReached):
            store.create("events", [], session_id="s")


class TestPipeline:
    def test_faults_are_injected_at_the_configured_rate(self):
        pipeline = Pipeline(ChaosConfig(fault_rate=1.0), seed=1)
        with pytest.raises(MockError, match="chaos"):
            pipeline.before(Operation("Op", "soap"))

    def test_connections_can_be_dropped(self):
        pipeline = Pipeline(ChaosConfig(drop_rate=1.0), seed=1)
        with pytest.raises(ConnectionDropped):
            pipeline.before(Operation("Op", "soap"))

    def test_no_chaos_by_default(self):
        Pipeline(ChaosConfig()).before(Operation("Op", "soap"))

    def test_verification_counts_operations(self):
        pipeline = Pipeline(ChaosConfig())
        op = Operation("Op", "soap")
        for _ in range(3):
            pipeline.record(op, "model", 200, 0.0, 10)
        assert pipeline.verify("Op", at_least=3) is True
        assert pipeline.verify("Op", at_most=2) is False
        assert pipeline.verify("Other") is False

    def test_probabilities_are_validated(self):
        with pytest.raises(ValueError, match="between"):
            ChaosConfig(fault_rate=2.0)

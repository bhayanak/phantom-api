"""The engine: transport, decode, route, respond, encode.

This is the only module that sees the whole picture, and it is deliberately
short. Everything interesting happens in a pack or a responder; the engine just
holds them in the right order.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response

from phantom_api.constants import MAX_REQUEST_BODY_BYTES
from phantom_api.mock.config import MockConfig, ProtocolConfig, ResponderRule
from phantom_api.mock.packs.base import PackContext
from phantom_api.mock.pipeline import ConnectionDropped, Pipeline, Stats
from phantom_api.mock.protocols import detect, get_pack
from phantom_api.mock.responders.corpus import CorpusResponder
from phantom_api.mock.responders.forward import ForwardResponder
from phantom_api.mock.responders.model import ModelResponder
from phantom_api.mock.responders.template import TemplateResponder
from phantom_api.mock.router import (
    ProtocolBinding,
    ProtocolRouter,
    ResponderBinding,
    ResponderRouter,
)
from phantom_api.mock.session import CursorStore, SessionStore
from phantom_api.mock.types import MockError, NotHandled, Operation, RawRequest, RawResponse


class MockEngine:
    """Holds every moving part and serves one request at a time."""

    def __init__(self, config: MockConfig) -> None:
        self.config = config
        self.pipeline = Pipeline(config.chaos, seed=config.model.seed)
        self.stats = Stats()
        self.sessions = SessionStore()
        self.cursors = CursorStore()
        self.context: PackContext | None = None
        self.pack_name: str | None = None
        self._protocols = ProtocolRouter(self._build_protocols(config.protocols))
        self._responders = ResponderRouter(self._build_responders(config.responders))

    # -- construction ----------------------------------------------------

    def _build_protocols(self, configs: list[ProtocolConfig]) -> list[ProtocolBinding]:
        bindings: list[ProtocolBinding] = []
        for item in configs:
            if item.pack == "auto":
                bindings.append(ProtocolBinding(config=item, pack=None))
                continue
            pack = get_pack(item.pack)
            spec = self.config.resolve(item.spec)
            if spec is not None and hasattr(pack, "load_spec"):
                pack.load_spec(spec)
            bindings.append(ProtocolBinding(config=item, pack=pack))
        return bindings

    def _build_responders(self, rules: list[ResponderRule]) -> list[ResponderBinding]:
        bindings: list[ResponderBinding] = []
        for rule in rules:
            bindings.append(ResponderBinding(rule=rule, responder=self._make_responder(rule)))
        return bindings

    def _make_responder(self, rule: ResponderRule) -> Any:
        if rule.responder == "corpus":
            corpus = self.config.resolve(rule.corpus)
            if corpus is None:
                raise ValueError("corpus responder requires a 'corpus' path")
            return CorpusResponder(corpus, on_miss=rule.on_miss)
        if rule.responder == "template":
            return TemplateResponder(rule.operations, seed=self.config.model.seed)
        if rule.responder == "forward":
            return ForwardResponder(
                rule.target or "", record_to=self.config.resolve(rule.record_to)
            )
        if rule.responder == "model":
            return ModelResponder(*self._build_model())
        raise ValueError(f"unknown responder {rule.responder!r}")

    def _build_model(self) -> tuple[PackContext, dict[str, Any]]:
        from phantom_api.mock.model.evolution import EvolutionEngine
        from phantom_api.mock.packs.base import get_pack as get_domain_pack

        if self.context is not None and self._projections is not None:
            return self.context, self._projections

        name = self.config.model.pack
        if not name:
            raise ValueError("a model responder needs mock.model.pack")
        pack = get_domain_pack(name)
        scenario = self._load_scenario()
        inventory = pack.build(scenario, self.config.model.seed)
        evolution = EvolutionEngine(
            inventory, mutations=pack.mutations(), seed=self.config.model.seed
        )
        evolution.load(scenario.get("change_engine") or {})

        scenario_path = self.config.resolve(self.config.model.scenario)
        data_dir = scenario_path.parent.parent if scenario_path else None
        self.context = PackContext(
            inventory=inventory,
            evolution=evolution,
            sessions=self.sessions,
            scenario=scenario,
            catalogs=pack.catalogs(data_dir),
            cursors=self.cursors,
            data_dir=data_dir,
        )
        self.pack_name = name
        self._projections = pack.projections()
        return self.context, self._projections

    _projections: dict[str, Any] | None = None

    def _load_scenario(self) -> dict[str, Any]:
        import yaml

        path = self.config.resolve(self.config.model.scenario)
        if path is None:
            return {}
        if not path.exists():
            raise FileNotFoundError(f"scenario not found: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # -- serving ---------------------------------------------------------

    def handle(self, request: RawRequest) -> RawResponse:
        started = time.perf_counter()
        binding = self._protocols.select(request)
        pack = binding.pack if binding else None
        if pack is None:
            pack = detect(request)
        if pack is None:
            return RawResponse.of("no protocol pack matched this request", 400)

        session_rule = binding.session_for(request.path) if binding else None
        op: Operation | None = None
        try:
            op = pack.decode(request, session_rule)
            self.pipeline.before(op)
            result, responder_name = self._dispatch(op)
            response = pack.encode(result, op)
        except ConnectionDropped:
            raise
        except MockError as error:
            response = pack.encode_error(error, op)
            responder_name = "fault"
        except NotHandled as miss:
            error = MockError(str(miss), kind="not-found", status=404)
            response = pack.encode_error(error, op)
            responder_name = "unhandled"

        self._attach_session(response, op)
        self.pipeline.record(
            op or Operation(name=request.path, protocol="unknown", request=request),
            responder_name,
            response.status,
            started,
            len(response.body),
        )
        return response

    def _dispatch(self, op: Operation) -> tuple[Any, str]:
        candidates = self._responders.candidates(op)
        if not candidates:
            raise MockError(f"no responder configured for {op.name}", kind="not-found", status=404)
        last: Exception | None = None
        for binding in candidates:
            try:
                return binding.responder.respond(op), binding.responder.name
            except NotHandled as miss:
                last = miss
                continue
        raise MockError(
            str(last) if last else f"no responder answered {op.name}",
            kind="not-found",
            status=404,
        )

    def _attach_session(self, response: RawResponse, op: Operation | None) -> None:
        """Issue a session cookie on first contact, the way stateful servers do."""
        if op is None or op.protocol != "soap":
            return
        binding = self._protocols.select(op.request) if op.request else None
        rule = binding.session_for(op.request.path) if binding and op.request else None
        if rule is None or rule.transport != "cookie":
            return
        if self.sessions.get(op.session_id) is not None:
            return
        session = self.sessions.create()
        response.headers["set-cookie"] = f'{rule.name}="{session.id}"; Path=/; HttpOnly; Secure'

    def close(self) -> None:
        for binding in self._responders.bindings:
            close = getattr(binding.responder, "close", None)
            if callable(close):
                close()

    def describe(self) -> list[str]:
        return [b.responder.describe() for b in self._responders.bindings]


def create_mock_app(config: MockConfig) -> FastAPI:
    """Build a FastAPI app that routes every path through the engine."""
    engine = MockEngine(config)
    app = FastAPI(title=config.name, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    if config.control_plane.enabled:
        from phantom_api.mock.control import register_control_plane

        register_control_plane(app, engine)

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover - lifecycle
        engine.close()

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def dispatch(request: Request, full_path: str) -> Response:
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            return Response(
                content=json.dumps({"error": "request body too large"}),
                status_code=413,
                media_type="application/json",
            )
        raw = RawRequest(
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
        try:
            result = engine.handle(raw)
        except ConnectionDropped:
            # Closing the socket is the point; an empty body with no status is
            # the closest a WSGI-shaped stack gets to it.
            return Response(status_code=444)
        return Response(
            content=result.body,
            status_code=result.status,
            headers=result.headers,
        )

    return app


def load_and_create(path: Path) -> FastAPI:
    from phantom_api.mock.config import load_config

    return create_mock_app(load_config(path))

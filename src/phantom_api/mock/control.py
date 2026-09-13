"""The control plane: inspect the mock, and make it misbehave on demand.

Answers the question that is painful against a real system -- *what did my
client actually ask for?* -- and lets a test drive the simulation instead of
waiting for it.

Bound to loopback by default. It can mutate state, so it must never share an
interface with the mocked service in a shared environment.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


class EventRequest(BaseModel):
    event: str = Field(..., description="Change type to apply, e.g. VmPoweredOffEvent.")
    target: str | None = Field(default=None, description="Entity id, or kind:name.")
    args: dict[str, Any] = Field(default_factory=dict)


class MutateRequest(BaseModel):
    action: str = Field(..., description="set | remove")
    entity: str
    path: str | None = None
    value: Any = None


class ChaosRequest(BaseModel):
    latency_ms: int | None = None
    latency_jitter_ms: int | None = None
    fault_rate: float | None = None
    fault_kind: str | None = None
    drop_rate: float | None = None


class VerifyRequest(BaseModel):
    operation: str
    at_least: int = 1
    at_most: int | None = None


def register_control_plane(app: FastAPI, engine: Any) -> None:
    prefix = engine.config.control_plane.prefix
    bind = engine.config.control_plane.bind

    def guard(request: Request) -> None:
        """Refuse control access from anywhere but the configured address."""
        if bind in ("0.0.0.0", "*"):
            return
        client = request.client.host if request.client else ""
        if client not in {bind, "127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="control plane is loopback-only")

    @app.get(f"{prefix}/status", include_in_schema=False)
    def status(request: Request) -> dict[str, Any]:
        guard(request)
        return {
            "name": engine.config.name,
            "uptime_seconds": engine.stats.uptime(),
            "requests": engine.pipeline.total,
            "responders": engine.describe(),
            "protocols": [p.config.pack for p in engine._protocols.bindings],
            "sessions": len(engine.sessions),
            "cursors": len(engine.cursors),
            "model": (
                {
                    "pack": engine.pack_name,
                    "entities": len(engine.context.inventory),
                    "by_kind": engine.context.inventory.kinds(),
                    "events": len(engine.context.evolution.events),
                }
                if engine.context
                else None
            ),
        }

    @app.get(f"{prefix}/log", include_in_schema=False)
    def log(request: Request, limit: int = 100) -> dict[str, Any]:
        guard(request)
        return {"count": engine.pipeline.total, "entries": engine.pipeline.entries(limit)}

    @app.post(f"{prefix}/verify", include_in_schema=False)
    def verify(request: Request, body: VerifyRequest) -> dict[str, Any]:
        guard(request)
        ok = engine.pipeline.verify(body.operation, at_least=body.at_least, at_most=body.at_most)
        return {
            "operation": body.operation,
            "seen": engine.pipeline.counts.get(body.operation, 0),
            "satisfied": ok,
        }

    @app.get(f"{prefix}/inventory", include_in_schema=False)
    def inventory(request: Request, kind: str | None = None) -> dict[str, Any]:
        guard(request)
        _require_model()
        if kind:
            entities = engine.context.inventory.of_kind(kind)
            return {
                "kind": kind,
                "count": len(entities),
                "entities": [{"id": e.id, "props": e.props, "edges": e.edges} for e in entities],
            }
        return engine.context.inventory.to_json()

    @app.post(f"{prefix}/events", include_in_schema=False)
    def inject(request: Request, body: EventRequest) -> dict[str, Any]:
        guard(request)
        _require_model()
        event = engine.context.evolution.apply(body.event, body.target, body.args)
        if event is None:
            raise HTTPException(status_code=400, detail="event could not be applied")
        return {
            "key": event.key,
            "type": event.type,
            "target": event.target_id,
            "detail": event.detail,
        }

    @app.get(f"{prefix}/events", include_in_schema=False)
    def events(request: Request, since: int = 0, limit: int = 100) -> dict[str, Any]:
        guard(request)
        _require_model()
        found = engine.context.evolution.since(since, limit)
        return {
            "count": len(found),
            "events": [
                {"key": e.key, "type": e.type, "target": e.target_id, "at": e.created_at}
                for e in found
            ],
        }

    @app.post(f"{prefix}/mutate", include_in_schema=False)
    def mutate(request: Request, body: MutateRequest) -> dict[str, Any]:
        guard(request)
        _require_model()
        inv = engine.context.inventory
        if body.action == "remove":
            removed = inv.remove(body.entity)
            return {"removed": removed.id if removed else None}
        entity = inv.get(body.entity)
        if entity is None:
            raise HTTPException(status_code=404, detail=f"no entity {body.entity}")
        if not body.path:
            raise HTTPException(status_code=400, detail="'path' is required for set")
        entity.set(body.path, body.value)
        return {"entity": entity.id, "path": body.path, "value": body.value}

    @app.post(f"{prefix}/chaos", include_in_schema=False)
    def chaos(request: Request, body: ChaosRequest) -> dict[str, Any]:
        guard(request)
        current = engine.pipeline.chaos
        for field_name, value in body.model_dump(exclude_none=True).items():
            setattr(current, field_name, value)
        return current.model_dump()

    @app.get(f"{prefix}/sessions", include_in_schema=False)
    def sessions(request: Request) -> dict[str, Any]:
        guard(request)
        return {
            "sessions": engine.sessions.snapshot(),
            "cursors": engine.cursors.snapshot(),
        }

    @app.post(f"{prefix}/reset", include_in_schema=False)
    def reset(request: Request) -> dict[str, Any]:
        guard(request)
        engine.pipeline.log.clear()
        engine.pipeline.counts.clear()
        engine.pipeline.total = 0
        return {"reset": True}

    def _require_model() -> None:
        if engine.context is None:
            raise HTTPException(status_code=409, detail="this mock has no model responder")

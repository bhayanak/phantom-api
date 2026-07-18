"""Admin dashboard endpoints: list routes, view statistics, reset state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from phantom_api.constants import ADMIN_PREFIX

if TYPE_CHECKING:
    from phantom_api.server.app import ServerState


def register_dashboard(app: FastAPI, state: ServerState) -> None:
    """Attach ``/__admin`` routes to ``app``."""

    async def list_routes() -> JSONResponse:
        routes = [
            {
                "method": route.normalized_method(),
                "path": route.path,
                "status_code": route.status_code,
                "summary": route.summary,
                "content_type": route.content_type,
            }
            for route in state.spec.routes
        ]
        return JSONResponse(
            {
                "title": state.spec.title,
                "version": state.spec.version,
                "source_type": state.spec.source_type,
                "count": len(routes),
                "routes": routes,
            }
        )

    async def stats() -> JSONResponse:
        return JSONResponse(
            {
                "total_requests": state.total_requests,
                "uptime_seconds": state.uptime_seconds(),
                "route_count": state.spec.route_count(),
                "hits": dict(sorted(state.hits.items(), key=lambda kv: kv[1], reverse=True)),
            }
        )

    async def reset() -> JSONResponse:
        state.reset()
        return JSONResponse({"reset": True, "message": "Statistics cleared."})

    app.add_api_route(f"{ADMIN_PREFIX}/routes", list_routes, methods=["GET"], name="admin:routes")
    app.add_api_route(f"{ADMIN_PREFIX}/stats", stats, methods=["GET"], name="admin:stats")
    app.add_api_route(f"{ADMIN_PREFIX}/reset", reset, methods=["POST"], name="admin:reset")

"""Build Starlette routes from a :class:`MockSpec` and attach them to an app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.routing import Route

from phantom_api.generators.response_builder import ResponseBuilder
from phantom_api.models import MockSpec
from phantom_api.server.handlers import make_handler

if TYPE_CHECKING:
    from fastapi import FastAPI

    from phantom_api.server.app import ServerState


def build_routes(app: FastAPI, spec: MockSpec, state: ServerState, seed: int | None = None) -> None:
    """Register every route in ``spec`` on ``app`` using generic handlers.

    Existing mock routes are cleared first so this is safe to call on hot reload.
    """
    _clear_mock_routes(app)
    builder = ResponseBuilder(seed=seed)

    for route in spec.routes:
        handler = make_handler(route, state, builder)
        app.router.routes.append(
            Route(
                path=route.path,
                endpoint=handler,
                methods=[route.normalized_method()],
                name=f"mock:{route.normalized_method()}:{route.path}",
            )
        )


def _clear_mock_routes(app: FastAPI) -> None:
    app.router.routes = [
        r for r in app.router.routes if not getattr(r, "name", "").startswith("mock:")
    ]

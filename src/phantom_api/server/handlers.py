"""Generic request handlers that serve mocked responses and track statistics."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from phantom_api.generators.response_builder import ResponseBuilder
from phantom_api.models import MockRoute

if TYPE_CHECKING:
    from phantom_api.server.app import ServerState


def make_handler(
    route: MockRoute, state: ServerState, builder: ResponseBuilder
) -> Callable[[Request], object]:
    """Create an async Starlette endpoint that serves ``route``."""

    route_key = f"{route.normalized_method()} {route.path}"

    async def handler(request: Request) -> Response:
        state.record_hit(route_key)

        if route.status_code == 204:
            return Response(status_code=204, headers=dict(route.response_headers))

        body = builder.build(route)
        headers = dict(route.response_headers)

        if route.content_type.startswith("application/json") or isinstance(body, (dict, list)):
            return JSONResponse(content=body, status_code=route.status_code, headers=headers)
        return PlainTextResponse(
            content="" if body is None else str(body),
            status_code=route.status_code,
            headers=headers,
            media_type=route.content_type,
        )

    return handler

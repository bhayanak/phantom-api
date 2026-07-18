"""Request middleware: latency simulation and error injection.

Clients can control behaviour per-request:

* Delay — header ``X-Mock-Delay: 250`` or query ``?__delay=250`` (milliseconds).
* Error — header ``X-Mock-Status: 503`` or query ``?__status=503`` returns that status
  with a JSON error body instead of the mocked response.
"""

from __future__ import annotations

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Cap injected delay so a stray value cannot hang the server.
_MAX_DELAY_MS = 30_000


class MockControlMiddleware(BaseHTTPMiddleware):
    """Apply latency and error-injection controls around every request."""

    def __init__(self, app, *, default_delay_ms: int = 0) -> None:
        super().__init__(app)
        self.default_delay_ms = max(0, int(default_delay_ms))

    async def dispatch(self, request: Request, call_next) -> Response:
        status_override = self._resolve_error(request)
        if status_override is not None:
            await self._sleep(self._resolve_delay(request))
            return JSONResponse(
                status_code=status_override,
                content={
                    "error": True,
                    "status": status_override,
                    "message": f"Injected error response ({status_override}).",
                    "path": request.url.path,
                },
            )

        await self._sleep(self._resolve_delay(request))
        return await call_next(request)

    def _resolve_delay(self, request: Request) -> int:
        raw = request.headers.get("x-mock-delay") or request.query_params.get("__delay")
        if raw is not None:
            try:
                return min(max(0, int(raw)), _MAX_DELAY_MS)
            except ValueError:
                pass
        return min(self.default_delay_ms, _MAX_DELAY_MS)

    @staticmethod
    def _resolve_error(request: Request) -> int | None:
        raw = request.headers.get("x-mock-status") or request.query_params.get("__status")
        if raw is None:
            return None
        try:
            code = int(raw)
        except ValueError:
            return None
        return code if 400 <= code <= 599 else None

    @staticmethod
    async def _sleep(delay_ms: int) -> None:
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

"""Reverse proxy that forwards requests to a real upstream API and records responses."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response

from phantom_api.recorder.storage import Interaction, RecordingStorage

# Hop-by-hop headers that must not be forwarded.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _validate_upstream(upstream: str) -> str:
    parts = urlsplit(upstream)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("Upstream URL must use http or https.")
    if not parts.netloc:
        raise ValueError("Upstream URL must include a host.")
    return upstream.rstrip("/")


def create_proxy_app(upstream: str, storage: RecordingStorage) -> FastAPI:
    """Create a proxy app that forwards to ``upstream`` and records every response."""
    base = _validate_upstream(upstream)
    app = FastAPI(title="phantom-api recorder", docs_url=None, redoc_url=None)
    client = httpx.AsyncClient(base_url=base, timeout=30.0, follow_redirects=True)

    @app.on_event("shutdown")
    async def _close_client() -> None:  # pragma: no cover - requires running loop.
        await client.aclose()

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def proxy(full_path: str, request: Request) -> Response:
        body = await request.body()
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        upstream_response = await client.request(
            method=request.method,
            url=f"/{full_path}",
            params=dict(request.query_params),
            headers=fwd_headers,
            content=body,
        )

        content_type = upstream_response.headers.get("content-type", "application/json")
        parsed_body = _decode_body(upstream_response, content_type)

        interaction = Interaction(
            method=request.method,
            path=f"/{full_path}",
            query=str(request.url.query),
            status_code=upstream_response.status_code,
            response_headers={
                k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP
            },
            content_type=content_type.split(";")[0].strip(),
            body=parsed_body,
        )
        storage.save(interaction)

        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers={
                k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP
            },
            media_type=content_type,
        )

    app.state.storage = storage
    return app


def _decode_body(response: httpx.Response, content_type: str):
    if "application/json" in content_type:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return response.text
    return response.text

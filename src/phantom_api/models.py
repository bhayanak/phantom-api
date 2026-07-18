"""Shared intermediate representation produced by all parsers and consumed by the server.

Every parser (OpenAPI, JSON, Postman) converts its source format into a :class:`MockSpec`
containing a list of :class:`MockRoute` objects. The server never sees the original format.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MockRoute(BaseModel):
    """A single mockable endpoint."""

    method: str = Field(..., description="Uppercase HTTP method, e.g. GET, POST.")
    path: str = Field(
        ..., description="Route path with ``{param}`` placeholders, e.g. /pets/{petId}."
    )
    status_code: int = Field(200, description="Default success status code to return.")
    summary: str = Field("", description="Human-readable description of the route.")
    response_schema: dict[str, Any] | None = Field(
        default=None, description="JSON-schema-like object describing the response body."
    )
    response_example: Any | None = Field(
        default=None, description="Concrete example response taken from the source spec, if any."
    )
    response_headers: dict[str, str] = Field(
        default_factory=dict, description="Static response headers to return."
    )
    content_type: str = Field("application/json", description="Response content type.")

    def normalized_method(self) -> str:
        return self.method.upper()


class MockSpec(BaseModel):
    """A complete parsed specification ready to be turned into a server."""

    title: str = Field("phantom-api mock", description="Human-readable API title.")
    version: str = Field("1.0.0", description="API version string.")
    source_type: str = Field("unknown", description="One of: openapi, swagger, json, postman.")
    routes: list[MockRoute] = Field(default_factory=list)

    def route_count(self) -> int:
        return len(self.routes)

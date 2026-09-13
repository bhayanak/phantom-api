"""REST / OpenAPI protocol pack.

The operation key is ``METHOD /path``, which is what makes a REST endpoint
addressable by the same router and responders as SOAP and JSON-RPC. When a spec
is supplied the path is normalised back to its templated form, so
``GET /pets/42`` and ``GET /pets/7`` share one operation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from phantom_api.mock.config import SessionRule
from phantom_api.mock.protocols.base import register
from phantom_api.mock.types import MockError, Operation, RawRequest, RawResponse


def _template_to_regex(template: str) -> re.Pattern[str]:
    pattern = re.escape(template).replace(r"\{", "{").replace(r"\}", "}")
    pattern = re.sub(r"\{([^{}/]+)\}", r"(?P<\1>[^/]+)", pattern)
    return re.compile(f"^{pattern}$")


class OpenApiPack:
    name = "openapi"

    def __init__(self) -> None:
        self._templates: list[tuple[str, re.Pattern[str]]] = []

    def load_spec(self, spec_path: Path) -> None:
        """Register path templates so concrete URLs collapse onto operations."""
        from phantom_api.parsers import detect_and_parse

        spec = detect_and_parse(spec_path)
        self._templates = [
            (route.path, _template_to_regex(route.path))
            for route in spec.routes
            if "{" in route.path
        ]

    def matches(self, request: RawRequest) -> bool:
        return True  # last resort: any HTTP request is addressable this way

    def decode(self, request: RawRequest, session: SessionRule | None = None) -> Operation:
        template, path_params = self._normalise(request.path)
        params: dict[str, Any] = {
            "path": path_params,
            "query": {k: v[0] if len(v) == 1 else v for k, v in parse_qs(request.query).items()},
        }
        if request.body:
            try:
                params["body"] = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                params["body"] = request.text

        rule = session or SessionRule(transport="header", name="authorization")
        session_id = request.header(rule.name.lower()) or None if rule.transport != "none" else None
        return Operation(
            name=f"{request.method} {template}",
            protocol=self.name,
            params=params,
            headers=dict(request.headers),
            session_id=session_id,
            request=request,
            context={"concrete_path": request.path, "template": template},
        )

    def _normalise(self, path: str) -> tuple[str, dict[str, str]]:
        for template, pattern in self._templates:
            match = pattern.match(path)
            if match:
                return template, match.groupdict()
        return path, {}

    def encode(self, result: Any, op: Operation) -> RawResponse:
        if isinstance(result, RawResponse):
            return result
        if isinstance(result, (str, bytes)):
            body = result.encode() if isinstance(result, str) else result
            return RawResponse(200, {"content-type": "application/json"}, body)
        return RawResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(result, default=str).encode(),
        )

    def encode_error(self, error: MockError, op: Operation | None) -> RawResponse:
        status = error.status if error.status >= 400 else 500
        if error.kind == "not-found":
            status = 404
        elif error.kind == "not-authenticated":
            status = 401
        elif error.kind == "invalid-request":
            status = 400
        body = {"error": error.kind, "message": error.message, **error.detail}
        return RawResponse(
            status=status,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode(),
        )


register(OpenApiPack())

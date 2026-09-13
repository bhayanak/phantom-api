"""JSON-RPC 1.0 / 2.0 protocol pack, batch-aware."""

from __future__ import annotations

import json
from typing import Any

from phantom_api.mock.config import SessionRule
from phantom_api.mock.protocols.base import register
from phantom_api.mock.types import MockError, Operation, RawRequest, RawResponse

_ERROR_CODES = {
    "invalid-request": -32600,
    "not-found": -32601,
    "invalid-params": -32602,
    "internal": -32603,
    "not-authenticated": -32000,
}


class JsonRpcPack:
    name = "jsonrpc"

    def matches(self, request: RawRequest) -> bool:
        if request.method != "POST" or not request.body:
            return False
        if "json" not in request.header("content-type", "application/json"):
            return False
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return False
        first = payload[0] if isinstance(payload, list) and payload else payload
        return isinstance(first, dict) and "method" in first

    def decode(self, request: RawRequest, session: SessionRule | None = None) -> Operation:
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError as exc:
            raise MockError(f"invalid JSON: {exc}", kind="invalid-request", status=400) from exc

        batch = isinstance(payload, list)
        first = payload[0] if batch and payload else payload
        if not isinstance(first, dict) or "method" not in first:
            raise MockError("not a JSON-RPC request", kind="invalid-request", status=400)

        rule = session or SessionRule(transport="header", name="session-id")
        session_id = (
            request.header(rule.name.lower()) or None if rule.transport == "header" else None
        )
        return Operation(
            name=str(first["method"]),
            protocol=self.name,
            params=first.get("params") or {},
            headers=dict(request.headers),
            session_id=session_id,
            request=request,
            context={"id": first.get("id"), "batch": batch, "raw": payload},
        )

    def encode(self, result: Any, op: Operation) -> RawResponse:
        if isinstance(result, RawResponse):
            return result
        body = {"jsonrpc": "2.0", "id": op.context.get("id"), "result": result}
        return RawResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode(),
        )

    def encode_error(self, error: MockError, op: Operation | None) -> RawResponse:
        body = {
            "jsonrpc": "2.0",
            "id": op.context.get("id") if op else None,
            "error": {
                "code": _ERROR_CODES.get(error.kind, -32603),
                "message": error.message,
                **({"data": error.detail} if error.detail else {}),
            },
        }
        # JSON-RPC carries failure in the envelope, so the transport stays 200
        # unless the request itself was unparseable.
        status = 400 if error.kind == "invalid-request" else 200
        return RawResponse(
            status=status,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode(),
        )


register(JsonRpcPack())

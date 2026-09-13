"""Protocol-neutral request, response and operation types.

Everything in :mod:`phantom_api.mock` speaks these three types. A protocol pack
turns bytes into an :class:`Operation` and an :class:`Operation` result back into
bytes; nothing between those two points knows whether the wire format was SOAP,
JSON-RPC or plain REST.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawRequest:
    """An inbound request, before any protocol interpretation."""

    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass(slots=True)
class RawResponse:
    """An outbound response, after protocol serialisation."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @classmethod
    def of(cls, text: str, status: int = 200, content_type: str = "text/plain") -> RawResponse:
        return cls(status=status, headers={"content-type": content_type}, body=text.encode())


@dataclass(slots=True)
class Operation:
    """A decoded request: what the caller asked for, and with what.

    ``name`` is the dispatch key -- the SOAP body element, the JSON-RPC method,
    or ``GET /pets/{id}`` for REST. ``params`` is whatever the pack could
    usefully extract; responders treat it as advisory, never as a schema.
    """

    name: str
    protocol: str
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    request: RawRequest | None = None
    #: Pack-specific detail a responder may need to echo back (namespaces,
    #: envelope style, the JSON-RPC id). Opaque to everything else.
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.protocol}:{self.name}"


class MockError(Exception):
    """A failure the mock wants to express in the target protocol's own terms.

    ``kind`` is a protocol-neutral label (``not-authenticated``, ``not-found``,
    ``invalid-request``, ``internal``); each pack maps it to the shape its
    clients expect. ``detail_type`` lets a pack emit a concrete fault subtype.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "internal",
        status: int = 500,
        detail_type: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status
        self.detail_type = detail_type
        self.detail = detail or {}


class NotHandled(Exception):
    """Raised by a responder that cannot answer, so the next one may try."""

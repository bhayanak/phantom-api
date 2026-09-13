"""Protocol pack contract and registry.

A pack is the only place that knows a wire format. It answers three questions:
what operation is this, how do I serialise a result, and how does this protocol
express failure.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from phantom_api.mock.config import SessionRule
from phantom_api.mock.types import MockError, Operation, RawRequest, RawResponse


@runtime_checkable
class ProtocolPack(Protocol):
    name: str

    def matches(self, request: RawRequest) -> bool:
        """Whether this pack can decode the request, for auto-detection."""

    def decode(self, request: RawRequest, session: SessionRule | None = None) -> Operation: ...

    def encode(self, result: Any, op: Operation) -> RawResponse: ...

    def encode_error(self, error: MockError, op: Operation | None) -> RawResponse: ...


_REGISTRY: dict[str, ProtocolPack] = {}


def register(pack: ProtocolPack) -> ProtocolPack:
    _REGISTRY[pack.name] = pack
    return pack


def get_pack(name: str) -> ProtocolPack:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown protocol pack {name!r}; known packs: {known}")
    return _REGISTRY[name]


def available() -> list[str]:
    return sorted(_REGISTRY)


def detect(request: RawRequest) -> ProtocolPack | None:
    """Pick a pack by sniffing the request, for the zero-config replay case."""
    for name in ("soap", "jsonrpc", "openapi"):
        pack = _REGISTRY.get(name)
        if pack and pack.matches(request):
            return pack
    return None

"""Protocol packs: the only modules that know a wire format."""

from __future__ import annotations

from phantom_api.mock.protocols import jsonrpc, openapi, soap  # noqa: F401 - registration
from phantom_api.mock.protocols.base import ProtocolPack, available, detect, get_pack, register

__all__ = ["ProtocolPack", "available", "detect", "get_pack", "register"]

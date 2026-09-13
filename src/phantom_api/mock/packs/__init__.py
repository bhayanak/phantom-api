"""Domain packs: the product-specific half of a stateful mock."""

from __future__ import annotations

from phantom_api.mock.packs import vcenter  # noqa: F401 - registers the bundled pack
from phantom_api.mock.packs.base import DomainPack, PackContext, available, get_pack, register

__all__ = ["DomainPack", "PackContext", "available", "get_pack", "register"]

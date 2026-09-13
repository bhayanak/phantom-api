"""Build a model from a scenario description.

Generation is seeded, so the same scenario always produces the same world.
Reproducibility matters more here than variety: a test that fails against a
generated inventory is only useful if the inventory can be regenerated.
"""

from __future__ import annotations

import random
from typing import Any

from phantom_api.mock.model.entity import Entity, Inventory


class IdAllocator:
    """Native-looking identifiers: ``vm-42``, ``host-7``, ``group-d1``."""

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._prefixes: dict[str, str] = {}

    def prefix_for(self, kind: str, prefix: str) -> None:
        self._prefixes[kind] = prefix

    def allocate(self, kind: str) -> str:
        prefix = self._prefixes.get(kind, kind.lower())
        value = f"{prefix}-{self._next}"
        self._next += 1
        return value


class Builder:
    """Small helper packs use to assemble an inventory without boilerplate."""

    def __init__(self, seed: int | None = None) -> None:
        self.inventory = Inventory()
        self.ids = IdAllocator()
        self.random = random.Random(seed)

    def entity(
        self,
        kind: str,
        *,
        name: str | None = None,
        props: dict[str, Any] | None = None,
        parent: Entity | None = None,
        parent_edge: str | None = None,
    ) -> Entity:
        entity = Entity(id=self.ids.allocate(kind), kind=kind, props=dict(props or {}))
        if name is not None:
            entity.props.setdefault("name", name)
        self.inventory.add(entity)
        if parent is not None:
            entity.edges.setdefault("parent", []).append(parent.id)
            if parent_edge:
                parent.edges.setdefault(parent_edge, []).append(entity.id)
        return entity

    def link(self, source: Entity, edge: str, target: Entity, *, back: str | None = None) -> None:
        source.edges.setdefault(edge, []).append(target.id)
        if back:
            target.edges.setdefault(back, []).append(source.id)

    def pick(self, items: list[Entity]) -> Entity:
        return self.random.choice(items)


def expand_counts(node: Any, key: str, default: int = 0) -> int:
    """Read a count from a scenario node, tolerating missing sections."""
    if not isinstance(node, dict):
        return default
    value = node.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

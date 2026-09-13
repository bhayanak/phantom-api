"""The domain model: typed entities, typed edges, addressable properties.

Deliberately protocol-free. Nothing here knows about SOAP, and nothing here
knows about any particular product -- an entity is an identifier, a kind, a
nested property bag and a set of named references to other entities.

Two behaviours are worth more than they look:

* :meth:`Entity.resolve` returns a *partial* result. Real systems report
  per-property failures rather than failing a whole read, and a mock that
  cannot express that trains its consumers on a shape they will never see.
* Edges are named, not typed by target. The same traversal engine then works
  for any product whose objects reference each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

MISSING = object()


@dataclass(slots=True)
class Entity:
    id: str
    kind: str
    props: dict[str, Any] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, path: str) -> Any:
        """Look up a dotted property path, returning ``MISSING`` if absent."""
        if path in self.props:
            return self.props[path]
        node: Any = self.props
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return MISSING
        return node

    def set(self, path: str, value: Any) -> None:
        """Write a dotted property path, creating intermediate levels."""
        parts = path.split(".")
        node = self.props
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    def refs(self, edge: str) -> list[str]:
        return self.edges.get(edge, [])


@dataclass(slots=True)
class PropertyResult:
    """One entity's answer to a property query: what resolved, what did not."""

    entity: Entity
    values: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


class Inventory:
    """An addressable collection of entities."""

    def __init__(self, entities: list[Entity] | None = None) -> None:
        self._by_id: dict[str, Entity] = {}
        self._by_kind: dict[str, list[Entity]] = {}
        for entity in entities or []:
            self.add(entity)

    def add(self, entity: Entity) -> Entity:
        self._by_id[entity.id] = entity
        self._by_kind.setdefault(entity.kind, []).append(entity)
        return entity

    def remove(self, entity_id: str) -> Entity | None:
        entity = self._by_id.pop(entity_id, None)
        if entity is not None:
            self._by_kind.get(entity.kind, []).remove(entity)
            for other in self._by_id.values():
                for refs in other.edges.values():
                    if entity_id in refs:
                        refs.remove(entity_id)
        return entity

    def get(self, entity_id: str) -> Entity | None:
        return self._by_id.get(entity_id)

    def of_kind(self, kind: str) -> list[Entity]:
        return list(self._by_kind.get(kind, []))

    def kinds(self) -> dict[str, int]:
        return {kind: len(items) for kind, items in sorted(self._by_kind.items())}

    def find_by_prop(self, kind: str, path: str, value: Any) -> Entity | None:
        for entity in self.of_kind(kind):
            if entity.resolve(path) == value:
                return entity
        return None

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Entity]:
        return iter(self._by_id.values())

    def read(self, entity: Entity, paths: list[str]) -> PropertyResult:
        result = PropertyResult(entity=entity)
        for path in paths:
            value = entity.resolve(path)
            if value is MISSING:
                # An edge is a property too, as far as the caller is concerned.
                if path in entity.edges:
                    result.values[path] = entity.edges[path]
                else:
                    result.missing.append(path)
            else:
                result.values[path] = value
        return result

    def to_json(self) -> dict[str, Any]:
        return {
            "count": len(self._by_id),
            "by_kind": self.kinds(),
            "entities": [
                {"id": e.id, "kind": e.kind, "props": e.props, "edges": e.edges}
                for e in self._by_id.values()
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Inventory:
        entities = [
            Entity(
                id=item.get("id") or item["moref"],
                kind=item["kind"] if "kind" in item else item["type"],
                props=dict(item.get("props", {})),
                edges={
                    name: [ref["value"] if isinstance(ref, dict) else str(ref) for ref in refs]
                    for name, refs in (item.get("edges") or {}).items()
                },
            )
            for item in data.get("entities", data.get("objects", []))
        ]
        return cls(entities)

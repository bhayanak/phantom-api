"""Client-directed graph traversal.

Some protocols do not ask "give me this object"; they hand the server a
traversal program -- start here, follow these named edges, recurse via these
other rules -- and expect the visited set back. This evaluates that program.

It is the single most testable component in the package and the one most worth
getting right: every inventory read in a graph-shaped protocol goes through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phantom_api.mock.model.entity import Entity, Inventory

MAX_VISITS = 200_000


@dataclass(slots=True)
class TraversalSpec:
    """Follow ``edge`` from entities of ``kind``, then apply ``select``."""

    name: str
    kind: str
    edge: str
    select: list[str] = field(default_factory=list)
    #: When true the traversed-from entity is not itself returned.
    skip: bool = False


@dataclass(slots=True)
class ObjectSpec:
    """Where to start, and which traversals to apply from there."""

    start: str
    select: list[str] = field(default_factory=list)
    skip: bool = False


class TraversalError(ValueError):
    """Raised when a traversal program references a rule that does not exist."""


def traverse(
    inventory: Inventory,
    object_spec: ObjectSpec,
    specs: dict[str, TraversalSpec],
    subtypes: dict[str, set[str]] | None = None,
) -> list[Entity]:
    """Return every entity reachable from ``object_spec``, in visit order.

    Cycles are common in real inventories (a host lists its VMs, each VM names
    its host), so visited identifiers are tracked rather than trusting the
    client's program to terminate.

    ``subtypes`` maps a base kind to the concrete kinds that satisfy it. Clients
    write traversals against the base type, so without it a rule declared on
    ``ComputeResource`` never fires for a ``ClusterComputeResource``.
    """
    root = inventory.get(object_spec.start)
    if root is None:
        return []

    subtypes = subtypes or {}

    def kind_matches(declared: str, actual: str) -> bool:
        return not declared or declared == actual or actual in subtypes.get(declared, set())

    visited: set[str] = set()
    ordered: list[Entity] = []
    budget = MAX_VISITS

    def visit(entity: Entity, select: list[str], skip: bool) -> None:
        nonlocal budget
        if budget <= 0:
            return
        budget -= 1
        if entity.id not in visited:
            visited.add(entity.id)
            if not skip:
                ordered.append(entity)
        for name in select:
            spec = specs.get(name)
            if spec is None:
                raise TraversalError(f"traversal rule {name!r} is not defined")
            if not kind_matches(spec.kind, entity.kind):
                continue
            for ref in entity.refs(spec.edge):
                target = inventory.get(ref)
                if target is None or target.id in visited:
                    continue
                visit(target, spec.select, spec.skip)

    visit(root, object_spec.select, object_spec.skip)
    return ordered


def descendants(inventory: Inventory, start: str, edges: list[str]) -> list[Entity]:
    """Convenience traversal: follow ``edges`` from ``start`` to exhaustion."""
    spec = TraversalSpec(name="walk", kind="", edge="", select=["walk"])
    specs = {
        f"walk-{edge}": TraversalSpec(
            name=f"walk-{edge}", kind="", edge=edge, select=[f"walk-{e}" for e in edges]
        )
        for edge in edges
    }
    del spec
    return traverse(
        inventory,
        ObjectSpec(start=start, select=[f"walk-{e}" for e in edges]),
        specs,
    )

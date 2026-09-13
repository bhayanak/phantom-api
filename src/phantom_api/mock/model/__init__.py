"""The protocol-free domain model: entities, edges, traversal, evolution."""

from __future__ import annotations

from phantom_api.mock.model.entity import MISSING, Entity, Inventory, PropertyResult
from phantom_api.mock.model.evolution import ChangeEvent, EvolutionEngine, parse_duration
from phantom_api.mock.model.generator import Builder, IdAllocator
from phantom_api.mock.model.graph import ObjectSpec, TraversalSpec, traverse

__all__ = [
    "MISSING",
    "Builder",
    "ChangeEvent",
    "Entity",
    "EvolutionEngine",
    "IdAllocator",
    "Inventory",
    "ObjectSpec",
    "PropertyResult",
    "TraversalSpec",
    "parse_duration",
    "traverse",
]

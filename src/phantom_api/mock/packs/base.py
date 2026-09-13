"""Domain packs: the product-specific half of a stateful mock.

A pack supplies three things and nothing else:

* how to build a model from a scenario file,
* how each operation projects onto that model,
* what each change event does to it.

Everything else -- transport, sessions, cursors, chaos, the control plane -- is
the framework's job. Keeping that line sharp is what makes the second pack cheap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from phantom_api.mock.model.entity import Inventory
from phantom_api.mock.model.evolution import EvolutionEngine, Mutation
from phantom_api.mock.session import SessionStore
from phantom_api.mock.types import Operation


@dataclass
class PackContext:
    """Everything a projection may touch."""

    inventory: Inventory
    evolution: EvolutionEngine
    sessions: SessionStore
    scenario: dict[str, Any]
    catalogs: dict[str, Any] = field(default_factory=dict)
    cursors: Any = None
    data_dir: Path | None = None

    def catalog(self, name: str, default: Any = None) -> Any:
        return self.catalogs.get(name, default)


#: A projection answers one operation against the context.
Projection = Callable[[PackContext, Operation], Any]


@runtime_checkable
class DomainPack(Protocol):
    name: str

    def build(self, scenario: dict[str, Any], seed: int | None) -> Inventory: ...

    def projections(self) -> dict[str, Projection]: ...

    def mutations(self) -> dict[str, Mutation]: ...

    def catalogs(self, data_dir: Path | None) -> dict[str, Any]: ...


_REGISTRY: dict[str, DomainPack] = {}


def register(pack: DomainPack) -> DomainPack:
    _REGISTRY[pack.name] = pack
    return pack


def get_pack(name: str) -> DomainPack:
    if name not in _REGISTRY:
        _load_entry_points()
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown domain pack {name!r}; known packs: {known}")
    return _REGISTRY[name]


def available() -> list[str]:
    _load_entry_points()
    return sorted(_REGISTRY)


_entry_points_loaded = False


def _load_entry_points() -> None:
    """Discover third-party packs installed as separate distributions."""
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    try:
        from importlib.metadata import entry_points

        for entry in entry_points(group="phantom_api.packs"):
            try:
                register(entry.load()())
            except Exception:
                continue
    except Exception:
        return

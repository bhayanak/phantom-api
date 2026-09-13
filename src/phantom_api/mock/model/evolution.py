"""Turn a change script into model mutations plus matching change events.

The invariant this file exists to enforce:

    every emitted event mutates the model *first*, then appends to the log.

A mock that announces a state change while still reporting the old state teaches
its consumers to distrust their own reconciliation logic -- which is exactly the
bug class those consumers exist to catch.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from phantom_api.mock.model.entity import Entity, Inventory

_DURATION = re.compile(r"^\s*(\d+)\s*(ms|s|m|h)?\s*$", re.IGNORECASE)
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

#: A mutation takes (inventory, target, arguments) and returns a change summary.
Mutation = Callable[[Inventory, Entity | None, dict[str, Any]], dict[str, Any]]


def parse_duration(value: str | int | float) -> float:
    """Parse ``60s``/``5m``/``250ms`` into seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"invalid duration {value!r}")
    return int(match.group(1)) * _UNITS[(match.group(2) or "s").lower()]


@dataclass(slots=True)
class ChangeEvent:
    """One recorded change. Protocol packs project this onto the wire."""

    key: int
    type: str
    created_at: float
    target_id: str | None
    target_kind: str | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScheduledChange:
    at: float
    type: str
    target: str | None
    args: dict[str, Any] = field(default_factory=dict)


class EvolutionEngine:
    """Drives the model forward, either on a script or at random."""

    def __init__(
        self,
        inventory: Inventory,
        *,
        mutations: dict[str, Mutation] | None = None,
        seed: int | None = None,
        max_events: int = 50_000,
    ) -> None:
        self.inventory = inventory
        self.mutations = dict(mutations or {})
        self.events: list[ChangeEvent] = []
        self.timeline: list[ScheduledChange] = []
        self.weights: dict[str, int] = {}
        self.events_per_minute = 0
        self.mode = "off"
        self._next_key = 1
        self._started = time.monotonic()
        self._fired = 0
        self._last_churn = self._started
        self._random = random.Random(seed)
        self._max_events = max_events

    # -- configuration ---------------------------------------------------

    def load(self, config: dict[str, Any]) -> None:
        self.mode = config.get("mode", "off")
        for item in config.get("timeline") or []:
            self.timeline.append(
                ScheduledChange(
                    at=parse_duration(item.get("at", 0)),
                    type=item["event"],
                    target=item.get("target"),
                    args={k: v for k, v in item.items() if k not in {"at", "event", "target"}},
                )
            )
        self.timeline.sort(key=lambda c: c.at)
        churn = config.get("churn") or {}
        self.events_per_minute = int(churn.get("events_per_minute", 0))
        self.weights = {str(k): int(v) for k, v in (churn.get("weights") or {}).items()}

    # -- driving ---------------------------------------------------------

    def tick(self, now: float | None = None) -> list[ChangeEvent]:
        """Apply everything due since the last tick. Called before each read."""
        if self.mode == "off":
            return []
        now = now if now is not None else time.monotonic()
        elapsed = now - self._started
        fired: list[ChangeEvent] = []

        if self.mode == "timeline":
            while self._fired < len(self.timeline) and self.timeline[self._fired].at <= elapsed:
                change = self.timeline[self._fired]
                self._fired += 1
                event = self.apply(change.type, change.target, change.args)
                if event:
                    fired.append(event)

        elif self.mode == "churn" and self.events_per_minute > 0 and self.weights:
            interval = 60.0 / self.events_per_minute
            while now - self._last_churn >= interval:
                self._last_churn += interval
                event = self.apply(self._weighted_choice(), None, {})
                if event:
                    fired.append(event)

        return fired

    def _weighted_choice(self) -> str:
        population = list(self.weights)
        weights = [self.weights[name] for name in population]
        return self._random.choices(population, weights=weights, k=1)[0]

    def apply(
        self, event_type: str, target: str | None, args: dict[str, Any] | None = None
    ) -> ChangeEvent | None:
        """Mutate the model, then record the event. Never the other way round."""
        args = args or {}
        entity = self._resolve(target)
        mutation = self.mutations.get(event_type)
        detail = mutation(self.inventory, entity, args) if mutation else dict(args)
        if entity is None and detail.get("entity_id"):
            entity = self.inventory.get(detail["entity_id"])

        event = ChangeEvent(
            key=self._next_key,
            type=event_type,
            created_at=time.time(),
            target_id=entity.id if entity else detail.get("entity_id"),
            target_kind=entity.kind if entity else detail.get("entity_kind"),
            detail=detail,
        )
        self._next_key += 1
        self.events.append(event)
        if len(self.events) > self._max_events:
            del self.events[: len(self.events) - self._max_events]
        return event

    def _resolve(self, target: str | None) -> Entity | None:
        """Accept ``kind:name`` or a bare identifier."""
        if not target:
            return None
        kind, _, name = target.partition(":")
        if not name:
            return self.inventory.get(kind)
        direct = self.inventory.get(name)
        if direct is not None:
            return direct
        for entity in self.inventory:
            if entity.props.get("name") == name:
                return entity
        return None

    def since(self, key: int = 0, limit: int = 1000) -> list[ChangeEvent]:
        return [e for e in self.events if e.key > key][:limit]

    def between(self, begin: float, end: float) -> list[ChangeEvent]:
        return [e for e in self.events if begin <= e.created_at <= end]

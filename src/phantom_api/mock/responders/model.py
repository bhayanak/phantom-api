"""Answer operations from the domain model.

This responder owns no protocol knowledge and no product knowledge. It holds the
context -- inventory, evolution engine, sessions, cursors -- and hands each
operation to the projection the pack registered for it.
"""

from __future__ import annotations

from typing import Any

from phantom_api.mock.packs.base import PackContext, Projection
from phantom_api.mock.types import NotHandled, Operation


class ModelResponder:
    name = "model"

    def __init__(self, context: PackContext, projections: dict[str, Projection]) -> None:
        self.context = context
        self.projections = projections

    def respond(self, op: Operation) -> Any:
        projection = self.projections.get(op.name)
        if projection is None:
            raise NotHandled(f"no projection for {op.name}")
        # Advance the world before reading it, so a read never returns state
        # that predates an event the client has already been told about.
        self.context.evolution.tick()
        return projection(self.context, op)

    def describe(self) -> str:
        return f"model({len(self.context.inventory)} entities, {len(self.projections)} projections)"

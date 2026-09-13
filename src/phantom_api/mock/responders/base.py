"""Responder contract.

A responder turns a decoded :class:`Operation` into a result. Raising
:class:`NotHandled` passes the request to the next matching rule, which is how
a corpus, a model and a proxy compose into one mock.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from phantom_api.mock.types import Operation


@runtime_checkable
class Responder(Protocol):
    name: str

    def respond(self, op: Operation) -> Any:
        """Return a result, or raise NotHandled to defer to the next rule."""

    def describe(self) -> str:
        """One line for the startup banner and the control plane."""

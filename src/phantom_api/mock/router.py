"""Ordered rule matching: protocol selection and responder selection.

Rules are evaluated top to bottom and the first match wins, which is the same
model as nginx and Envoy. Patterns are glob-ish -- ``/rest/**`` and ``Retrieve*``
-- because that is what people already expect from a config file.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

from phantom_api.mock.config import ProtocolConfig, ResponderRule, SessionRule
from phantom_api.mock.types import Operation, RawRequest


def path_matches(pattern: str, path: str) -> bool:
    """``/rest/**`` matches any depth, ``/rest/*`` matches one segment."""
    if pattern in ("*", "/**", "**"):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


@dataclass(slots=True)
class ProtocolBinding:
    config: ProtocolConfig
    pack: Any

    def session_for(self, path: str) -> SessionRule | None:
        for pattern, rule in self.config.session.items():
            if path_matches(pattern, path):
                return rule
        return None


class ProtocolRouter:
    """Choose the protocol pack for an inbound request."""

    def __init__(self, bindings: list[ProtocolBinding]) -> None:
        self.bindings = bindings

    def select(self, request: RawRequest) -> ProtocolBinding | None:
        for binding in self.bindings:
            if any(path_matches(p, request.path) for p in binding.config.paths):
                return binding
        return None


@dataclass(slots=True)
class ResponderBinding:
    rule: ResponderRule
    responder: Any

    def matches(self, op: Operation) -> bool:
        match = self.rule.match
        if match.protocol and match.protocol != op.protocol:
            return False
        if match.path and op.request and not path_matches(match.path, op.request.path):
            return False
        if match.operation:
            return any(_op_matches(pattern, op.name) for pattern in match.operation)
        return True


def _op_matches(pattern: str, name: str) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("re:"):
        return re.search(pattern[3:], name) is not None
    return fnmatch.fnmatchcase(name, pattern)


class ResponderRouter:
    """Every responder that could answer an operation, in declaration order."""

    def __init__(self, bindings: list[ResponderBinding]) -> None:
        self.bindings = bindings

    def candidates(self, op: Operation) -> list[ResponderBinding]:
        return [binding for binding in self.bindings if binding.matches(op)]

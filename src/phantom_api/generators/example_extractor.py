"""Extract concrete example values from a route, preferring spec-provided examples."""

from __future__ import annotations

from typing import Any

from phantom_api.models import MockRoute


class ExampleExtractor:
    """Return spec examples when present; signal absence otherwise."""

    _SENTINEL = object()

    @classmethod
    def extract(cls, route: MockRoute) -> Any:
        """Return the route's example, or :data:`ExampleExtractor._SENTINEL` if none."""
        if route.response_example is not None:
            return route.response_example
        if isinstance(route.response_schema, dict) and "example" in route.response_schema:
            return route.response_schema["example"]
        return cls._SENTINEL

    @classmethod
    def has_example(cls, route: MockRoute) -> bool:
        return cls.extract(route) is not cls._SENTINEL

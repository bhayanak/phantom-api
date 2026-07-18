"""Build a concrete response body for a route from its example or schema."""

from __future__ import annotations

from typing import Any

from phantom_api.generators.example_extractor import ExampleExtractor
from phantom_api.generators.fake_data import FakeDataGenerator
from phantom_api.models import MockRoute


class ResponseBuilder:
    """Turn a :class:`MockRoute` into a response body.

    Priority: spec examples first, then schema-driven fake data.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._faker = FakeDataGenerator(seed=seed)

    def build(self, route: MockRoute) -> Any:
        if route.status_code == 204:
            return None

        if ExampleExtractor.has_example(route):
            return ExampleExtractor.extract(route)

        if route.response_schema is not None:
            return self._faker.generate(route.response_schema)

        # No schema and no example: return an empty object as a safe default.
        return {}

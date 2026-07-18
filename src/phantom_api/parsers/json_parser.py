"""Parse a plain JSON file into auto-generated CRUD endpoints.

Two shapes are supported:

* An object whose values are arrays (``{"users": [...], "posts": [...]}``) — each key
  becomes a REST collection.
* A bare array (``[...]``) — becomes a single collection named after the file stem.
"""

from __future__ import annotations

import re
from typing import Any

from phantom_api.constants import MAX_ROUTES
from phantom_api.models import MockRoute, MockSpec
from phantom_api.parsers.base import BaseParser, ParserError

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]+")


class JSONParser(BaseParser):
    """Parser that turns example JSON data into CRUD routes."""

    def _matches(self, data: Any) -> bool:
        # A JSON file is a fallback: match arrays, or objects that are not specs/collections.
        if isinstance(data, list):
            return True
        if isinstance(data, dict):
            return "openapi" not in data and "swagger" not in data and "info" not in data
        return False

    def parse(self) -> MockSpec:
        data = self.load_raw()
        collections = self._as_collections(data)

        spec = MockSpec(
            title=f"{self.path.stem} mock",
            version="1.0.0",
            source_type="json",
        )

        for name, items in collections.items():
            spec.routes.extend(self._crud_routes(name, items))
            if len(spec.routes) > MAX_ROUTES:
                raise ParserError(f"Spec exceeds the maximum of {MAX_ROUTES} routes.")

        if not spec.routes:
            raise ParserError("No collections could be derived from the JSON file.")
        return spec

    def _as_collections(self, data: Any) -> dict[str, list[Any]]:
        if isinstance(data, list):
            name = self._safe_name(self.path.stem) or "items"
            return {name: data}

        if isinstance(data, dict):
            collections: dict[str, list[Any]] = {}
            for key, value in data.items():
                if isinstance(value, list):
                    collections[self._safe_name(key)] = value
            if collections:
                return collections
            # A single object becomes a one-item collection.
            name = self._safe_name(self.path.stem) or "items"
            return {name: [data]}

        raise ParserError("JSON root must be an object or array.")

    @staticmethod
    def _safe_name(name: str) -> str:
        return _SAFE_SEGMENT.sub("-", str(name)).strip("-").lower()

    def _crud_routes(self, name: str, items: list[Any]) -> list[MockRoute]:
        sample = items[0] if items else {}
        item_schema = self._infer_schema(sample)
        collection_path = f"/{name}"
        item_path = f"/{name}/{{id}}"

        return [
            MockRoute(
                method="GET",
                path=collection_path,
                status_code=200,
                summary=f"List {name}",
                response_schema={"type": "array", "items": item_schema},
                response_example=items,
            ),
            MockRoute(
                method="POST",
                path=collection_path,
                status_code=201,
                summary=f"Create {name}",
                response_schema=item_schema,
                response_example=sample,
            ),
            MockRoute(
                method="GET",
                path=item_path,
                status_code=200,
                summary=f"Get {name} by id",
                response_schema=item_schema,
                response_example=sample,
            ),
            MockRoute(
                method="PUT",
                path=item_path,
                status_code=200,
                summary=f"Update {name}",
                response_schema=item_schema,
                response_example=sample,
            ),
            MockRoute(
                method="DELETE",
                path=item_path,
                status_code=204,
                summary=f"Delete {name}",
                response_schema=None,
                response_example=None,
            ),
        ]

    def _infer_schema(self, value: Any) -> dict[str, Any]:
        """Infer a lightweight JSON schema from an example value."""
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
        if isinstance(value, list):
            items = self._infer_schema(value[0]) if value else {"type": "string"}
            return {"type": "array", "items": items}
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {k: self._infer_schema(v) for k, v in value.items()},
            }
        return {"type": "string"}

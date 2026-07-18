"""Schema-aware fake data generation using Faker.

Maps JSON-schema / OpenAPI schema fragments to realistic values. Generation priority:

1. An ``example`` embedded in the schema.
2. A semantic match based on ``format`` or the property name (e.g. ``email`` → an address).
3. A type-based fallback.
"""

from __future__ import annotations

from typing import Any

import exrex
from faker import Faker

# Maximum nesting depth to guard against pathological / recursive schemas.
_MAX_DEPTH = 8
# Number of items to generate for an unbounded array.
_DEFAULT_ARRAY_ITEMS = 2


class FakeDataGenerator:
    """Generate deterministic-ish fake data from schema fragments."""

    def __init__(self, seed: int | None = None) -> None:
        self._faker = Faker()
        if seed is not None:
            Faker.seed(seed)
            self._seed = seed
        else:
            self._seed = None

    def generate(self, schema: Any, field_name: str = "", _depth: int = 0) -> Any:
        if not isinstance(schema, dict) or _depth > _MAX_DEPTH:
            return None

        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if "const" in schema:
            return schema["const"]
        if schema.get("enum"):
            return schema["enum"][0]

        for combinator in ("allOf", "oneOf", "anyOf"):
            if combinator in schema and isinstance(schema[combinator], list) and schema[combinator]:
                if combinator == "allOf":
                    merged: dict[str, Any] = {"type": "object", "properties": {}}
                    for sub in schema[combinator]:
                        if isinstance(sub, dict):
                            merged["properties"].update(sub.get("properties", {}))
                    return self.generate(merged, field_name, _depth)
                return self.generate(schema[combinator][0], field_name, _depth + 1)

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            schema_type = next((t for t in schema_type if t != "null"), schema_type[0])

        if schema_type is None:
            if "properties" in schema:
                schema_type = "object"
            elif "items" in schema:
                schema_type = "array"

        handler = {
            "string": self._string,
            "integer": self._integer,
            "number": self._number,
            "boolean": self._boolean,
            "array": self._array,
            "object": self._object,
            "null": lambda *_: None,
        }.get(schema_type)

        if handler is None:
            return None
        return handler(schema, field_name, _depth)

    def _string(self, schema: dict[str, Any], field_name: str, _depth: int) -> str:
        pattern = schema.get("pattern")
        if pattern:
            try:
                return str(exrex.getone(pattern))
            except Exception:
                pass

        fmt = schema.get("format", "")
        by_format = self._string_by_format(fmt)
        if by_format is not None:
            return by_format

        by_name = self._string_by_name(field_name)
        if by_name is not None:
            return by_name

        min_len = schema.get("minLength", 0)
        max_len = schema.get("maxLength")
        text = self._faker.word()
        if max_len is not None and len(text) > max_len:
            text = text[:max_len]
        if len(text) < min_len:
            text = text.ljust(min_len, "x")
        return text

    def _string_by_format(self, fmt: str) -> str | None:
        mapping = {
            "email": self._faker.email,
            "uri": self._faker.url,
            "url": self._faker.url,
            "hostname": self._faker.hostname,
            "ipv4": self._faker.ipv4,
            "ipv6": self._faker.ipv6,
            "uuid": lambda: str(self._faker.uuid4()),
            "date-time": lambda: self._faker.iso8601(),
            "date": lambda: self._faker.date(),
            "time": lambda: self._faker.time(),
            "password": lambda: self._faker.password(),
            "byte": lambda: self._faker.pystr(),
        }
        func = mapping.get(fmt)
        return func() if func else None

    def _string_by_name(self, field_name: str) -> str | None:
        name = field_name.lower()
        if not name:
            return None
        checks: list[tuple[tuple[str, ...], Any]] = [
            (("email",), self._faker.email),
            (("first_name", "firstname", "given"), self._faker.first_name),
            (("last_name", "lastname", "surname", "family"), self._faker.last_name),
            (("username", "user_name", "login"), self._faker.user_name),
            (("name", "title"), self._faker.name),
            (("phone", "mobile", "tel"), self._faker.phone_number),
            (("address", "street"), self._faker.address),
            (("city",), self._faker.city),
            (("country",), self._faker.country),
            (("zip", "postal"), self._faker.postcode),
            (("company", "organization", "org"), self._faker.company),
            (("url", "uri", "website", "link"), self._faker.url),
            (("description", "summary", "bio", "about"), self._faker.sentence),
            (("color", "colour"), self._faker.color_name),
            (("id", "uuid", "guid"), lambda: str(self._faker.uuid4())),
        ]
        for keys, func in checks:
            if any(key in name for key in keys):
                return str(func())
        return None

    def _integer(self, schema: dict[str, Any], field_name: str, _depth: int) -> int:
        minimum = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        maximum = schema.get("maximum", schema.get("exclusiveMaximum", 1000))
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        return self._faker.random_int(int(minimum), int(maximum))

    def _number(self, schema: dict[str, Any], field_name: str, _depth: int) -> float:
        minimum = float(schema.get("minimum", 0))
        maximum = float(schema.get("maximum", 1000))
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        return round(self._faker.pyfloat(min_value=minimum, max_value=maximum), 2)

    def _boolean(self, schema: dict[str, Any], field_name: str, _depth: int) -> bool:
        return self._faker.pybool()

    def _array(self, schema: dict[str, Any], field_name: str, _depth: int) -> list[Any]:
        items_schema = schema.get("items", {"type": "string"})
        count = schema.get("minItems", _DEFAULT_ARRAY_ITEMS)
        max_items = schema.get("maxItems")
        if max_items is not None:
            count = min(count if count else _DEFAULT_ARRAY_ITEMS, max_items)
        count = max(int(count) or _DEFAULT_ARRAY_ITEMS, 1)
        return [self.generate(items_schema, field_name, _depth + 1) for _ in range(count)]

    def _object(self, schema: dict[str, Any], field_name: str, _depth: int) -> dict[str, Any]:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            return {key: self.generate(prop, key, _depth + 1) for key, prop in properties.items()}
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return {"key": self.generate(additional, "key", _depth + 1)}
        return {}

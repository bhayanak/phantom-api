"""Tests for schema-aware fake data generation."""

from __future__ import annotations

import pytest

from phantom_api.generators.fake_data import FakeDataGenerator
from phantom_api.generators.response_builder import ResponseBuilder
from phantom_api.models import MockRoute


def test_generates_email_from_format():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "format": "email"})
    assert "@" in value


def test_generates_uuid_from_format():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "format": "uuid"})
    assert len(value) == 36


def test_integer_respects_bounds():
    gen = FakeDataGenerator(seed=2)
    for _ in range(20):
        value = gen.generate({"type": "integer", "minimum": 5, "maximum": 10})
        assert 5 <= value <= 10


def test_enum_returns_member():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "enum": ["a", "b", "c"]})
    assert value == "a"


def test_object_with_properties():
    gen = FakeDataGenerator(seed=1)
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "email": {"type": "string", "format": "email"},
            "active": {"type": "boolean"},
        },
    }
    result = gen.generate(schema)
    assert set(result) == {"id", "email", "active"}
    assert isinstance(result["id"], int)
    assert isinstance(result["active"], bool)


def test_array_generation():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"type": "array", "items": {"type": "integer"}, "minItems": 3})
    assert isinstance(result, list)
    assert len(result) == 3


def test_semantic_name_match():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string"}, field_name="email_address")
    assert "@" in value


def test_pattern_generation():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "pattern": "[0-9]{3}"})
    assert value.isdigit()
    assert len(value) == 3


def test_seed_is_deterministic():
    a = FakeDataGenerator(seed=42).generate({"type": "string", "format": "email"})
    b = FakeDataGenerator(seed=42).generate({"type": "string", "format": "email"})
    assert a == b


def test_response_builder_prefers_example():
    route = MockRoute(method="GET", path="/x", response_example={"hello": "world"})
    assert ResponseBuilder().build(route) == {"hello": "world"}


def test_response_builder_uses_schema():
    route = MockRoute(
        method="GET",
        path="/x",
        response_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    )
    result = ResponseBuilder(seed=1).build(route)
    assert "n" in result


def test_response_builder_204_is_none():
    route = MockRoute(method="DELETE", path="/x", status_code=204)
    assert ResponseBuilder().build(route) is None


def test_response_builder_no_schema_no_example():
    route = MockRoute(method="GET", path="/x")
    assert ResponseBuilder().build(route) == {}


def test_number_type_respects_bounds():
    gen = FakeDataGenerator(seed=3)
    for _ in range(20):
        value = gen.generate({"type": "number", "minimum": 1.0, "maximum": 2.0})
        assert 1.0 <= value <= 2.0


def test_integer_swapped_bounds():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "integer", "minimum": 10, "maximum": 1})
    assert 1 <= value <= 10


def test_number_swapped_bounds():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "number", "minimum": 5.0, "maximum": 1.0})
    assert 1.0 <= value <= 5.0


def test_default_and_const():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate({"type": "string", "default": "d"}) == "d"
    assert gen.generate({"type": "string", "const": "c"}) == "c"


def test_all_of_merges_properties():
    gen = FakeDataGenerator(seed=1)
    schema = {
        "allOf": [
            {"properties": {"a": {"type": "integer"}}},
            {"properties": {"b": {"type": "boolean"}}},
        ]
    }
    result = gen.generate(schema)
    assert set(result) == {"a", "b"}


def test_one_of_picks_first():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"oneOf": [{"type": "integer"}, {"type": "string"}]})
    assert isinstance(result, int)


def test_any_of_picks_first():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"anyOf": [{"type": "boolean"}]})
    assert isinstance(result, bool)


def test_nullable_type_list():
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": ["null", "integer"]})
    assert isinstance(value, int)


def test_null_type_returns_none():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate({"type": "null"}) is None


def test_unknown_type_returns_none():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate({"type": "mystery"}) is None


def test_non_dict_schema_returns_none():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate("nope") is None


def test_implicit_object_from_properties():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"properties": {"x": {"type": "integer"}}})
    assert "x" in result


def test_implicit_array_from_items():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"items": {"type": "integer"}})
    assert isinstance(result, list)


def test_additional_properties_object():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate({"type": "object", "additionalProperties": {"type": "integer"}})
    assert "key" in result


def test_object_without_properties_is_empty():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate({"type": "object"}) == {}


def test_string_length_padding_and_truncation():
    gen = FakeDataGenerator(seed=1)
    long = gen.generate({"type": "string", "maxLength": 3})
    assert len(long) <= 3
    short = gen.generate({"type": "string", "minLength": 30})
    assert len(short) >= 30


def test_array_max_items_caps():
    gen = FakeDataGenerator(seed=1)
    result = gen.generate(
        {"type": "array", "items": {"type": "integer"}, "minItems": 9, "maxItems": 2}
    )
    assert len(result) == 2


@pytest.mark.parametrize(
    ("fmt",),
    [("hostname",), ("ipv4",), ("ipv6",), ("date",), ("time",), ("uri",), ("password",)],
)
def test_string_formats(fmt):
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "format": fmt})
    assert isinstance(value, str) and value


@pytest.mark.parametrize(
    ("field", "check"),
    [
        ("first_name", str.strip),
        ("last_name", str.strip),
        ("username", str.strip),
        ("phone_number", str.strip),
        ("home_address", str.strip),
        ("city", str.strip),
        ("country", str.strip),
        ("zip_code", str.strip),
        ("company_name", str.strip),
        ("website_url", str.strip),
        ("description", str.strip),
        ("favorite_color", str.strip),
        ("user_id", str.strip),
        ("full_title", str.strip),
    ],
)
def test_semantic_name_matches(field, check):
    gen = FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string"}, field_name=field)
    assert check(value)


def test_bad_pattern_falls_back(monkeypatch):
    import phantom_api.generators.fake_data as fd

    def boom(_pattern):
        raise ValueError("bad")

    monkeypatch.setattr(fd.exrex, "getone", boom)
    gen = fd.FakeDataGenerator(seed=1)
    value = gen.generate({"type": "string", "pattern": "[0-9]+"})
    assert isinstance(value, str)


def test_depth_guard_returns_none():
    gen = FakeDataGenerator(seed=1)
    assert gen.generate({"type": "object"}, _depth=100) is None


def test_generator_without_seed():
    gen = FakeDataGenerator()
    assert isinstance(gen.generate({"type": "integer"}), int)

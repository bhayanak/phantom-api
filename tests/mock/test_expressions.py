"""Tests for the expression evaluator and the remaining protocol edges.

The evaluator is a security boundary: template expressions come from a config
file, and the whole point of parsing to an AST is that a config cannot reach the
filesystem, the network, or the interpreter.
"""

from __future__ import annotations

import pytest

from phantom_api.mock.expressions import ExpressionError, evaluate


class Helper:
    """Stands in for the `fake` object a template is given."""

    value = 7

    def shout(self, text: str, times: int = 1) -> str:
        return (text.upper() + "!") * times

    def _private(self) -> str:  # pragma: no cover - must never be reachable
        return "secret"


@pytest.fixture
def ctx():
    return {
        "params": {"id": "42", "tags": ["a", "b"], "nested": {"deep": 1}},
        "headers": {"x-trace": "abc"},
        "count": 3,
        "helper": Helper(),
        "empty": None,
    }


# -- what must work ----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 + 2", 3),
        ("10 - count", 7),
        ("count * 2", 6),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("-count", -3),
        ("not count", False),
        ("count > 2 and count < 10", True),
        ("count > 5 or count == 3", True),
        ("1 < count <= 3", True),
        ("count if count > 1 else 0", 3),
        ("[1, count, 'x']", [1, 3, "x"]),
        ("{'a': count}", {"a": 3}),
        ("params['id']", "42"),
        ("params['tags'][1]", "b"),
        ("params.nested", {"deep": 1}),
        ("helper.value", 7),
        ("'a' + 'b'", "ab"),
    ],
)
def test_supported_expressions(source, expected, ctx):
    assert evaluate(source, ctx) == expected


def test_a_method_on_a_context_object_can_be_called(ctx):
    assert evaluate("helper.shout('hi')", ctx) == "HI!"


def test_keyword_arguments_are_passed_through(ctx):
    assert evaluate("helper.shout('hi', times=2)", ctx) == "HI!HI!"


def test_a_missing_key_is_none_rather_than_an_error(ctx):
    """A template that references an absent field should render, not crash."""
    assert evaluate("params['nope']", ctx) is None
    assert evaluate("params.nope", ctx) is None
    assert evaluate("empty['x']", ctx) is None


def test_an_unknown_name_is_refused(ctx):
    """A name that is not in the context is a typo in the config, not data.

    Missing *data* renders as nothing; a missing *name* should be loud, because
    silently rendering every misspelled field as empty hides the mistake.
    """
    with pytest.raises(ExpressionError, match="unknown name"):
        evaluate("whatever", ctx)


# -- what must be refused ----------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "().__class__",
        "helper._private()",
        "[x for x in [1, 2]]",
        "lambda: 1",
        "open('/etc/passwd')",
        "helper.value.__class__.__mro__",
        "1 if (yield) else 2",
    ],
)
def test_dangerous_constructs_are_refused(source, ctx):
    with pytest.raises(ExpressionError):
        evaluate(source, ctx)


def test_a_syntax_error_names_the_expression(ctx):
    with pytest.raises(ExpressionError, match="invalid expression"):
        evaluate("1 +", ctx)


def test_calling_something_that_is_not_callable_is_refused(ctx):
    with pytest.raises(ExpressionError, match="not callable"):
        evaluate("helper.value()", ctx)


def test_calling_a_bare_name_is_refused(ctx):
    """Only methods on context objects may be called."""
    with pytest.raises(ExpressionError):
        evaluate("count()", ctx)


def test_an_unsupported_unary_operator_is_refused(ctx):
    with pytest.raises(ExpressionError, match="unary"):
        evaluate("~count", ctx)


def test_an_unsupported_binary_operator_is_refused(ctx):
    with pytest.raises(ExpressionError):
        evaluate("count ** 2", ctx)


def test_an_unsupported_comparison_is_refused(ctx):
    with pytest.raises(ExpressionError, match="comparison"):
        evaluate("count is None", ctx)

"""A deliberately small, safe expression and template language.

Mock configuration is data that arrives from a file, so it must never become
code. Rather than sandboxing ``eval`` -- which is a losing game -- this walks a
parsed AST and refuses every node type it has not explicitly allowed. No
imports, no attribute access into dunders, no calls except to a fixed helper
table.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Callable
from typing import Any

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_HELPERS: dict[str, Callable[..., Any]] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "get": lambda obj, key, default=None: (
        obj.get(key, default) if isinstance(obj, dict) else default
    ),
    "join": lambda items, sep=",": sep.join(str(i) for i in items),
    "default": lambda value, fallback: fallback if value in (None, "") else value,
}

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a refused construct."""


def evaluate(source: str, context: dict[str, Any]) -> Any:
    """Evaluate a single expression against ``context``."""
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression {source!r}: {exc}") from exc
    return _eval(tree.body, context)


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise ExpressionError(f"unknown name {node.id!r}")
        return ctx[node.id]
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError(f"attribute {node.attr!r} is not accessible")
        target = _eval(node.value, ctx)
        if isinstance(target, dict):
            return target.get(node.attr)
        return getattr(target, node.attr, None)
    if isinstance(node, ast.Subscript):
        target = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        try:
            return target[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        raise ExpressionError("unsupported unary operator")
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, ctx) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if type(op) not in _COMPARE:
                raise ExpressionError("unsupported comparison")
            right = _eval(comparator, ctx)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, ctx) if _eval(node.test, ctx) else _eval(node.orelse, ctx)
    if isinstance(node, ast.Call):
        return _call(node, ctx)
    if isinstance(node, ast.List):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _eval(k, ctx): _eval(v, ctx)
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None
        }
    raise ExpressionError(f"{type(node).__name__} is not permitted in expressions")


def _call(node: ast.Call, ctx: dict[str, Any]) -> Any:
    args = [_eval(a, ctx) for a in node.args]
    kwargs = {kw.arg: _eval(kw.value, ctx) for kw in node.keywords if kw.arg}
    if isinstance(node.func, ast.Name):
        fn = _HELPERS.get(node.func.id)
        if fn is None:
            raise ExpressionError(f"unknown function {node.func.id!r}")
        return fn(*args, **kwargs)
    if isinstance(node.func, ast.Attribute):
        # Only calls onto objects already in the context, e.g. fake.email().
        target = _eval(node.func.value, ctx)
        name = node.func.attr
        if name.startswith("_"):
            raise ExpressionError(f"method {name!r} is not accessible")
        method = getattr(target, name, None)
        if not callable(method):
            raise ExpressionError(f"{name!r} is not callable")
        return method(*args, **kwargs)
    raise ExpressionError("unsupported call target")


def render(template: str, context: dict[str, Any]) -> str:
    """Substitute every ``{{ expression }}`` in ``template``."""

    def replace(match: re.Match[str]) -> str:
        value = evaluate(match.group(1), context)
        return "" if value is None else str(value)

    return _EXPR.sub(replace, template)

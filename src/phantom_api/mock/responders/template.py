"""Declarative canned responses with a dispatch strategy per operation.

The structure is the one SoapUI settled on -- operation, then a list of
responses, then a rule for choosing between them -- because it is the right one:
readable in a diff, obvious to edit, and enough for the long tail of operations
that need a plausible answer and nothing more.

    operations:
      GetAccountSummary:
        dispatch: match
        responses:
          - when: "params.accountId == '0'"
            fault: { kind: not-found, message: "Unknown account" }
          - body: "<balance>{{ fake.pyint(100, 9999) }}</balance>"
            delay_ms: 50
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from phantom_api.mock.expressions import ExpressionError, evaluate, render
from phantom_api.mock.protocols.xml_codec import Raw
from phantom_api.mock.types import MockError, NotHandled, Operation

DISPATCH_STRATEGIES = ("first", "sequence", "random", "match")


class TemplateResponder:
    name = "template"

    def __init__(self, operations: dict[str, Any], *, seed: int | None = None) -> None:
        self.operations = operations
        self._cursor: dict[str, int] = {}
        self._random = random.Random(seed)
        self._faker = _make_faker(seed)

    def respond(self, op: Operation) -> Any:
        spec = self.operations.get(op.name) or self.operations.get("*")
        if spec is None:
            raise NotHandled(f"no template for {op.name}")
        if isinstance(spec, str):
            spec = {"responses": [{"body": spec}]}

        responses = spec.get("responses") or []
        if not responses:
            raise NotHandled(f"template for {op.name} defines no responses")

        context = self._context(op)
        chosen = self._dispatch(op.name, spec.get("dispatch", "first"), responses, context)
        if chosen is None:
            raise NotHandled(f"no template response matched for {op.name}")

        if "fault" in chosen:
            fault = chosen["fault"] or {}
            raise MockError(
                render(str(fault.get("message", "mock fault")), context),
                kind=fault.get("kind", "internal"),
                status=int(fault.get("status", 500)),
                detail_type=fault.get("detail_type"),
                detail=fault.get("detail") or {},
            )

        if "json" in chosen:
            return _render_data(chosen["json"], context)
        body = chosen.get("body", "")
        # `body` is normally raw text for the wire. A mapping or a list means
        # the author wrote structured data, and stringifying it would emit a
        # Python repr -- single-quoted, and not valid JSON for any consumer.
        if isinstance(body, (dict, list)):
            return _render_data(body, context)
        return Raw(render(str(body), context))

    def _dispatch(
        self, key: str, strategy: str, responses: list[dict], context: dict[str, Any]
    ) -> dict | None:
        if strategy == "match":
            for response in responses:
                condition = response.get("when")
                if condition is None:
                    return response
                try:
                    if evaluate(str(condition), context):
                        return response
                except ExpressionError as exc:
                    raise MockError(f"bad 'when' expression: {exc}", kind="internal") from exc
            return None
        if strategy == "random":
            return self._random.choice(responses)
        if strategy == "sequence":
            index = self._cursor.get(key, 0)
            self._cursor[key] = index + 1
            return responses[index % len(responses)]
        return responses[0]

    def _context(self, op: Operation) -> dict[str, Any]:
        return {
            "params": op.params,
            "headers": op.headers,
            "operation": op.name,
            "session": op.session_id,
            "now": datetime.now(timezone.utc),
            "fake": self._faker,
            "random": self._random,
        }

    def describe(self) -> str:
        return f"template({len(self.operations)} operations)"


def _render_data(node: Any, context: dict[str, Any]) -> Any:
    if isinstance(node, str):
        return render(node, context)
    if isinstance(node, dict):
        return {k: _render_data(v, context) for k, v in node.items()}
    if isinstance(node, list):
        return [_render_data(v, context) for v in node]
    return node


def _make_faker(seed: int | None) -> Any:
    from faker import Faker

    faker = Faker()
    if seed is not None:
        Faker.seed(seed)
    return faker

"""Detect when a corpus has stopped matching reality.

A mock nobody notices has gone stale is worse than no mock: it passes tests that
would fail against the real system. This replays a recorded corpus against the
live endpoint and reports where the two disagree.

Read-only against the upstream by design -- it sends exactly the requests that
were already recorded, and nothing else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

#: Values that legitimately differ on every call and are not drift.
_LIST_SAMPLE = 8

_VOLATILE = re.compile(
    r"<(?:currentTime|serverClock|createdTime|loginTime|lastActiveTime|token|key"
    r"|sessionId|chainId)>[^<]*</\w+>"
    r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


@dataclass
class Divergence:
    operation: str
    kind: str
    detail: str


@dataclass
class DriftReport:
    checked: int = 0
    matched: int = 0
    diverged: list[Divergence] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """A run that checked nothing has not shown the corpus to be current."""
        return self.checked > 0 and not self.diverged

    def summary(self) -> str:
        return (
            f"{self.checked} checked, {self.matched} match, "
            f"{len(self.diverged)} diverged, {len(self.skipped)} skipped"
        )


def _shape(text: str) -> str:
    """Element and field names only, with volatile values removed.

    Comparing bodies verbatim reports drift on every timestamp. What matters is
    whether the *structure* the client parses has changed.
    """
    stripped = _VOLATILE.sub("", text)
    if stripped.lstrip().startswith("{"):
        try:
            return " ".join(sorted(_json_keys(json.loads(stripped))))
        except ValueError:
            pass
    return " ".join(re.findall(r"<([A-Za-z_][\w.:-]*)", stripped))


def _json_keys(node: object, prefix: str = "") -> list[str]:
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            out.append(f"{prefix}{key}")
            out.extend(_json_keys(value, f"{prefix}{key}."))
        return out
    if isinstance(node, list) and node:
        # Lists are often heterogeneous -- optional fields appear on some
        # members and not others. Reading only the first reports a difference
        # every time the two runs happen to order them differently.
        keys: list[str] = []
        for item in node[:_LIST_SAMPLE]:
            keys.extend(_json_keys(item, prefix))
        return sorted(set(keys))
    return []


def check_drift(
    corpus: Path,
    target: str,
    *,
    verify: bool = False,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> DriftReport:
    """Replay every recorded request against ``target`` and compare shapes.

    Pass ``client`` to drive an in-process application instead of a network
    endpoint.
    """
    # Imported here: the corpus responder reads fingerprints from this package,
    # so importing it at module scope would close a cycle.
    from phantom_api.mock.responders.corpus import CorpusResponder

    responder = CorpusResponder(corpus)
    report = DriftReport()
    base = target.rstrip("/")

    owned = client is None
    active = client or httpx.Client(verify=verify, timeout=timeout)
    try:
        _replay(active, base, responder, report, headers or {})
    finally:
        if owned:
            active.close()
    return report


def _replay(
    client: httpx.Client,
    base: str,
    responder: object,
    report: DriftReport,
    headers: dict[str, str],
) -> None:
    # The same request can be recorded more than once with different outcomes --
    # a login captured both succeeding and failing, say. Group the variants and
    # accept the live answer if it matches any of them, otherwise every such
    # pair reports as drift forever. Sending each distinct request once is also
    # the polite thing to do to the system on the other end.
    variants: dict[tuple[str, str, str], list[Any]] = {}
    for exchange in responder.exchanges:  # type: ignore[attr-defined]
        if not exchange.endpoint_path:
            report.skipped.append(exchange.label)
            continue
        key = (exchange.method, exchange.endpoint_path, exchange.request_body)
        variants.setdefault(key, []).append(exchange)

    for (method, path, body), recorded in variants.items():
        first = recorded[0]
        content_type = (
            "application/json"
            if first.content_type.startswith("application/json")
            else "text/xml; charset=utf-8"
        )
        try:
            live = client.request(
                method,
                f"{base}{path}",
                content=body.encode() if body else None,
                headers={"content-type": content_type, **headers},
            )
        except httpx.HTTPError as exc:
            report.diverged.append(Divergence(first.operation, "unreachable", str(exc)))
            report.checked += 1
            continue

        report.checked += 1
        same_status = [e for e in recorded if e.status == live.status_code]
        if not same_status:
            expected = ", ".join(str(e.status) for e in recorded)
            report.diverged.append(
                Divergence(
                    first.operation,
                    "status",
                    f"recorded {expected}, live {live.status_code}",
                )
            )
            continue

        live_shape = _shape(live.text)
        if any(_shape(e.response_body) == live_shape for e in same_status):
            report.matched += 1
            continue

        recorded_fields = set(_shape(same_status[0].response_body).split())
        live_fields = set(live_shape.split())
        added = sorted(live_fields - recorded_fields)[:5]
        removed = sorted(recorded_fields - live_fields)[:5]
        detail = []
        if removed:
            detail.append(f"gone: {', '.join(removed)}")
        if added:
            detail.append(f"new: {', '.join(added)}")
        report.diverged.append(
            Divergence(first.operation, "shape", "; ".join(detail) or "reordered")
        )

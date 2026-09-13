"""Request normalisation and fingerprinting for corpus matching.

A verbatim body hash matches almost nothing, because real requests carry session
identifiers, timestamps and server-issued tokens that differ on every call.
Normalisation removes exactly those before hashing, and records what it removed
so a replayed response can be re-templated with the live request's values.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

#: Values that vary per call and must not influence the fingerprint.
_VOLATILE = [
    ("session", re.compile(r"(<vcSessionCookie>)[^<]*(</vcSessionCookie>)")),
    ("session", re.compile(r"(session[_-]?id[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9\-]{8,}")),
    ("timestamp", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")),
    ("token", re.compile(r"(<token>)[^<]*(</token>)")),
    (
        "uuid",
        re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        ),
    ),
]

_WHITESPACE = re.compile(r">\s+<")


@dataclass(slots=True)
class Fingerprint:
    """A normalised request plus the volatile values that were stripped."""

    operation: str
    digest: str
    parameter_digest: str
    stripped: dict[str, list[str]] = field(default_factory=dict)

    def keys(self) -> list[str]:
        """Match keys, most specific first."""
        return [
            f"{self.operation}|{self.digest}",
            f"{self.operation}|{self.parameter_digest}",
            self.operation,
        ]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def normalise(body: str) -> tuple[str, dict[str, list[str]]]:
    """Strip volatile values, returning the stable text and what was removed."""
    stripped: dict[str, list[str]] = {}
    text = _WHITESPACE.sub("><", body.strip())
    for label, pattern in _VOLATILE:
        found = [m.group(0) for m in pattern.finditer(text)]
        if found:
            stripped.setdefault(label, []).extend(found)
        if pattern.groups >= 2:
            text = pattern.sub(rf"\1<{label}>\2", text)
        else:
            text = pattern.sub(f"<{label}>", text)
    return text, stripped


def _structural(text: str) -> str:
    """Element and attribute names only, ignoring every value.

    This is the loosest useful key: it matches a request of the same *shape*
    even when the caller asked about different objects.
    """
    tags = re.findall(r"<([A-Za-z_][\w.:-]*)", text)
    return " ".join(tags)


def fingerprint(operation: str, body: str) -> Fingerprint:
    normalised, stripped = normalise(body)
    return Fingerprint(
        operation=operation,
        digest=_hash(normalised),
        parameter_digest=_hash(_structural(normalised)),
        stripped=stripped,
    )

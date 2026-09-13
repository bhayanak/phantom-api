"""Sessions and server-side cursors.

Both are things a stateful client creates and then relies on the server to
remember. Both are also things clients leak, so both are bounded: sessions
expire on idle, cursors evict least-recently-used. Growing without limit is how
a mock becomes the flakiest part of a test suite.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

DEFAULT_IDLE_TIMEOUT = 30 * 60.0
MAX_SESSIONS = 512
MAX_CURSORS = 1024


@dataclass
class Session:
    id: str
    created_at: float
    last_seen: float
    authenticated: bool = False
    username: str | None = None
    locale: str = "en_US"
    data: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Issue, look up and expire sessions."""

    def __init__(
        self, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT, max_sessions: int = MAX_SESSIONS
    ) -> None:
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def create(self, *, authenticated: bool = False, username: str | None = None) -> Session:
        self._evict()
        now = time.time()
        session = Session(
            id=secrets.token_hex(16).upper(),
            created_at=now,
            last_seen=now,
            authenticated=authenticated,
            username=username,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session.last_seen > self.idle_timeout:
            self._sessions.pop(session_id, None)
            return None
        session.last_seen = time.time()
        self._sessions.move_to_end(session_id)
        return session

    def destroy(self, session_id: str | None) -> bool:
        return self._sessions.pop(session_id or "", None) is not None

    def _evict(self) -> None:
        cutoff = time.time() - self.idle_timeout
        for key in [k for k, s in self._sessions.items() if s.last_seen < cutoff]:
            self._sessions.pop(key, None)
        while len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)

    def __len__(self) -> int:
        return len(self._sessions)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id[:8] + "...",
                "authenticated": s.authenticated,
                "username": s.username,
                "age_seconds": round(time.time() - s.created_at, 1),
                "idle_seconds": round(time.time() - s.last_seen, 1),
            }
            for s in self._sessions.values()
        ]


@dataclass
class Cursor:
    """A server-side iteration handle over a materialised result."""

    token: str
    kind: str
    items: list[Any]
    position: int = 0
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def take(self, count: int) -> list[Any]:
        chunk = self.items[self.position : self.position + count]
        self.position += len(chunk)
        return chunk

    @property
    def exhausted(self) -> bool:
        return self.position >= len(self.items)

    def reset(self) -> None:
        self.position = 0


class CursorStore:
    """Bounded cursor storage with least-recently-used eviction.

    Clients routinely create these and never destroy them; the real systems cap
    them and fault past the limit. Mirroring that is optional
    (``strict_limit``), but never growing without bound is not.
    """

    def __init__(self, *, max_cursors: int = MAX_CURSORS, strict_limit: int | None = None) -> None:
        self.max_cursors = max_cursors
        self.strict_limit = strict_limit
        self._cursors: OrderedDict[str, Cursor] = OrderedDict()

    def create(self, kind: str, items: list[Any], *, session_id: str | None = None) -> Cursor:
        if self.strict_limit is not None:
            live = sum(1 for c in self._cursors.values() if c.session_id == session_id)
            if live >= self.strict_limit:
                raise CursorLimitReached(
                    f"session already holds {live} {kind} cursors (limit {self.strict_limit})"
                )
        while len(self._cursors) >= self.max_cursors:
            self._cursors.popitem(last=False)
        cursor = Cursor(token=secrets.token_hex(12), kind=kind, items=items, session_id=session_id)
        self._cursors[cursor.token] = cursor
        return cursor

    def get(self, token: str | None) -> Cursor | None:
        if not token:
            return None
        cursor = self._cursors.get(token)
        if cursor is not None:
            self._cursors.move_to_end(token)
        return cursor

    def destroy(self, token: str | None) -> bool:
        return self._cursors.pop(token or "", None) is not None

    def __len__(self) -> int:
        return len(self._cursors)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "token": c.token[:8] + "...",
                "kind": c.kind,
                "remaining": len(c.items) - c.position,
                "age_seconds": round(time.time() - c.created_at, 1),
            }
            for c in self._cursors.values()
        ]


class CursorLimitReached(RuntimeError):
    """Raised when strict cursor limits are enabled and a client exceeds them."""

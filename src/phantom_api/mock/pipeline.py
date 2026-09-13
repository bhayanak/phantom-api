"""What happens around a response: delay, deliberate misbehaviour, and the log.

Chaos lives here rather than in any responder, so a corpus replay and a
simulated model can be made to misbehave identically.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from phantom_api.mock.config import ChaosConfig
from phantom_api.mock.types import MockError, Operation

MAX_LOG_ENTRIES = 5000


@dataclass(slots=True)
class LogEntry:
    at: float
    operation: str
    protocol: str
    path: str
    responder: str
    status: int
    duration_ms: float
    bytes: int
    note: str = ""


class ConnectionDropped(Exception):
    """Raised to close the connection without a response, on purpose."""


class Pipeline:
    """Applies chaos, times the call, and keeps the request log."""

    def __init__(self, chaos: ChaosConfig, *, seed: int | None = None) -> None:
        self.chaos = chaos
        self._random = random.Random(seed)
        self.log: deque[LogEntry] = deque(maxlen=MAX_LOG_ENTRIES)
        self.counts: dict[str, int] = {}
        self.total = 0

    def before(self, op: Operation) -> None:
        """Delay and fault injection, before the responder runs."""
        if self.chaos.drop_rate and self._random.random() < self.chaos.drop_rate:
            raise ConnectionDropped(f"chaos dropped {op.name}")
        delay = self.chaos.latency_ms
        if self.chaos.latency_jitter_ms:
            delay += self._random.randint(0, self.chaos.latency_jitter_ms)
        if delay > 0:
            time.sleep(delay / 1000.0)
        if self.chaos.fault_rate and self._random.random() < self.chaos.fault_rate:
            raise MockError(
                f"chaos fault injected for {op.name}",
                kind=self.chaos.fault_kind,
                detail_type="ChaosInjected",
            )

    def record(
        self,
        op: Operation,
        responder: str,
        status: int,
        started: float,
        size: int,
        note: str = "",
    ) -> None:
        self.total += 1
        self.counts[op.name] = self.counts.get(op.name, 0) + 1
        self.log.append(
            LogEntry(
                at=time.time(),
                operation=op.name,
                protocol=op.protocol,
                path=op.request.path if op.request else "",
                responder=responder,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                bytes=size,
                note=note,
            )
        )

    def entries(self, limit: int = 100) -> list[dict[str, Any]]:
        return [asdict(entry) for entry in list(self.log)[-limit:]]

    def verify(self, operation: str, *, at_least: int = 1, at_most: int | None = None) -> bool:
        """Assert how many times an operation arrived. Turns the mock into an oracle."""
        seen = self.counts.get(operation, 0)
        if seen < at_least:
            return False
        return not (at_most is not None and seen > at_most)


@dataclass
class Stats:
    started_at: float = field(default_factory=time.time)

    def uptime(self) -> float:
        return round(time.time() - self.started_at, 3)

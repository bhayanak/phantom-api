"""Mock any protocol: SOAP, JSON-RPC, REST, stateful or replayed.

Four responders answer requests, ordered by fidelity against effort:

    forward   proxy the real system, optionally recording it
    corpus    replay recorded traffic, wire-accurate with no modelling
    template  declarative canned responses with a dispatch strategy
    model     a domain model that stays self-consistent and evolves

They compose: serve most operations from a corpus, synthesise the rest from a
model, forward anything unknown, and record it as you go.
"""

from __future__ import annotations

from phantom_api.mock.config import ConfigError, MockConfig, load_config
from phantom_api.mock.engine import MockEngine, create_mock_app
from phantom_api.mock.types import MockError, NotHandled, Operation, RawRequest, RawResponse

__all__ = [
    "ConfigError",
    "MockConfig",
    "MockEngine",
    "MockError",
    "NotHandled",
    "Operation",
    "RawRequest",
    "RawResponse",
    "create_mock_app",
    "load_config",
]

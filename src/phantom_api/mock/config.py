"""Declarative mock configuration.

One YAML file describes the whole mock: which protocol speaks on which paths,
which responder answers which operations, and what to do when things should go
wrong. Rules are evaluated in order and the first match wins, which is the same
mental model as an nginx or Envoy config.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from phantom_api.constants import DEFAULT_HOST

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised when a configuration file is unusable."""


class SessionRule(BaseModel):
    """Where a protocol carries its session token on a given path."""

    transport: Literal["cookie", "header", "soap-header", "none"] = "cookie"
    name: str = "session"


class ProtocolConfig(BaseModel):
    pack: str = Field(..., description="Protocol pack name: soap, jsonrpc, openapi, raw.")
    paths: list[str] = Field(default_factory=lambda: ["/**"])
    spec: Path | None = Field(default=None, description="OpenAPI spec, for the openapi pack.")
    session: dict[str, SessionRule] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class MatchRule(BaseModel):
    """Which requests a responder rule applies to."""

    operation: list[str] = Field(default_factory=list)
    protocol: str | None = None
    path: str | None = None

    @field_validator("operation", mode="before")
    @classmethod
    def _listify(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, str) else value


class ResponderRule(BaseModel):
    match: MatchRule = Field(default_factory=MatchRule)
    responder: Literal["corpus", "template", "model", "forward"] = "corpus"
    corpus: Path | None = None
    on_miss: Literal["synthesize", "fault", "forward", "not-found"] = "fault"
    target: str | None = Field(default=None, description="Upstream base URL for forward.")
    operations: dict[str, Any] = Field(default_factory=dict)
    record_to: Path | None = None

    @field_validator("match", mode="before")
    @classmethod
    def _wildcard(cls, value: Any) -> Any:
        return {} if value in ("*", None) else value


class ListenConfig(BaseModel):
    host: str = DEFAULT_HOST
    port: int = 8443
    tls: Literal["self-signed", "off"] | None = "off"
    tls_cert: Path | None = None
    tls_key: Path | None = None
    advertise_host: str | None = None
    advertise_port: int | None = None


class ChaosConfig(BaseModel):
    """Deliberate misbehaviour. Everything here is off unless asked for."""

    latency_ms: int = 0
    latency_jitter_ms: int = 0
    fault_rate: float = 0.0
    fault_kind: str = "internal"
    drop_rate: float = 0.0

    @field_validator("fault_rate", "drop_rate")
    @classmethod
    def _probability(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("must be between 0.0 and 1.0")
        return value


class ModelConfig(BaseModel):
    pack: str | None = Field(default=None, description="Domain pack providing projections.")
    scenario: Path | None = None
    seed: int | None = None


class ControlPlaneConfig(BaseModel):
    enabled: bool = True
    bind: str = DEFAULT_HOST
    prefix: str = "/__phantom"


class MockConfig(BaseModel):
    """The whole mock, in one object."""

    name: str = "phantom-mock"
    listen: ListenConfig = Field(default_factory=ListenConfig)
    protocols: list[ProtocolConfig] = Field(default_factory=list)
    responders: list[ResponderRule] = Field(default_factory=list)
    model: ModelConfig = Field(default_factory=ModelConfig)
    chaos: ChaosConfig = Field(default_factory=ChaosConfig)
    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)
    #: Resolved relative to the config file, so a config can be moved wholesale.
    base_dir: Path = Field(default_factory=Path.cwd, exclude=True)

    def resolve(self, path: Path | None) -> Path | None:
        if path is None:
            return None
        return path if path.is_absolute() else (self.base_dir / path).resolve()


def _interpolate(node: Any) -> Any:
    """Replace ``${VAR}`` with the environment value, refusing unset names.

    Secrets belong in the environment. Failing loudly on an unset variable is
    better than starting a mock that silently authenticates against ``${...}``.
    """
    if isinstance(node, str):

        def sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"environment variable {name} is referenced but not set")
            return os.environ[name]

        return _ENV_REF.sub(sub, node)
    if isinstance(node, dict):
        return {key: _interpolate(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_interpolate(item) for item in node]
    return node


def load_config(path: Path) -> MockConfig:
    """Load and validate a mock configuration from YAML."""
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    body = data.get("mock", data)
    body = _interpolate(body)
    body["base_dir"] = path.resolve().parent
    try:
        return MockConfig.model_validate(body)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"invalid configuration in {path}: {exc}") from exc


def corpus_only_config(corpus: Path, *, host: str, port: int, tls: str) -> MockConfig:
    """The zero-config case: replay a corpus and nothing else."""
    return MockConfig(
        name=f"replay:{corpus.name}",
        listen=ListenConfig(host=host, port=port, tls=tls),  # type: ignore[arg-type]
        protocols=[ProtocolConfig(pack="auto", paths=["/**"])],
        responders=[ResponderRule(responder="corpus", corpus=corpus, on_miss="fault")],
        base_dir=corpus.resolve().parent,
    )

"""Persist recorded HTTP interactions to disk in a path-traversal-safe manner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Interaction(BaseModel):
    """A single recorded request/response pair."""

    method: str
    path: str
    query: str = ""
    status_code: int = 200
    response_headers: dict[str, str] = Field(default_factory=dict)
    content_type: str = "application/json"
    body: Any = None

    def key(self) -> str:
        """Stable identity for this interaction (method + path + query)."""
        return f"{self.method.upper()} {self.path}?{self.query}"

    def filename(self) -> str:
        digest = hashlib.sha256(self.key().encode("utf-8")).hexdigest()[:16]
        return f"{self.method.upper()}_{digest}.json"


class RecordingStorage:
    """Read and write :class:`Interaction` records under a fixed output directory."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).resolve()

    def _ensure_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _safe_target(self, filename: str) -> Path:
        # Filenames are always hash-based; still verify containment defensively.
        target = (self.output_dir / filename).resolve()
        if self.output_dir not in target.parents and target != self.output_dir:
            raise ValueError(f"Refusing to write outside recordings directory: {filename}")
        return target

    def save(self, interaction: Interaction) -> Path:
        self._ensure_dir()
        target = self._safe_target(interaction.filename())
        target.write_text(
            json.dumps(interaction.model_dump(), indent=2, default=str), encoding="utf-8"
        )
        return target

    def load_all(self) -> list[Interaction]:
        if not self.output_dir.exists():
            return []
        interactions: list[Interaction] = []
        for file in sorted(self.output_dir.glob("*.json")):
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
                interactions.append(Interaction.model_validate(raw))
            except (json.JSONDecodeError, ValueError):
                continue
        return interactions

    def count(self) -> int:
        if not self.output_dir.exists():
            return 0
        return sum(1 for _ in self.output_dir.glob("*.json"))

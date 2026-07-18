"""Base parser abstraction and shared file-loading helpers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from phantom_api.constants import MAX_SPEC_BYTES
from phantom_api.models import MockSpec


class ParserError(Exception):
    """Raised when a spec cannot be parsed."""


class BaseParser(ABC):
    """Abstract base for all spec parsers.

    Subclasses implement :meth:`can_parse` (cheap detection) and :meth:`parse`
    (full conversion to a :class:`MockSpec`).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _validate_file(self) -> None:
        if not self.path.exists():
            raise ParserError(f"File not found: {self.path}")
        if not self.path.is_file():
            raise ParserError(f"Not a file: {self.path}")
        size = self.path.stat().st_size
        if size > MAX_SPEC_BYTES:
            raise ParserError(
                f"Spec file too large ({size} bytes); limit is {MAX_SPEC_BYTES} bytes."
            )

    def load_raw(self) -> Any:
        """Load the file as JSON or YAML into a Python object."""
        self._validate_file()
        text = self.path.read_text(encoding="utf-8")
        suffix = self.path.suffix.lower()
        try:
            if suffix in {".yaml", ".yml"}:
                return yaml.safe_load(text)
            if suffix == ".json":
                return json.loads(text)
            # Unknown extension: try JSON first, then YAML.
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return yaml.safe_load(text)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ParserError(f"Failed to parse {self.path}: {exc}") from exc

    def can_parse(self) -> bool:
        """Cheaply determine whether this parser can handle the file.

        Never raises; returns ``False`` on any error.
        """
        try:
            data = self.load_raw()
        except ParserError:
            return False
        return self._matches(data)

    @abstractmethod
    def _matches(self, data: Any) -> bool:
        """Return ``True`` if ``data`` looks like this parser's format."""

    @abstractmethod
    def parse(self) -> MockSpec:
        """Parse the file into a :class:`MockSpec`."""

"""Spec parsers that convert source formats into a :class:`~phantom_api.models.MockSpec`."""

from phantom_api.parsers.base import BaseParser, ParserError
from phantom_api.parsers.json_parser import JSONParser
from phantom_api.parsers.openapi_parser import OpenAPIParser
from phantom_api.parsers.postman_parser import PostmanParser

__all__ = [
    "BaseParser",
    "JSONParser",
    "OpenAPIParser",
    "ParserError",
    "PostmanParser",
    "detect_and_parse",
    "get_parser",
]

_PARSERS: dict[str, type[BaseParser]] = {
    "openapi": OpenAPIParser,
    "swagger": OpenAPIParser,
    "json": JSONParser,
    "postman": PostmanParser,
}


def get_parser(source_type: str) -> type[BaseParser]:
    """Return the parser class for an explicit ``source_type``."""
    try:
        return _PARSERS[source_type.lower()]
    except KeyError as exc:
        valid = ", ".join(sorted(_PARSERS))
        raise ParserError(f"Unknown source type {source_type!r}. Valid types: {valid}.") from exc


def detect_and_parse(path: str, source_type: str | None = None):
    """Detect the appropriate parser for ``path`` (or use ``source_type``) and parse it.

    Returns a :class:`~phantom_api.models.MockSpec`.
    """
    if source_type is not None:
        return get_parser(source_type)(path).parse()

    for parser_cls in (OpenAPIParser, PostmanParser, JSONParser):
        parser = parser_cls(path)
        if parser.can_parse():
            return parser.parse()

    raise ParserError(
        f"Could not auto-detect a supported format for {path!r}. "
        "Pass --type openapi|json|postman explicitly."
    )

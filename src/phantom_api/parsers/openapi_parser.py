"""Parse OpenAPI 3.x and Swagger 2.0 specifications into a :class:`MockSpec`.

Uses a self-contained ``$ref`` resolver rather than delegating to a strict validator, so
that slightly-off-spec but usable documents still produce a working mock server.
"""

from __future__ import annotations

from typing import Any

from phantom_api.constants import MAX_ROUTES
from phantom_api.models import MockRoute, MockSpec
from phantom_api.parsers.base import BaseParser, ParserError

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


class OpenAPIParser(BaseParser):
    """Parser for OpenAPI 3.x and Swagger 2.0 documents."""

    def _matches(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        return ("openapi" in data or "swagger" in data) and "paths" in data

    def parse(self) -> MockSpec:
        data = self.load_raw()
        if not isinstance(data, dict):
            raise ParserError("OpenAPI spec must be a mapping at the top level.")

        is_swagger = "swagger" in data and "openapi" not in data
        source_type = "swagger" if is_swagger else "openapi"

        info = data.get("info") or {}
        spec = MockSpec(
            title=str(info.get("title", "phantom-api mock")),
            version=str(info.get("version", "1.0.0")),
            source_type=source_type,
        )

        paths = data.get("paths") or {}
        if not isinstance(paths, dict):
            raise ParserError("`paths` must be a mapping.")

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                route = self._build_route(
                    path=path,
                    method=method,
                    operation=operation,
                    root=data,
                    is_swagger=is_swagger,
                )
                if route is not None:
                    spec.routes.append(route)
                if len(spec.routes) > MAX_ROUTES:
                    raise ParserError(f"Spec exceeds the maximum of {MAX_ROUTES} routes.")

        return spec

    def _build_route(
        self,
        *,
        path: str,
        method: str,
        operation: dict[str, Any],
        root: dict[str, Any],
        is_swagger: bool,
    ) -> MockRoute | None:
        responses = operation.get("responses") or {}
        status_code, response_def = self._pick_response(responses)

        schema, example, content_type = self._extract_response_body(
            response_def, root=root, is_swagger=is_swagger
        )

        return MockRoute(
            method=method.upper(),
            path=path,
            status_code=status_code,
            summary=str(operation.get("summary") or operation.get("operationId") or ""),
            response_schema=schema,
            response_example=example,
            content_type=content_type,
        )

    @staticmethod
    def _pick_response(responses: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Choose the most representative success response (lowest 2xx, else default)."""
        success_codes = sorted(
            code for code in responses if str(code).isdigit() and 200 <= int(code) < 300
        )
        if success_codes:
            code = success_codes[0]
            return int(code), responses.get(code) or {}
        if "default" in responses:
            return 200, responses.get("default") or {}
        # Fall back to the first declared response, whatever it is.
        for code, definition in responses.items():
            if str(code).isdigit():
                return int(code), definition or {}
        return 200, {}

    def _extract_response_body(
        self, response_def: dict[str, Any], *, root: dict[str, Any], is_swagger: bool
    ) -> tuple[dict[str, Any] | None, Any, str]:
        if not isinstance(response_def, dict):
            return None, None, "application/json"

        if is_swagger:
            schema = response_def.get("schema")
            example = response_def.get("examples", {}).get("application/json")
            resolved = self._resolve_ref(schema, root) if schema else None
            return resolved, example, "application/json"

        # OpenAPI 3.x uses a `content` map keyed by media type.
        content = response_def.get("content") or {}
        if not isinstance(content, dict) or not content:
            return None, None, "application/json"

        media_type = "application/json" if "application/json" in content else next(iter(content))
        media_obj = content.get(media_type) or {}
        schema = media_obj.get("schema")
        example = media_obj.get("example")
        if example is None:
            examples = media_obj.get("examples") or {}
            if isinstance(examples, dict) and examples:
                first = next(iter(examples.values()))
                if isinstance(first, dict):
                    example = first.get("value")
        resolved = self._resolve_ref(schema, root) if schema else None
        return resolved, example, media_type

    def _resolve_ref(
        self, schema: Any, root: dict[str, Any], _seen: frozenset[str] = frozenset()
    ) -> Any:
        """Recursively resolve ``$ref`` pointers, guarding against cycles."""
        if isinstance(schema, dict):
            if "$ref" in schema:
                ref = schema["$ref"]
                if not isinstance(ref, str) or ref in _seen:
                    return {}
                target = self._lookup_pointer(ref, root)
                return self._resolve_ref(target, root, _seen | {ref})
            return {key: self._resolve_ref(value, root, _seen) for key, value in schema.items()}
        if isinstance(schema, list):
            return [self._resolve_ref(item, root, _seen) for item in schema]
        return schema

    @staticmethod
    def _lookup_pointer(ref: str, root: dict[str, Any]) -> Any:
        if not ref.startswith("#/"):
            # External refs are not supported for safety; treat as empty schema.
            return {}
        node: Any = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return {}
        return node

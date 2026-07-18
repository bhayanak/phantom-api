"""Parse a Postman Collection v2.1 into mock routes.

Folders (nested ``item`` arrays) are walked recursively. Saved example responses are used
when present; otherwise a route is still created so the endpoint responds.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from phantom_api.constants import MAX_ROUTES
from phantom_api.models import MockRoute, MockSpec
from phantom_api.parsers.base import BaseParser, ParserError


class PostmanParser(BaseParser):
    """Parser for Postman Collection v2.1 exports."""

    def _matches(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        info = data.get("info")
        if not isinstance(info, dict):
            return False
        schema = str(info.get("schema", ""))
        return "getpostman.com" in schema or ("item" in data and "name" in info)

    def parse(self) -> MockSpec:
        data = self.load_raw()
        if not isinstance(data, dict):
            raise ParserError("Postman collection must be a mapping at the top level.")

        info = data.get("info") or {}
        spec = MockSpec(
            title=str(info.get("name", "phantom-api mock")),
            version="1.0.0",
            source_type="postman",
        )

        items = data.get("item")
        if not isinstance(items, list):
            raise ParserError("Postman collection is missing an `item` array.")

        for item in items:
            self._walk(item, spec)
            if len(spec.routes) > MAX_ROUTES:
                raise ParserError(f"Collection exceeds the maximum of {MAX_ROUTES} routes.")

        if not spec.routes:
            raise ParserError("No requests found in the Postman collection.")
        return spec

    def _walk(self, item: Any, spec: MockSpec) -> None:
        if not isinstance(item, dict):
            return
        if isinstance(item.get("item"), list):
            for child in item["item"]:
                self._walk(child, spec)
            return
        request = item.get("request")
        if isinstance(request, dict):
            route = self._build_route(item, request)
            if route is not None:
                spec.routes.append(route)

    def _build_route(self, item: dict[str, Any], request: dict[str, Any]) -> MockRoute | None:
        method = str(request.get("method", "GET")).upper()
        path = self._extract_path(request.get("url"))
        if path is None:
            return None

        status_code, example, headers, content_type = self._extract_example(item)

        return MockRoute(
            method=method,
            path=path,
            status_code=status_code,
            summary=str(item.get("name", "")),
            response_schema=None,
            response_example=example,
            response_headers=headers,
            content_type=content_type,
        )

    @staticmethod
    def _extract_path(url: Any) -> str | None:
        if url is None:
            return None
        if isinstance(url, str):
            raw = url
        elif isinstance(url, dict):
            path = url.get("path")
            if isinstance(path, list):
                segments = [PostmanParser._segment(p) for p in path]
                return "/" + "/".join(s for s in segments if s)
            raw = str(url.get("raw", ""))
        else:
            return None

        # Strip Postman variables and parse the path portion of the raw URL.
        raw = raw.replace("{{", "").replace("}}", "")
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        path_part = parsed.path or "/"
        return path_part if path_part.startswith("/") else f"/{path_part}"

    @staticmethod
    def _segment(part: Any) -> str:
        text = str(part)
        # Postman path variables use a leading ``:``; convert to FastAPI ``{name}``.
        if text.startswith(":"):
            return f"{{{text[1:]}}}"
        return text

    @staticmethod
    def _extract_example(
        item: dict[str, Any],
    ) -> tuple[int, Any, dict[str, str], str]:
        responses = item.get("response")
        if not isinstance(responses, list) or not responses:
            return 200, None, {}, "application/json"

        first = responses[0]
        if not isinstance(first, dict):
            return 200, None, {}, "application/json"

        status_code = int(first.get("code", 200) or 200)
        headers: dict[str, str] = {}
        content_type = "application/json"
        for header in first.get("header") or []:
            if isinstance(header, dict):
                key = str(header.get("key", ""))
                value = str(header.get("value", ""))
                if key.lower() == "content-type":
                    content_type = value.split(";")[0].strip() or content_type
                elif key:
                    headers[key] = value

        body = first.get("body")
        example: Any = None
        if isinstance(body, str) and body.strip():
            try:
                example = json.loads(body)
            except json.JSONDecodeError:
                example = body
                content_type = "text/plain"

        return status_code, example, headers, content_type

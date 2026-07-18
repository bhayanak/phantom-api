"""Watch the source spec file and rebuild routes on change (hot reload)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI
from watchfiles import awatch

from phantom_api.parsers import detect_and_parse
from phantom_api.parsers.base import ParserError
from phantom_api.server.app import rebuild_routes


def attach_hot_reload(
    app: FastAPI, spec_path: str | Path, source_type: str | None, on_reload=None
) -> None:
    """Register a background watcher that reparses ``spec_path`` on change.

    ``on_reload`` is an optional callback invoked with a status string.
    """
    spec_path = Path(spec_path)
    watcher_task: dict[str, asyncio.Task] = {}

    async def _watch() -> None:
        async for _changes in awatch(str(spec_path)):
            try:
                new_spec = detect_and_parse(str(spec_path), source_type)
            except ParserError as exc:
                if on_reload:
                    on_reload(f"reload failed: {exc}")
                continue
            rebuild_routes(app, new_spec)
            if on_reload:
                on_reload(f"reloaded {new_spec.route_count()} routes from {spec_path.name}")

    @app.on_event("startup")
    async def _start_watcher() -> None:  # pragma: no cover - requires running loop.
        watcher_task["task"] = asyncio.create_task(_watch())

    @app.on_event("shutdown")
    async def _stop_watcher() -> None:  # pragma: no cover - requires running loop.
        task = watcher_task.get("task")
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

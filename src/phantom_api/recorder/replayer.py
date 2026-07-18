"""Replay recorded interactions as a mock server."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from phantom_api.models import MockRoute, MockSpec
from phantom_api.recorder.storage import RecordingStorage
from phantom_api.server.app import ServerConfig, create_app


def build_replay_spec(recordings_dir: str | Path) -> MockSpec:
    """Turn a directory of recordings into a :class:`MockSpec`."""
    storage = RecordingStorage(recordings_dir)
    interactions = storage.load_all()

    spec = MockSpec(
        title="phantom-api replay",
        version="1.0.0",
        source_type="recording",
    )

    seen: set[str] = set()
    for interaction in interactions:
        key = f"{interaction.method.upper()} {interaction.path}"
        if key in seen:
            continue
        seen.add(key)
        spec.routes.append(
            MockRoute(
                method=interaction.method.upper(),
                path=interaction.path,
                status_code=interaction.status_code,
                summary="replayed",
                response_example=interaction.body,
                response_headers=interaction.response_headers,
                content_type=interaction.content_type,
            )
        )

    return spec


def create_replay_app(recordings_dir: str | Path, config: ServerConfig | None = None) -> FastAPI:
    """Create a mock server that serves previously recorded responses."""
    spec = build_replay_spec(recordings_dir)
    return create_app(spec, config)

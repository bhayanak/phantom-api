"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def petstore_path() -> Path:
    return FIXTURES / "petstore.yaml"


@pytest.fixture
def swagger_path() -> Path:
    return FIXTURES / "swagger-v2.json"


@pytest.fixture
def users_json_path() -> Path:
    return FIXTURES / "users.json"


@pytest.fixture
def collection_path() -> Path:
    return FIXTURES / "collection.json"

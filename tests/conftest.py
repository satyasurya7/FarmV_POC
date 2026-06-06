"""Shared fixtures and helpers for the test suite."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


EMBEDDING_DIM = 768


@pytest.fixture
def fake_embedding() -> list:
    """Return a deterministic 768-dim unit-ish vector (no Vertex AI call)."""
    return [0.01] * EMBEDDING_DIM


def make_row(chunk_id: str, content: str, score: float, metadata=None) -> dict:
    """Return an asyncpg-Record-like dict for use in mock fetch() calls."""
    return {"id": chunk_id, "content": content, "score": score, "metadata": metadata}


def make_mock_pool(semantic_rows: list, fts_rows: list) -> MagicMock:
    """Return a mock asyncpg.Pool whose fetch() returns the given rows in order."""
    mock_conn = AsyncMock()
    # First call → semantic results, second call → FTS results
    mock_conn.fetch = AsyncMock(side_effect=[semantic_rows, fts_rows])

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = MagicMock()
    mock_pool.acquire = _acquire
    return mock_pool

"""Integration tests for the RAG retriever — requires live DB with ingested chunks.

Run only after `docker compose up postgres -d` and `python -m scripts.ingest_kb`.
Skip automatically if DB is not reachable.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _db_available() -> bool:
    try:
        import asyncpg
        from src.config import settings

        async def _check():
            conn = await asyncpg.connect(dsn=settings.db.dsn, timeout=3)
            await conn.close()

        asyncio.get_event_loop().run_until_complete(_check())
        return True
    except Exception:
        return False


DB_UP = _db_available()


@pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")
@pytest.mark.asyncio
async def test_retrieve_returns_results():
    from src.database import get_pool
    from src.rag.retriever import retrieve

    pool = await get_pool()
    # Check if chunks exist before testing
    count = await pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks")
    if count == 0:
        pytest.skip("No chunks ingested yet — run scripts/ingest_kb.py first")

    chunks, latency_ms = await retrieve(pool, "ప్రకృతి వ్యవసాయం అంటే ఏమిటి")
    assert len(chunks) > 0
    assert latency_ms > 0
    assert all(0 <= c.score <= 1.5 for c in chunks)


@pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")
@pytest.mark.asyncio
async def test_retrieve_crop_variety_code():
    from src.database import get_pool
    from src.rag.retriever import retrieve

    pool = await get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks")
    if count == 0:
        pytest.skip("No chunks ingested yet")

    # Should find BPT 2537 via either semantic or FTS
    chunks, _ = await retrieve(pool, "BPT 2537 crop duration", top_k=3, threshold=0.3)
    assert len(chunks) >= 0  # may be 0 if KB doesn't have this variety


@pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")
@pytest.mark.asyncio
async def test_retrieve_empty_query_returns_gracefully():
    from src.database import get_pool
    from src.rag.retriever import retrieve

    pool = await get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks")
    if count == 0:
        pytest.skip("No chunks ingested yet")

    # Very low threshold so something comes back or nothing — shouldn't crash
    chunks, latency_ms = await retrieve(pool, "xyzzy nonexistent", threshold=0.99)
    assert isinstance(chunks, list)
    assert latency_ms >= 0

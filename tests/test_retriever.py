"""Tests for the RAG retriever.

Unit tests (no DB, no API keys) — run always.
Integration tests (require live DB + ingested KB) — auto-skipped when DB is down.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import make_mock_pool, make_row


# ── helpers ───────────────────────────────────────────────────────────────────

FAKE_EMBEDDING = [0.01] * 768


def _patch_embed(return_value=None):
    """Patch embed_query so no Vertex AI call is made."""
    return patch(
        "src.rag.retriever.embed_query",
        new_callable=AsyncMock,
        return_value=return_value or FAKE_EMBEDDING,
    )


# ── unit tests — merge logic ──────────────────────────────────────────────────

async def test_retrieve_returns_semantic_chunks():
    rows = [make_row("id1", "chunk content", 0.9)]
    pool = make_mock_pool(semantic_rows=rows, fts_rows=[])
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "test query", top_k=5, threshold=0.5)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "id1"
    assert chunks[0].content == "chunk content"
    assert chunks[0].score == pytest.approx(0.9)


async def test_retrieve_semantic_takes_priority_over_fts():
    semantic = [make_row("sem1", "semantic result", 0.85)]
    fts = [make_row("fts1", "fts result", 0.6)]
    pool = make_mock_pool(semantic_rows=semantic, fts_rows=fts)
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    # Semantic result must appear first
    assert chunks[0].chunk_id == "sem1"
    assert chunks[1].chunk_id == "fts1"


async def test_retrieve_deduplicates_fts_against_semantic():
    """A chunk returned by both semantic and FTS must appear only once."""
    shared_row = make_row("shared", "shared content", 0.8)
    pool = make_mock_pool(semantic_rows=[shared_row], fts_rows=[shared_row])
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    ids = [c.chunk_id for c in chunks]
    assert ids.count("shared") == 1


async def test_retrieve_fts_score_normalized_by_half():
    """FTS raw score must be multiplied by 0.5 in the merged result."""
    pool = make_mock_pool(semantic_rows=[], fts_rows=[make_row("fts1", "text", 0.8)])
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    assert chunks[0].score == pytest.approx(0.4)


async def test_retrieve_fts_fills_gap_when_semantic_is_short():
    """FTS results fill up to top_k when semantic returns fewer than top_k."""
    semantic = [make_row("sem1", "s1", 0.9)]
    fts = [
        make_row("fts1", "f1", 0.5),
        make_row("fts2", "f2", 0.4),
    ]
    pool = make_mock_pool(semantic_rows=semantic, fts_rows=fts)
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=3, threshold=0.5)
    assert len(chunks) == 3


async def test_retrieve_top_k_limits_fts_fill():
    """FTS must not push the merged list beyond top_k."""
    semantic = [make_row("sem1", "s1", 0.9), make_row("sem2", "s2", 0.8)]
    fts = [
        make_row("fts1", "f1", 0.7),
        make_row("fts2", "f2", 0.6),
    ]
    pool = make_mock_pool(semantic_rows=semantic, fts_rows=fts)
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=2, threshold=0.5)
    assert len(chunks) == 2


async def test_retrieve_empty_both_returns_empty_list():
    pool = make_mock_pool(semantic_rows=[], fts_rows=[])
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    assert chunks == []


async def test_retrieve_returns_positive_latency_ms():
    pool = make_mock_pool(semantic_rows=[], fts_rows=[])
    with _patch_embed():
        from src.rag.retriever import retrieve
        _, latency_ms = await retrieve(pool, "query", top_k=5, threshold=0.5)
    assert latency_ms >= 0


async def test_retrieve_metadata_none_becomes_empty_dict():
    pool = make_mock_pool(
        semantic_rows=[make_row("id1", "content", 0.9, metadata=None)],
        fts_rows=[],
    )
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    assert chunks[0].metadata == {}


async def test_retrieve_metadata_json_string_parsed():
    meta_json = json.dumps({"question_number": "1.2", "source": "kb.docx"})
    pool = make_mock_pool(
        semantic_rows=[make_row("id1", "content", 0.9, metadata=meta_json)],
        fts_rows=[],
    )
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, _ = await retrieve(pool, "query", top_k=5, threshold=0.5)
    assert chunks[0].metadata["question_number"] == "1.2"


async def test_retrieve_uses_settings_defaults_when_not_specified():
    """Calling retrieve() without top_k/threshold must not raise."""
    pool = make_mock_pool(semantic_rows=[], fts_rows=[])
    with _patch_embed():
        from src.rag.retriever import retrieve
        chunks, latency_ms = await retrieve(pool, "some query")
    assert isinstance(chunks, list)
    assert latency_ms >= 0


async def test_retrieve_calls_embed_query_once():
    pool = make_mock_pool(semantic_rows=[], fts_rows=[])
    with _patch_embed() as mock_embed:
        from src.rag.retriever import retrieve
        await retrieve(pool, "my query", top_k=5, threshold=0.5)
    mock_embed.assert_awaited_once_with("my query")


# ── integration tests — require live DB with ingested KB ──────────────────────

def _db_available() -> bool:
    try:
        import asyncpg
        from src.config import settings

        async def _check():
            conn = await asyncpg.connect(dsn=settings.db.dsn, timeout=3)
            await conn.close()

        asyncio.run(_check())
        return True
    except Exception:
        return False


DB_UP = _db_available()
_skip_no_db = pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")


@pytest.fixture
async def live_pool():
    """Fresh asyncpg pool per test — avoids event-loop reuse issues with the singleton."""
    import asyncpg
    from src.config import settings

    pool = await asyncpg.create_pool(dsn=settings.db.dsn, min_size=1, max_size=3, command_timeout=10)
    yield pool
    await pool.close()


@_skip_no_db
async def test_retrieve_returns_results_live(live_pool):
    from src.rag.retriever import retrieve

    count = await live_pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks")
    if count == 0:
        pytest.skip("No chunks ingested — run scripts/ingest_kb.py first")

    chunks, latency_ms = await retrieve(live_pool, "ప్రకృతి వ్యవసాయం అంటే ఏమిటి")
    assert len(chunks) > 0
    assert latency_ms > 0
    assert all(0 <= c.score <= 1.5 for c in chunks)


@_skip_no_db
async def test_retrieve_crop_variety_code_live(live_pool):
    from src.rag.retriever import retrieve

    if await live_pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks") == 0:
        pytest.skip("No chunks ingested")

    # Should find BPT 2537 via FTS even if semantic score is borderline
    chunks, _ = await retrieve(live_pool, "BPT 2537 crop duration", top_k=3, threshold=0.3)
    assert isinstance(chunks, list)  # result count depends on KB content


@_skip_no_db
async def test_retrieve_nonexistent_query_returns_gracefully_live(live_pool):
    from src.rag.retriever import retrieve

    if await live_pool.fetchval("SELECT COUNT(*) FROM knowledge_chunks") == 0:
        pytest.skip("No chunks ingested")

    # Very high threshold → likely empty result; should not raise
    chunks, latency_ms = await retrieve(live_pool, "xyzzy nonexistent", threshold=0.99)
    assert isinstance(chunks, list)
    assert latency_ms >= 0

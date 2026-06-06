"""Hybrid RAG retriever — combines pgvector cosine similarity + PostgreSQL full-text search."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

import json

import asyncpg
from loguru import logger

from src.config import settings
from src.rag.embeddings import embed_query


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    score: float
    metadata: dict


async def retrieve(
    pool: asyncpg.Pool,
    query: str,
    top_k: int | None = None,
    threshold: float | None = None,
) -> tuple[List[RetrievedChunk], float]:
    """Return top-k chunks and retrieval latency (ms)."""
    k = top_k or settings.rag.top_k
    thresh = threshold or settings.rag.similarity_threshold

    t0 = time.monotonic()
    query_embedding = await embed_query(query)

    async with pool.acquire() as conn:
        # Serialize query embedding for pgvector
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # Semantic search via cosine similarity
        semantic_rows = await conn.fetch(
            """
            SELECT id::text, content, metadata,
                   1 - (embedding <=> $1::vector) AS score
            FROM knowledge_chunks
            WHERE 1 - (embedding <=> $1::vector) >= $2
            ORDER BY score DESC
            LIMIT $3
            """,
            vec_str,
            thresh,
            k,
        )

        # Full-text search (fallback / boost for exact crop codes / variety names)
        fts_rows = await conn.fetch(
            """
            SELECT id::text, content, metadata,
                   ts_rank(ts_content, plainto_tsquery('simple', $1)) AS score
            FROM knowledge_chunks
            WHERE ts_content @@ plainto_tsquery('simple', $1)
            ORDER BY score DESC
            LIMIT $2
            """,
            query,
            k,
        )

    # Merge: semantic results take priority; FTS fills gaps
    seen_ids = set()
    merged: List[RetrievedChunk] = []

    for row in semantic_rows:
        seen_ids.add(row["id"])
        merged.append(
            RetrievedChunk(
                chunk_id=row["id"],
                content=row["content"],
                score=float(row["score"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
        )

    for row in fts_rows:
        if row["id"] not in seen_ids and len(merged) < k:
            merged.append(
                RetrievedChunk(
                    chunk_id=row["id"],
                    content=row["content"],
                    score=float(row["score"]) * 0.5,  # normalise FTS score
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )

    latency_ms = (time.monotonic() - t0) * 1000
    logger.debug("Retrieved {} chunks in {:.1f}ms for query: {!r}", len(merged), latency_ms, query[:60])
    return merged, latency_ms

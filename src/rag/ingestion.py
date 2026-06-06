"""Bulk ingestion of knowledge base files into PostgreSQL (pgvector)."""

from __future__ import annotations

from pathlib import Path
from typing import List

import asyncpg
from loguru import logger

from src.rag.chunker import Chunk, chunk_file
from src.rag.embeddings import embed_texts

import json


async def ingest_directory(pool: asyncpg.Pool, source_dir: Path) -> int:
    files = sorted(source_dir.iterdir())
    supported = {".csv", ".txt", ".md", ".docx"}
    files = [f for f in files if f.suffix.lower() in supported]

    if not files:
        logger.warning("No supported files found in {}", source_dir)
        return 0

    all_chunks: List[Chunk] = []
    for f in files:
        chunks = chunk_file(f)
        all_chunks.extend(chunks)
        logger.info("Chunked {} → {} chunks", f.name, len(chunks))

    logger.info("Generating embeddings for {} total chunks…", len(all_chunks))
    texts = [c.content for c in all_chunks]
    embeddings = await embed_texts(texts)

    async with pool.acquire() as conn:
        # Clear existing chunks from same source files
        source_names = list({c.source_file for c in all_chunks})
        await conn.execute(
            "DELETE FROM knowledge_chunks WHERE source_file = ANY($1::text[])", source_names
        )

        # Register pgvector codec so asyncpg accepts Python lists as vector
        await conn.execute("SET search_path TO public")
        await conn.execute("SELECT NULL::vector")  # ensure extension is loaded

        # Bulk insert — serialize embedding list to pgvector string '[v1,v2,...]'
        records = [
            (
                c.source_file,
                c.chunk_index,
                c.content,
                json.dumps(c.metadata),
                "[" + ",".join(str(v) for v in embeddings[i]) + "]",
            )
            for i, c in enumerate(all_chunks)
        ]
        await conn.executemany(
            """
            INSERT INTO knowledge_chunks (source_file, chunk_index, content, metadata, embedding)
            VALUES ($1, $2, $3, $4::jsonb, $5::vector)
            """,
            records,
        )

    logger.info("Inserted {} chunks", len(all_chunks))
    return len(all_chunks)

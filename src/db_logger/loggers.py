"""Async DB logging helpers — one function per log table."""

from __future__ import annotations

import json
import traceback
from typing import List

import asyncpg
from loguru import logger as log

from src.database import get_pool
from src.rag.retriever import RetrievedChunk


async def _pool() -> asyncpg.Pool:
    return await get_pool()


async def log_session_start(session_id: str, phone_number: str | None, tata_call_id: str | None) -> None:
    p = await _pool()
    await p.execute(
        """
        INSERT INTO call_sessions (id, phone_number, tata_call_id)
        VALUES ($1::uuid, $2, $3)
        ON CONFLICT (id) DO NOTHING
        """,
        session_id, phone_number, tata_call_id,
    )


async def update_session_phone(
    session_id: str, phone_number: str | None, call_sid: str | None = None
) -> None:
    p = await _pool()
    # COALESCE on both fields: never overwrite an existing value with NULL/empty
    await p.execute(
        """
        UPDATE call_sessions
        SET phone_number = COALESCE(NULLIF($2, ''), phone_number),
            tata_call_id = COALESCE($3, tata_call_id)
        WHERE id = $1::uuid
        """,
        session_id, phone_number or None, call_sid,
    )


async def log_caller_name(session_id: str, name: str) -> None:
    p = await _pool()
    await p.execute(
        "UPDATE call_sessions SET caller_name = $2 WHERE id = $1::uuid",
        session_id, name,
    )


async def log_session_end(session_id: str, status: str = "completed") -> None:
    p = await _pool()
    # AND status = 'active' guard: makes repeated calls from pipeline + server idempotent
    await p.execute(
        """
        UPDATE call_sessions
        SET ended_at = NOW(),
            duration_s = EXTRACT(EPOCH FROM (NOW() - started_at)),
            status = $2
        WHERE id = $1::uuid AND status = 'active'
        """,
        session_id, status,
    )


async def log_utterance(
    session_id: str,
    turn: int,
    text: str,
    confidence: float | None = None,
    stt_latency_ms: float | None = None,
) -> str:
    p = await _pool()
    row = await p.fetchrow(
        """
        INSERT INTO utterances (session_id, turn_number, text, confidence, stt_latency_ms)
        VALUES ($1::uuid, $2, $3, $4, $5)
        RETURNING id::text
        """,
        session_id, turn, text, confidence, stt_latency_ms,
    )
    return row["id"]


async def log_response(
    session_id: str,
    utterance_id: str | None,
    turn: int,
    text: str,
    llm_latency_ms: float | None = None,
    tts_latency_ms: float | None = None,
) -> None:
    p = await _pool()
    await p.execute(
        """
        INSERT INTO agent_responses (session_id, utterance_id, turn_number, text, llm_latency_ms, tts_latency_ms)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
        """,
        session_id, utterance_id, turn, text, llm_latency_ms, tts_latency_ms,
    )


async def log_retrieval(
    session_id: str,
    utterance_id: str | None,
    query: str,
    chunks: List[RetrievedChunk],
    latency_ms: float,
) -> None:
    p = await _pool()
    chunk_data = [
        {"chunk_id": c.chunk_id, "text": c.content[:300], "score": c.score}
        for c in chunks
    ]
    top_score = chunks[0].score if chunks else None
    await p.execute(
        """
        INSERT INTO retrieval_logs (session_id, utterance_id, query, retrieved_chunks, top_score, retrieval_latency_ms)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6)
        """,
        session_id, utterance_id, query, json.dumps(chunk_data), top_score, latency_ms,
    )


async def log_error(
    session_id: str | None,
    error_type: str,
    error_message: str,
    exc: Exception | None = None,
) -> None:
    p = await _pool()
    stack = traceback.format_exc() if exc else None
    await p.execute(
        """
        INSERT INTO error_logs (session_id, error_type, error_message, stack_trace)
        VALUES ($1::uuid, $2, $3, $4)
        """,
        session_id, error_type, error_message, stack,
    )
    log.error("[session={}] {} — {}", session_id, error_type, error_message)


async def log_metrics(
    session_id: str,
    turn: int,
    stt_ms: float | None = None,
    retrieval_ms: float | None = None,
    llm_ms: float | None = None,
    tts_ms: float | None = None,
    e2e_ms: float | None = None,
) -> None:
    p = await _pool()
    await p.execute(
        """
        INSERT INTO performance_metrics
            (session_id, turn_number, stt_latency_ms, retrieval_latency_ms, llm_latency_ms, tts_latency_ms, e2e_latency_ms)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
        """,
        session_id, turn, stt_ms, retrieval_ms, llm_ms, tts_ms, e2e_ms,
    )

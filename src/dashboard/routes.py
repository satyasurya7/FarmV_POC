"""Dashboard — HTML pages + JSON API.

Routes:
  GET /dashboard                      → sessions list (dashboard.html)
  GET /dashboard/session              → session detail (session.html)
  GET /dashboard/recordings/{file}    → serve WAV file
  GET /dashboard/api/stats            → aggregate stats JSON
  GET /dashboard/api/sessions         → sessions list JSON
  GET /dashboard/api/sessions/{id}    → session detail JSON
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from src.database import get_pool

router = APIRouter(prefix="/dashboard")

_STATIC = Path(__file__).parent / "static"
_RECORDINGS = Path("data/recordings").resolve()


# ── HTML pages ────────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
async def page_dashboard():
    return FileResponse(_STATIC / "dashboard.html")


@router.get("/session")
async def page_session():
    return FileResponse(_STATIC / "session.html")


# ── Recordings ────────────────────────────────────────────────────────────────

@router.get("/recordings/{filename}")
async def serve_recording(filename: str):
    # Guard against path traversal
    path = (_RECORDINGS / filename).resolve()
    if not str(path).startswith(str(_RECORDINGS)) or not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(path), media_type="audio/wav")


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/stats")
async def api_stats():
    pool = await get_pool()
    row = await pool.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours')                              AS total_today,
            COUNT(*) FILTER (WHERE status = 'completed' AND started_at > NOW() - INTERVAL '24 hours')     AS completed_today,
            COUNT(*) FILTER (WHERE status = 'active')                                                      AS active_now,
            AVG(duration_s) FILTER (WHERE duration_s IS NOT NULL AND started_at > NOW() - INTERVAL '24 hours') AS avg_duration_s
        FROM call_sessions
    """)
    metrics = await pool.fetchrow("""
        SELECT AVG(e2e_latency_ms) AS avg_e2e_ms
        FROM performance_metrics
        WHERE created_at > NOW() - INTERVAL '24 hours' AND e2e_latency_ms IS NOT NULL
    """)
    return {
        "total_today": int(row["total_today"]),
        "completed_today": int(row["completed_today"]),
        "active_now": int(row["active_now"]),
        "avg_duration_s": round(float(row["avg_duration_s"]), 1) if row["avg_duration_s"] else None,
        "avg_e2e_ms": round(float(metrics["avg_e2e_ms"])) if metrics["avg_e2e_ms"] else None,
    }


@router.get("/api/sessions")
async def api_sessions(limit: int = 100):
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT
            cs.id::text,
            cs.phone_number,
            cs.caller_name,
            cs.started_at,
            cs.duration_s,
            cs.status,
            COUNT(u.id) AS turn_count
        FROM call_sessions cs
        LEFT JOIN utterances u ON u.session_id = cs.id
        GROUP BY cs.id
        ORDER BY cs.started_at DESC
        LIMIT $1
    """, limit)

    result = []
    for r in rows:
        sid = r["id"]
        result.append({
            "id": sid,
            "phone_number": r["phone_number"],
            "caller_name": r["caller_name"],
            "started_at": r["started_at"].isoformat(),
            "duration_s": r["duration_s"],
            "status": r["status"],
            "turn_count": int(r["turn_count"]),
            "has_recording": (_RECORDINGS / f"{sid}_conversation.wav").exists(),
        })
    return result


@router.get("/api/sessions/{session_id}")
async def api_session_detail(session_id: str):
    pool = await get_pool()

    session = await pool.fetchrow("""
        SELECT id::text, phone_number, caller_name, started_at, ended_at, duration_s, status
        FROM call_sessions WHERE id = $1::uuid
    """, session_id)

    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)

    turns_rows = await pool.fetch("""
        SELECT
            u.turn_number,
            u.text               AS farmer_text,
            ar.text              AS bot_text,
            ar.llm_latency_ms,
            ar.tts_latency_ms,
            rl.retrieved_chunks,
            rl.retrieval_latency_ms,
            rl.top_score,
            rl.query             AS rag_query,
            pm.e2e_latency_ms
        FROM utterances u
        LEFT JOIN agent_responses ar
            ON ar.session_id = u.session_id AND ar.turn_number = u.turn_number
        LEFT JOIN retrieval_logs rl
            ON rl.utterance_id = u.id
        LEFT JOIN performance_metrics pm
            ON pm.session_id = u.session_id AND pm.turn_number = u.turn_number
        WHERE u.session_id = $1::uuid
        ORDER BY u.turn_number
    """, session_id)

    turns = []
    for r in turns_rows:
        chunks = r["retrieved_chunks"]
        if isinstance(chunks, str):
            try:
                chunks = json.loads(chunks)
            except Exception:
                chunks = []
        turns.append({
            "turn_number": r["turn_number"],
            "farmer_text": r["farmer_text"],
            "bot_text": r["bot_text"],
            "llm_latency_ms": r["llm_latency_ms"],
            "tts_latency_ms": r["tts_latency_ms"],
            "retrieval_latency_ms": r["retrieval_latency_ms"],
            "top_score": r["top_score"],
            "rag_query": r["rag_query"],
            "e2e_latency_ms": r["e2e_latency_ms"],
            "rag_chunks": chunks or [],
        })

    return {
        "id": session["id"],
        "phone_number": session["phone_number"],
        "caller_name": session["caller_name"],
        "started_at": session["started_at"].isoformat(),
        "ended_at": session["ended_at"].isoformat() if session["ended_at"] else None,
        "duration_s": session["duration_s"],
        "status": session["status"],
        "turns": turns,
    }


@router.get("/api/sessions/{session_id}/errors")
async def api_session_errors(session_id: str):
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT error_type, error_message, stack_trace, created_at
        FROM error_logs
        WHERE session_id = $1::uuid
        ORDER BY created_at
    """, session_id)
    return [
        {
            "error_type": r["error_type"],
            "error_message": r["error_message"],
            "stack_trace": r["stack_trace"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]

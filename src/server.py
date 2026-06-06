"""HTTP + WebSocket entrypoint.

- GET /health         → liveness probe
- GET /metrics        → basic session stats
- WS  /ws/smartflo   → one WebSocket per incoming Tata Tele SmartFlo call
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import uuid

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer

from src.config import settings
from src.dashboard.routes import router as dashboard_router
from src.database import close_pool, get_pool
from src.db_logger.loggers import log_session_end, log_session_start
from src.pipeline import run_pipeline
from src.rag.embeddings import embed_query
from src.telephony.tata_tele import create_transport


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()

    # Any session left in "active" from a previous server run is orphaned — mark dropped
    result = await pool.execute(
        "UPDATE call_sessions SET status='dropped', ended_at=NOW() WHERE status='active'"
    )
    # result is "UPDATE N" — extract the count
    dropped = int(result.split()[-1]) if result else 0
    if dropped:
        logger.info("Marked {} orphaned active sessions as dropped", dropped)

    # Pre-load ML models once at startup so the first call has no model-load delay
    logger.info("Pre-loading VAD and Smart Turn models...")
    SileroVADAnalyzer(sample_rate=8000)
    LocalSmartTurnAnalyzerV3()
    logger.info("Models pre-loaded")

    # Warm up the Vertex AI embedding connection — first call pays ~3s cold-start
    # penalty; subsequent calls are ~450ms. One dummy query here eliminates the lag
    # on the first real caller's turn 1.
    logger.info("Warming up Vertex AI embedding API...")
    try:
        await embed_query("warm up")
        logger.info("Vertex AI embedding warmed up. Server ready on port {}", settings.server.http_port)
    except Exception as exc:
        logger.warning("Embedding warm-up failed (non-fatal): {}", exc)
    yield
    await close_pool()


app = FastAPI(title="Farm Vaidya Voice Agent", version="0.1.0", lifespan=lifespan)
app.include_router(dashboard_router)

# session_id → caller phone number (for /metrics in-memory view)
_active_sessions: dict[str, str] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(_active_sessions)}


@app.get("/metrics")
async def metrics():
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'active')    AS active_calls,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed_calls,
            COUNT(*) FILTER (WHERE status = 'error')     AS error_calls,
            AVG(duration_s) FILTER (WHERE duration_s IS NOT NULL) AS avg_duration_s
        FROM call_sessions
        WHERE started_at > NOW() - INTERVAL '24 hours'
        """
    )
    return dict(row)


@app.websocket("/ws/smartflo")
async def ws_smartflo(websocket: WebSocket):
    """Handle one incoming Tata Tele SmartFlo call."""
    await websocket.accept()

    session_id = str(uuid.uuid4())
    phone_number = websocket.headers.get("X-Caller-ID") or websocket.query_params.get("caller_id")
    tata_call_id = websocket.headers.get("X-Call-ID") or websocket.query_params.get("call_id")

    _active_sessions[session_id] = phone_number or "unknown"
    logger.info("New call session={} caller={}", session_id, phone_number)

    await log_session_start(session_id, phone_number, tata_call_id)

    pool = await get_pool()
    transport = create_transport(websocket)

    try:
        await run_pipeline(transport=transport, pool=pool, session_id=session_id)
    except WebSocketDisconnect:
        logger.info("Call ended (abrupt disconnect) session={}", session_id)
        await log_session_end(session_id, status="dropped")
    except Exception as exc:
        logger.error("Unhandled error in session {}: {}", session_id, exc)
        await log_session_end(session_id, status="error")
    finally:
        _active_sessions.pop(session_id, None)
        # Safety-net: covers CancelledError (server shutdown/restart) and any
        # path that skipped log_session_end. WHERE status='active' ensures we
        # never overwrite a completed/error/dropped status already set.
        try:
            _pool = await get_pool()
            await _pool.execute(
                """
                UPDATE call_sessions
                SET ended_at = NOW(),
                    duration_s = EXTRACT(EPOCH FROM (NOW() - started_at)),
                    status = 'dropped'
                WHERE id = $1::uuid AND status = 'active'
                """,
                session_id,
            )
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass  # already closed


def main() -> None:
    uvicorn.run(
        "src.server:app",
        host=settings.server.host,
        port=settings.server.http_port,
        log_level=settings.server.log_level.lower(),
        workers=1,
    )


if __name__ == "__main__":
    main()

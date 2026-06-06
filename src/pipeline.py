"""Farm Vaidya voice agent pipeline (pipecat 1.3.x).

One pipeline instance per incoming call.

Flow:
  transport.input()
    → SonioxSTTService         (audio → text, Telugu)
    → LLMUserContextAggregator (accumulates turn history)
    → RAGContextProcessor      (injects KB context into system instruction)
    → GoogleVertexLLMService   (Gemini 2.5 Flash, Vertex AI Mumbai)
    → LLMAssistantContextAggregator
    → CartesiaTTSService       (text → audio, Telugu)
  → transport.output()
"""

from __future__ import annotations

import re
import time
from typing import List

from loguru import logger

from fastapi.websockets import WebSocketDisconnect

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService, CartesiaTTSSettings
from pipecat.services.soniox.stt import SonioxSTTService, SonioxSTTSettings
from pipecat.transcriptions.language import Language

from src.config import settings
from src.recording import BotTap, CallerTap, ConversationRecorder
from src.db_logger.loggers import (
    log_caller_name,
    log_error,
    log_metrics,
    log_response,
    log_retrieval,
    log_session_end,
    log_utterance,
)


def _extract_name(utterance: str) -> str:
    """Extract caller's first name from turn-1 reply to 'మీ పేరు చెప్పగలరా?'

    Handles patterns like:
      'నా పేరు నరేంద్ర.'   → 'నరేంద్ర'
      'నా పేరు రాజు గారు'  → 'రాజు'
      'నేను సురేష్'        → 'సురేష్'
      'రాజేష్'             → 'రాజేష్'
    Falls back to the full utterance if no pattern matches.
    """
    text = utterance.strip().rstrip(".")
    # "నా పేరు [name]" — optionally followed by honorifics
    m = re.search(r"నా పేరు\s+(.+?)(?:\s+(?:అండి|గారు|sir|Sir|madam))?$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".")
    # "నేను [name]" — "I am [name]"
    m = re.search(r"నేను\s+(\S+)", text)
    if m:
        return m.group(1).rstrip("ని").strip()
    # Short reply (≤ 2 words) — almost certainly just the name
    if len(text.split()) <= 2:
        return text
    return text


from src.error_handler import LLMErrorProcessor, STTGuardProcessor
from src.prompts import GREETING, NO_CONTEXT_RESPONSE, SYSTEM_PROMPT
from src.rag.retriever import RetrievedChunk, retrieve
from src.services.llm import create_llm_service


# ── RAG context processor ────────────────────────────────────────────────────

class RAGContextProcessor(FrameProcessor):
    """Intercepts LLMContextFrame, retrieves KB context, updates system instruction."""

    def __init__(self, pool, session_id: str):
        super().__init__()
        self._pool = pool
        self._session_id = session_id
        self._turn = 0
        self._utterance_id: str | None = None
        self._last_retrieval_ms: float = 0.0  # read by LLMResponseLogger

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            context = frame.context
            messages = context.get_messages()

            # Collect last 2 user utterances for context-aware RAG retrieval.
            # Follow-up questions like "ఎంత వాడాలి?" are too vague on their own;
            # prepending the previous turn restores the topic for the embedding search.
            user_turns: list[str] = []
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content")
                    if content and isinstance(content, str):
                        user_turns.append(content)
                    if len(user_turns) >= 2:
                        break

            user_msg = user_turns[0] if user_turns else None  # current utterance (for logging)
            rag_query = " ".join(reversed(user_turns)) if user_turns else None  # prev + current

            if user_msg:
                self._turn += 1
                t0 = time.monotonic()

                self._utterance_id = await log_utterance(
                    session_id=self._session_id,
                    turn=self._turn,
                    text=user_msg,
                )

                # Turn 1 is the response to "మీ పేరు చెప్పగలరా?" — log extracted name
                if self._turn == 1:
                    await log_caller_name(self._session_id, _extract_name(user_msg))

                try:
                    chunks, retrieval_ms = await retrieve(self._pool, rag_query)
                    self._last_retrieval_ms = retrieval_ms
                    await log_retrieval(
                        session_id=self._session_id,
                        utterance_id=self._utterance_id,
                        query=rag_query,
                        chunks=chunks,
                        latency_ms=retrieval_ms,
                    )
                except Exception as exc:
                    logger.error("RAG retrieval error: {}", exc)
                    await log_error(self._session_id, "retrieval_error", str(exc), exc)
                    chunks = []
                    retrieval_ms = 0.0

                kb_context = _format_context(chunks)
                system_text = SYSTEM_PROMPT.format(context=kb_context or NO_CONTEXT_RESPONSE)

                # Update system instruction on the context object
                context.set_messages(
                    [{"role": "system", "content": system_text}]
                    + [m for m in messages if m.get("role") != "system"]
                )

        await self.push_frame(frame, direction)


class LLMResponseLogger(FrameProcessor):
    """Accumulates streaming LLM text and logs the full response + latencies.

    The log entry is buffered after LLMFullResponseEndFrame and only written
    to the DB once BotStartedSpeakingFrame arrives upstream — this gives us
    the TTS latency (time from LLM done → first audio playing).

    If the bot never starts speaking (error path), the pending entry is
    flushed without TTS latency on the next LLMFullResponseStartFrame.
    """

    def __init__(self, rag_processor: RAGContextProcessor):
        super().__init__()
        self._rag = rag_processor
        self._buf: list[str] | None = None
        self._t_llm_start: float = 0.0
        self._t_llm_end: float | None = None
        self._pending: dict | None = None  # buffered entry awaiting TTS timing

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, LLMFullResponseStartFrame):
                # Flush stale pending entry from a previous turn where TTS never fired
                if self._pending is not None:
                    await self._write(tts_ms=None)
                self._buf = []
                self._t_llm_start = time.monotonic()
            elif isinstance(frame, LLMTextFrame) and self._buf is not None:
                self._buf.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame) and self._buf is not None:
                await self._buffer()

        elif direction == FrameDirection.UPSTREAM:
            # BotStartedSpeakingFrame travels upstream from transport.output()
            if isinstance(frame, BotStartedSpeakingFrame) and self._pending is not None:
                tts_ms = None
                if self._t_llm_end is not None:
                    tts_ms = round((time.monotonic() - self._t_llm_end) * 1000, 1)
                await self._write(tts_ms=tts_ms)

        await self.push_frame(frame, direction)

    async def _buffer(self) -> None:
        """Prepare log entry after LLM finishes; wait for TTS before writing."""
        if not self._buf:
            self._buf = None
            return
        text = "".join(self._buf)
        llm_ms = round((time.monotonic() - self._t_llm_start) * 1000, 1)
        self._buf = None
        self._t_llm_end = time.monotonic()
        self._pending = {
            "session_id": self._rag._session_id,
            "utterance_id": self._rag._utterance_id,
            "turn": self._rag._turn,
            "text": text,
            "llm_ms": llm_ms,
            "retrieval_ms": self._rag._last_retrieval_ms,
        }

    async def _write(self, tts_ms: float | None) -> None:
        """Write response + performance metrics to DB."""
        if self._pending is None:
            return
        p, self._pending, self._t_llm_end = self._pending, None, None
        try:
            await log_response(
                session_id=p["session_id"],
                utterance_id=p["utterance_id"],
                turn=p["turn"],
                text=p["text"],
                llm_latency_ms=p["llm_ms"],
                tts_latency_ms=tts_ms,
            )
            e2e_ms = round(p["retrieval_ms"] + p["llm_ms"] + (tts_ms or 0), 1)
            await log_metrics(
                session_id=p["session_id"],
                turn=p["turn"],
                retrieval_ms=p["retrieval_ms"],
                llm_ms=p["llm_ms"],
                tts_ms=tts_ms,
                e2e_ms=e2e_ms,
            )
        except Exception as exc:
            logger.warning("LLMResponseLogger: DB write failed: {}", exc)


def _format_context(chunks: List[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    return "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(chunks))


# ── Pipeline entry point ─────────────────────────────────────────────────────

async def run_pipeline(transport, pool, session_id: str) -> None:
    logger.info("Pipeline starting for session {}", session_id)

    # Build services
    stt = SonioxSTTService(
        api_key=settings.soniox.api_key,
        settings=SonioxSTTSettings(language_hints=[Language.TE]),
    )

    llm = create_llm_service()

    tts = CartesiaTTSService(
        api_key=settings.cartesia.api_key,
        settings=CartesiaTTSSettings(
            model=settings.cartesia.model_id,
            voice=settings.cartesia.voice_id,
            **( {"language": settings.cartesia.language} if settings.cartesia.language else {} ),
        ),
    )

    # Seed the greeting as the first assistant turn so the LLM knows it already
    # asked for the caller's name — without this, the model has no record of the
    # TTSSpeakFrame greeting that bypassed the context aggregator.
    initial_context = LLMContext(messages=[
        {"role": "system", "content": SYSTEM_PROMPT.format(context="")},
        {"role": "assistant", "content": GREETING},
    ])

    vad = SileroVADAnalyzer(
        sample_rate=8000,
        params=VADParams(
            confidence=0.6,
            start_secs=0.2,
            stop_secs=0.3,    # 0.3s pause to detect end-of-turn on telephony
            min_volume=0.3,   # lower than default for compressed telephony audio
        ),
    )
    agg_pair = LLMContextAggregatorPair(
        initial_context,
        user_params=LLMUserAggregatorParams(vad_analyzer=vad),
    )

    rag_processor = RAGContextProcessor(pool=pool, session_id=session_id)
    response_logger = LLMResponseLogger(rag_processor)
    stt_guard = STTGuardProcessor(session_id=session_id)
    llm_error = LLMErrorProcessor(session_id=session_id)

    conv_rec = ConversationRecorder(session_id=session_id)
    conv_rec.start()
    caller_tap = CallerTap(conv_rec)   # gates on BotStarted/StoppedSpeaking (upstream)
    bot_tap = BotTap(conv_rec)

    pipeline = Pipeline(
        [
            transport.input(),
            caller_tap,       # capture caller audio; intercepts upstream bot-speaking events
            stt,
            stt_guard,
            agg_pair.user(),
            rag_processor,
            llm,
            response_logger,  # log LLM response text + latency
            tts,
            bot_tap,          # capture TTS audio
            llm_error,
            agg_pair.assistant(),
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
        enable_rtvi=False,
    )

    # Send greeting — TTSSpeakFrame bypasses LLMUserAggregator and goes straight to TTS
    await task.queue_frames([TTSSpeakFrame(text=GREETING)])

    runner = PipelineRunner()
    try:
        await runner.run(task)
    except WebSocketDisconnect:
        # Let server.py handle status logging for abrupt disconnects
        raise
    except Exception as exc:
        logger.error("Pipeline error session={}: {}", session_id, exc)
        await log_error(session_id, "pipeline_error", str(exc), exc)
        await log_session_end(session_id, status="error")
        raise
    else:
        await log_session_end(session_id, status="completed")
    # CancelledError (BaseException, not Exception) is NOT caught above.
    # server.py's finally safety-net handles the status update in that case.

    logger.info("Pipeline done for session {}", session_id)

"""Pipeline error handling processors (pipecat 1.3.x)."""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSSpeakFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from src.db_logger.loggers import log_error
from src.prompts import LLM_ERROR_RESPONSE, STT_FAILURE_RESPONSE


class STTGuardProcessor(FrameProcessor):
    """Drops empty transcriptions and prompts the caller to repeat."""

    MIN_WORDS = 1

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if len(text.split()) < self.MIN_WORDS:
                logger.debug("STT guard dropped empty transcription")
                await log_error(
                    self._session_id, "stt_empty", f"Empty transcription: {text!r}"
                )
                await self.push_frame(
                    TextFrame(text=STT_FAILURE_RESPONSE), FrameDirection.DOWNSTREAM
                )
                return

        await self.push_frame(frame, direction)


class LLMErrorProcessor(FrameProcessor):
    """Logs LLM error frames and lets the pipeline continue gracefully."""

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, ErrorFrame):
            error_msg = str(frame.error)
            logger.warning("LLM error frame: {}", error_msg)
            await log_error(self._session_id, "llm_error", error_msg)
            # Push the fallback message upstream so TTS speaks it.
            # Upstream direction reaches CartesiaTTSService which sits before
            # this processor in the pipeline; it generates audio and sends it
            # downstream to transport.output() normally.
            await self.push_frame(
                TTSSpeakFrame(text=LLM_ERROR_RESPONSE), FrameDirection.UPSTREAM
            )
            return  # swallow the error frame; pipeline continues

        await self.push_frame(frame, direction)

"""Per-call conversation recording.

Records both sides into a single mono WAV:
  data/recordings/{session_id}_conversation.wav

Strategy:
  - Bot audio (OutputAudioRawFrame) is always captured with wall-clock timestamps
    so silence during LLM processing is preserved in the recording.
  - Caller audio (InputAudioRawFrame) is captured ONLY when the bot is not speaking,
    avoiding bot playback noise on the caller's channel.
  - BotStartedSpeakingFrame / BotStoppedSpeakingFrame gate the caller side cleanly.
  - An asyncio Queue serialises writes so two processors can push concurrently.
"""

from __future__ import annotations

import audioop
import asyncio
import time
import wave
from pathlib import Path

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_RECORDINGS_DIR = Path("data/recordings")
_SAMPLE_RATE = 8000
_BPS = 2  # bytes per 16-bit sample


class ConversationRecorder:
    """Shared state for both tap processors.

    Both CallerTap and BotTap hold a reference to the same instance.
    """

    def __init__(self, session_id: str) -> None:
        _RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = _RECORDINGS_DIR / f"{session_id}_conversation.wav"
        self._queue: asyncio.Queue = asyncio.Queue()
        self._start_time: float = time.monotonic()
        self._bot_speaking: bool = False
        self._writer_task: asyncio.Task | None = None
        self._closed = False
        self._resample_state: object = None  # continuity state for audioop.ratecv

    def start(self) -> None:
        self._writer_task = asyncio.create_task(self._write_loop())

    # ── called by BotTap ─────────────────────────────────────────────────────

    def bot_started(self) -> None:
        self._bot_speaking = True

    def bot_stopped(self) -> None:
        self._bot_speaking = False

    async def write_bot(self, audio: bytes, sample_rate: int) -> None:
        if self._closed:
            return
        # Calculate duration BEFORE resampling (using the actual incoming rate)
        duration = len(audio) / _BPS / sample_rate
        ts = max(time.monotonic() - self._start_time - duration, 0.0)
        if sample_rate != _SAMPLE_RATE:
            # Resample to _SAMPLE_RATE; preserve state for smooth inter-chunk conversion
            audio, self._resample_state = audioop.ratecv(
                audio, _BPS, 1, sample_rate, _SAMPLE_RATE, self._resample_state
            )
        await self._queue.put((ts, audio))

    # ── called by CallerTap ──────────────────────────────────────────────────

    async def write_caller(self, audio: bytes) -> None:
        # Skip caller audio while bot is playing — caller hears the bot on their
        # handset; recording both simultaneously would double the bot audio.
        if self._closed or self._bot_speaking:
            return
        await self._queue.put(self._item(audio))

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)  # sentinel → writer exits
        if self._writer_task:
            try:
                await asyncio.wait_for(self._writer_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("ConversationRecorder: writer timed out on close")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _item(self, audio: bytes) -> tuple[float, bytes]:
        """Timestamp = when this frame's audio STARTED (arrival minus its duration)."""
        duration = len(audio) / _BPS / _SAMPLE_RATE
        ts = time.monotonic() - self._start_time - duration
        return (max(ts, 0.0), audio)

    # ── writer ───────────────────────────────────────────────────────────────

    async def _write_loop(self) -> None:
        wav = None
        samples_written = 0
        try:
            wav = wave.open(str(self._path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(_BPS)
            wav.setframerate(_SAMPLE_RATE)

            while True:
                item = await self._queue.get()
                if item is None:
                    break

                ts, audio = item
                target = int(ts * _SAMPLE_RATE)

                # Fill silence up to the frame's position in the timeline
                if target > samples_written:
                    silence = b"\x00" * (target - samples_written) * _BPS
                    wav.writeframes(silence)
                    samples_written = target

                wav.writeframes(audio)
                samples_written += len(audio) // _BPS

        except Exception as exc:
            logger.error("ConversationRecorder: write_loop error: {}", exc)
        finally:
            if wav:
                try:
                    wav.close()
                    logger.debug("ConversationRecorder: saved {}", self._path)
                except Exception as exc:
                    logger.warning("ConversationRecorder: close error: {}", exc)


# ── Frame processors ──────────────────────────────────────────────────────────

class CallerTap(FrameProcessor):
    """Captures InputAudioRawFrame and bot-speaking events for the recorder."""

    def __init__(self, recorder: ConversationRecorder) -> None:
        super().__init__()
        self._recorder = recorder
        self._done = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, InputAudioRawFrame):
                await self._recorder.write_caller(frame.audio)
            elif isinstance(frame, EndFrame) and not self._done:
                self._done = True
                await self._recorder.close()

        # BotStarted/StoppedSpeaking travel UPSTREAM from transport.output
        elif direction == FrameDirection.UPSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._recorder.bot_started()
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._recorder.bot_stopped()

        await self.push_frame(frame, direction)


class BotTap(FrameProcessor):
    """Captures OutputAudioRawFrame for the recorder."""

    def __init__(self, recorder: ConversationRecorder) -> None:
        super().__init__()
        self._recorder = recorder
        self._done = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, OutputAudioRawFrame):
                await self._recorder.write_bot(frame.audio, frame.sample_rate)
            elif isinstance(frame, EndFrame) and not self._done:
                self._done = True
                await self._recorder.close()

        await self.push_frame(frame, direction)

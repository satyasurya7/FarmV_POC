"""Tata Tele Business Services transport adapter.

Tata Tele SmartFlo uses the Twilio Media Streams protocol over WebSocket:
  - All frames are JSON text messages (not binary)
  - Audio codec: G.711 µ-law, 8 kHz, mono, 8-bit (base64-encoded in JSON)
  - Incoming: {"event":"media","media":{"payload":"<base64_ulaw>"},...}
  - Outgoing: {"event":"media","streamSid":"<sid>","media":{"payload":"<base64_ulaw>"}}
  - Call end:  {"event":"stop",...}
"""

from __future__ import annotations

import audioop
import base64
import json

from loguru import logger

from fastapi import WebSocket

from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.config import settings


_SAMPLE_RATE = 8000
_CHANNELS = 1


class TataTeleSerializer(FrameSerializer):
    """Twilio-compatible JSON Media Streams serializer for Tata Tele SmartFlo.

    Incoming JSON → decode base64 µ-law payload → 16-bit PCM InputAudioRawFrame
    Outgoing PCM  → encode to µ-law → base64 → JSON text frame
    """

    def __init__(self, metadata_callback=None) -> None:
        super().__init__()
        self._stream_sid: str | None = None
        # Optional async callback(phone_number, call_sid) fired on 'start' event.
        self._metadata_callback = metadata_callback

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if not isinstance(frame, OutputAudioRawFrame):
            return None

        # Encode 16-bit PCM → 8-bit µ-law → base64
        ulaw = audioop.lin2ulaw(frame.audio, 2)
        payload = base64.b64encode(ulaw).decode("ascii")

        msg = {
            "event": "media",
            "streamSid": self._stream_sid or "",
            "media": {"payload": payload},
        }
        return json.dumps(msg)

    async def deserialize(self, data: str | bytes) -> Frame | None:
        # Tata Tele always sends JSON text frames
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8")
            except Exception:
                return None

        try:
            msg = json.loads(data)
        except Exception:
            return None

        event = msg.get("event")

        if event == "start":
            start = msg.get("start", {})
            self._stream_sid = msg.get("streamSid") or start.get("streamSid")
            fmt = start.get("mediaFormat", {})
            logger.info(
                "TataTeleSerializer: call started streamSid={} format={}",
                self._stream_sid, fmt,
            )
            # Tata Tele / Twilio put caller metadata in start.customParameters
            custom = start.get("customParameters", {})
            phone = (
                custom.get("From") or custom.get("from")
                or start.get("from") or start.get("From")
            )
            call_sid = start.get("callSid") or start.get("call_sid")
            if self._metadata_callback and (phone or call_sid):
                try:
                    await self._metadata_callback(phone_number=phone, call_sid=call_sid)
                except Exception as exc:
                    logger.warning("TataTeleSerializer: metadata_callback error: {}", exc)
            return None

        if event == "media":
            payload_b64 = msg.get("media", {}).get("payload", "")
            if not payload_b64:
                return None
            ulaw = base64.b64decode(payload_b64)
            pcm = audioop.ulaw2lin(ulaw, 2)
            return InputAudioRawFrame(audio=pcm, sample_rate=_SAMPLE_RATE, num_channels=_CHANNELS)

        if event == "stop":
            logger.info("TataTeleSerializer: call stopped — {}", msg.get("stop", {}))
            return EndFrame()

        return None


def create_transport(websocket: WebSocket, metadata_callback=None) -> FastAPIWebsocketTransport:
    """Wrap an already-accepted FastAPI WebSocket as a pipecat transport."""
    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=_SAMPLE_RATE,
            audio_out_sample_rate=_SAMPLE_RATE,
            add_wav_header=False,
            serializer=TataTeleSerializer(metadata_callback=metadata_callback),
            session_timeout=300,
        ),
    )

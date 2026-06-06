"""Generate a Telugu farming question WAV for end-to-end testing.

Uses Cartesia REST API to produce 8 kHz 16-bit mono PCM, saved as
test_user_input.wav in the project root.

Usage:
  python scripts/generate_test_audio.py
"""
import asyncio
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings

TEST_TEXT = "మా పొలంలో వరి పంటకు పచ్చ తెగులు వస్తోంది. దయచేసి చెప్పండి ఏం చేయాలి?"

CARTESIA_TTS_URL = "https://api.cartesia.ai/tts/bytes"


async def main() -> None:
    import aiohttp

    headers = {
        "X-API-Key": settings.cartesia.api_key,
        "Cartesia-Version": "2026-03-01",
        "Content-Type": "application/json",
    }
    body = {
        "model_id": settings.cartesia.model_id,
        "transcript": TEST_TEXT,
        "voice": {"mode": "id", "id": settings.cartesia.voice_id},
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 8000,
        },
    }

    print("Generating Telugu farming question audio via Cartesia...")
    async with aiohttp.ClientSession() as session:
        async with session.post(CARTESIA_TTS_URL, json=body, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Cartesia error {resp.status}: {text}")
                sys.exit(1)
            pcm = await resp.read()

    out = "test_user_input.wav"
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm)

    duration_s = len(pcm) / (8000 * 2)
    print(f"Saved: {out}  ({len(pcm):,} bytes, {duration_s:.1f}s at 8kHz)")


if __name__ == "__main__":
    asyncio.run(main())

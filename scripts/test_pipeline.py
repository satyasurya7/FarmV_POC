"""
End-to-end pipeline test -- simulates a Tata Tele call over WebSocket.

Usage:
  python scripts/test_pipeline.py                   # uses microphone
  python scripts/test_pipeline.py --wav test_greeting.wav   # streams a WAV file as input
"""

import argparse
import asyncio
import sys
import time
import wave

import numpy as np
import sounddevice as sd
import websockets

SERVER_URL = "ws://localhost:8080/ws/smartflo"
SAMPLE_RATE  = 8000
CHUNK_MS     = 20
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)
CHUNK_BYTES  = CHUNK_FRAMES * 2
RECORD_SECS  = 6


def save_wav(path: str, audio_bytes: bytes) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_bytes)


def play_pcm(audio_bytes: bytes) -> None:
    arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(arr, samplerate=SAMPLE_RATE)
    sd.wait()


def load_wav_as_pcm(path: str) -> bytes:
    """Read a WAV file and return raw 8kHz 16-bit mono PCM bytes."""
    import audioop
    with wave.open(path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        src_rate = wf.getframerate()
        src_width = wf.getsampwidth()
        src_channels = wf.getnchannels()
    if src_channels == 2:
        raw = audioop.tomono(raw, src_width, 0.5, 0.5)
    if src_rate != SAMPLE_RATE:
        raw, _ = audioop.ratecv(raw, src_width, 1, src_rate, SAMPLE_RATE, None)
    return raw


async def collect_audio(ws, timeout_no_data: float = 3.0) -> bytes:
    """Collect binary frames from ws until `timeout_no_data` seconds of silence."""
    buf = bytearray()
    while True:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout_no_data)
            if isinstance(msg, bytes):
                buf.extend(msg)
                sys.stdout.write(".")
                sys.stdout.flush()
        except asyncio.TimeoutError:
            break
        except websockets.exceptions.ConnectionClosed:
            break
    return bytes(buf)


async def run(input_wav: str | None) -> None:
    print(f"Connecting to {SERVER_URL} ...")

    async with websockets.connect(SERVER_URL) as ws:
        print("Connected.\n")

        # Phase 1: receive greeting
        print("Waiting for greeting (pipeline startup + TTS ~5s)...")
        t0 = time.monotonic()
        greeting = await collect_audio(ws, timeout_no_data=6.0)
        elapsed = time.monotonic() - t0

        if greeting:
            save_wav("test_greeting.wav", greeting)
            print(f"\nGreeting received: {len(greeting):,} bytes in {elapsed:.1f}s -> test_greeting.wav")
            print("Playing greeting...")
            play_pcm(greeting)
        else:
            print("\nNo greeting audio received -- check server logs.")

        # Phase 2: get user audio (WAV file or microphone)
        print(f"\n{'-'*50}")
        if input_wav:
            print(f"Loading input from: {input_wav}")
            audio_bytes = load_wav_as_pcm(input_wav)
            print(f"Loaded: {len(audio_bytes):,} bytes ({len(audio_bytes)/16000:.1f}s at 8kHz)")
        else:
            print(f"Speak Telugu now -- recording {RECORD_SECS} seconds...")
            print("(Speak any Telugu farming question into the microphone)\n")
            for i in range(3, 0, -1):
                print(f"  Starting in {i}...", end="\r")
                await asyncio.sleep(1)
            print("  [REC] Recording...         ")
            recording = sd.rec(
                int(RECORD_SECS * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            )
            for remaining in range(RECORD_SECS, 0, -1):
                print(f"  {remaining}s remaining...", end="\r")
                await asyncio.sleep(1)
            sd.wait()
            print("  Recording done.          \n")
            audio_bytes = recording.tobytes()

        save_wav("test_input.wav", audio_bytes)
        print(f"Input saved: {len(audio_bytes):,} bytes -> test_input.wav")

        # Phase 3: stream audio to agent + receive response
        print("\nStreaming to agent...")
        t_send = time.monotonic()

        response_audio = bytearray()

        async def send_audio():
            for i in range(0, len(audio_bytes), CHUNK_BYTES):
                await ws.send(audio_bytes[i : i + CHUNK_BYTES])
                await asyncio.sleep(CHUNK_MS / 1000)
            # trailing silence: VAD stop_secs=0.8, so 200*20ms=4s ensures finalize fires
            silence = bytes(CHUNK_BYTES)
            for _ in range(200):
                await ws.send(silence)
                await asyncio.sleep(CHUNK_MS / 1000)

        async def recv_response():
            deadline = asyncio.get_event_loop().time() + 35.0
            while asyncio.get_event_loop().time() < deadline:
                remaining_time = deadline - asyncio.get_event_loop().time()
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining_time, 3.0))
                    if isinstance(msg, bytes):
                        response_audio.extend(msg)
                        sys.stdout.write(".")
                        sys.stdout.flush()
                except asyncio.TimeoutError:
                    if response_audio:
                        break
                except websockets.exceptions.ConnectionClosed:
                    break

        await asyncio.gather(send_audio(), recv_response())

        elapsed_response = time.monotonic() - t_send
        print(f"\nRound-trip: {elapsed_response:.1f}s")

        # Phase 4: play and save response
        if response_audio:
            save_wav("test_response.wav", bytes(response_audio))
            print(f"Response received: {len(response_audio):,} bytes -> test_response.wav")
            print("\nPlaying agent response...")
            play_pcm(bytes(response_audio))
        else:
            print("No response audio -- check server logs for STT/LLM errors.")

        print(f"\n{'-'*50}")
        print("Test complete. WAV files: test_greeting.wav  test_input.wav  test_response.wav")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Farm Vaidya end-to-end pipeline test")
    parser.add_argument("--wav", help="Path to a WAV file to use as input instead of microphone")
    args = parser.parse_args()
    asyncio.run(run(input_wav=args.wav))

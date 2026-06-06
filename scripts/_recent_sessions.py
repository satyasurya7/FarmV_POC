import asyncio, sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from src.config import settings
import asyncpg

async def main():
    pool = await asyncpg.create_pool(dsn=settings.db.dsn, min_size=1, max_size=2)

    print("=== RECENT SESSIONS ===")
    sessions = await pool.fetch("""
        SELECT id::text, caller_name, phone_number, status, started_at, ended_at, duration_s
        FROM call_sessions ORDER BY started_at DESC LIMIT 5
    """)
    for s in sessions:
        print(f"\nID: {s['id']}")
        print(f"  name={s['caller_name']}  phone={s['phone_number']}")
        print(f"  status={s['status']}  started={s['started_at']}  duration={s['duration_s']}s")

    if sessions:
        sid = sessions[0]['id']
        print(f"\n=== UTTERANCES for {sid[:8]}... ===")
        utts = await pool.fetch("""
            SELECT turn_number, text FROM utterances WHERE session_id=$1::uuid ORDER BY turn_number
        """, sid)
        for u in utts:
            print(f"  Turn {u['turn_number']}: {u['text']}")

        print(f"\n=== ERRORS for {sid[:8]}... ===")
        errs = await pool.fetch("""
            SELECT error_type, error_message, created_at FROM error_logs WHERE session_id=$1::uuid ORDER BY created_at
        """, sid)
        for e in errs:
            print(f"  [{e['created_at']}] {e['error_type']}: {e['error_message']}")

        print(f"\n=== AGENT RESPONSES for {sid[:8]}... ===")
        resps = await pool.fetch("""
            SELECT turn_number, text, llm_latency_ms, tts_latency_ms FROM agent_responses WHERE session_id=$1::uuid ORDER BY turn_number
        """, sid)
        for r in resps:
            print(f"  Turn {r['turn_number']}: {r['text'][:100]}")
            print(f"    LLM={r['llm_latency_ms']}ms  TTS={r['tts_latency_ms']}ms")

    await pool.close()

asyncio.run(main())

import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

async def main():
    from src.database import get_pool
    pool = await get_pool()
    result = await pool.execute(
        "UPDATE call_sessions SET ended_at=NOW(), "
        "duration_s=EXTRACT(EPOCH FROM (NOW()-started_at)), "
        "status='dropped' WHERE status='active'"
    )
    print("Cleaned up:", result)
    rows = await pool.fetch("SELECT id::text, status FROM call_sessions WHERE status='active'")
    print("Remaining active sessions:", len(rows))

asyncio.run(main())

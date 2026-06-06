import asyncio, sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from src.config import settings
import asyncpg

async def main():
    pool = await asyncpg.create_pool(dsn=settings.db.dsn, min_size=1, max_size=2)
    rows = await pool.fetch(
        "SELECT content, metadata FROM knowledge_chunks ORDER BY source_file, chunk_index LIMIT 80"
    )
    for r in rows:
        meta = json.loads(r['metadata']) if r['metadata'] else {}
        qnum = meta.get('question_number', meta.get('section_header', ''))
        print(f"{qnum} | {r['content'][:150]}")
    total = await pool.fetchval('SELECT COUNT(*) FROM knowledge_chunks')
    print(f"\nTotal chunks: {total}")
    await pool.close()

asyncio.run(main())

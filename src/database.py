import asyncio
import asyncpg
from loguru import logger
from src.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        for attempt in range(1, 11):
            try:
                _pool = await asyncpg.create_pool(
                    dsn=settings.db.dsn,
                    min_size=2,
                    max_size=20,
                    command_timeout=30,
                    ssl=False,
                )
                logger.info("DB pool created (host={})", settings.db.host)
                break
            except Exception as exc:
                if attempt == 10:
                    raise
                wait = min(2 ** attempt, 30)
                logger.warning("DB not ready (attempt {}/10), retrying in {}s: {}", attempt, wait, exc)
                await asyncio.sleep(wait)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed")

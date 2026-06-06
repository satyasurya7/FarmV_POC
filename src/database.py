import asyncpg
from loguru import logger
from src.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.db.dsn,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        logger.info("DB pool created (host={})", settings.db.host)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed")

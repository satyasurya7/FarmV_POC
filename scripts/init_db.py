"""One-time database initialisation script.

Usage:
    python -m scripts.init_db

Reads schema.sql and executes it against the configured PostgreSQL instance.
Safe to run multiple times (all DDL uses IF NOT EXISTS).
"""

import asyncio
import sys
from pathlib import Path

import asyncpg
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import settings

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


async def main() -> None:
    logger.info("Connecting to PostgreSQL at {}", settings.db.host)
    conn = await asyncpg.connect(dsn=settings.db.dsn)
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await conn.execute(schema_sql)
        logger.info("Schema applied successfully")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Knowledge base ingestion script.

Usage:
    python -m scripts.ingest_kb --source knowledge_base/

Loads files from the knowledge_base directory, chunks them, generates
embeddings via Vertex AI, and upserts into the knowledge_chunks table.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from src.config import settings  # noqa: F401  (triggers .env load)
from src.database import close_pool, get_pool
from src.rag.ingestion import ingest_directory


async def main(source_dir: Path) -> None:
    if not source_dir.exists():
        logger.error("Source directory not found: {}", source_dir)
        sys.exit(1)

    pool = await get_pool()
    try:
        total = await ingest_directory(pool, source_dir)
        logger.info("Ingestion complete — {} chunks stored", total)
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Farm Vaidya knowledge base")
    parser.add_argument("--source", default="knowledge_base/", help="Directory containing KB files")
    args = parser.parse_args()

    asyncio.run(main(Path(args.source)))

"""Vertex AI embedding generation — text-multilingual-embedding-002.

Supports Telugu and handles batching for bulk ingestion.
"""

from __future__ import annotations

import asyncio
from typing import List

import tiktoken
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings


def _get_client():
    from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput  # type: ignore  # noqa: F401
    import vertexai

    vertexai.init(project=settings.vertex.project, location=settings.vertex.region)
    return TextEmbeddingModel.from_pretrained(settings.vertex.embedding_model)


_model = None


def _model_instance():
    global _model
    if _model is None:
        _model = _get_client()
    return _model


_tok = tiktoken.get_encoding("cl100k_base")
MAX_TOKENS_PER_BATCH = 15_000  # Vertex AI limit is 20k; leave headroom


def _token_batches(texts: List[str]) -> List[List[str]]:
    """Split texts into batches that each stay under MAX_TOKENS_PER_BATCH."""
    batches, current, current_tokens = [], [], 0
    for t in texts:
        t_tokens = len(_tok.encode(t))
        if current and current_tokens + t_tokens > MAX_TOKENS_PER_BATCH:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(t)
        current_tokens += t_tokens
    if current:
        batches.append(current)
    return batches


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _embed_batch(batch: List[str]) -> List[List[float]]:
    from vertexai.language_models import TextEmbeddingInput  # type: ignore
    loop = asyncio.get_event_loop()
    model = _model_instance()
    # TextEmbeddingInput avoids the deprecated string-list code path (removal: 2026-06-24)
    inputs = [TextEmbeddingInput(text=t, task_type="RETRIEVAL_QUERY") for t in batch]
    embeddings = await loop.run_in_executor(None, lambda b=inputs: model.get_embeddings(b))
    return [e.values for e in embeddings]


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Return embeddings for a list of texts using token-aware batching."""
    batches = _token_batches(texts)
    results: List[List[float]] = []
    done = 0
    for batch in batches:
        results.extend(await _embed_batch(batch))
        done += len(batch)
        logger.info("Embedded {}/{} chunks", done, len(texts))
    return results


async def embed_query(text: str) -> List[float]:
    """Return embedding for a single query string."""
    results = await embed_texts([text])
    return results[0]

"""Knowledge base chunking.

Strategy by file type:
- .docx  → Q&A pair extraction (question number + answer = one chunk)
- .csv   → one chunk per row
- .txt / .md → sliding window (300 tokens, 50-token overlap)

The Farm Vaidya KB is a .docx with numbered Q&A pairs in Telugu.
Each question + its answer paragraphs form one self-contained chunk.
"""

from __future__ import annotations

import re
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

import tiktoken

from src.config import settings

_enc = tiktoken.get_encoding("cl100k_base")

# Matches question numbers like "1.2", "1.23.1", "109.3", "111.4" at line start
_Q_NUMBER_RE = re.compile(r"^\d+(\.\d+)+[\.\s]")


@dataclass
class Chunk:
    content: str
    source_file: str
    chunk_index: int
    metadata: dict


# ── helpers ──────────────────────────────────────────────────────────────────

def _token_len(text: str) -> int:
    return len(_enc.encode(text))


def _sliding_window(text: str, source: str) -> List[Chunk]:
    tokens = _enc.encode(text)
    size = settings.rag.chunk_size_tokens
    overlap = settings.rag.chunk_overlap_tokens
    chunks, idx, i = [], 0, 0
    while i < len(tokens):
        chunk_text = _enc.decode(tokens[i : i + size])
        if chunk_text.strip():
            chunks.append(Chunk(content=chunk_text, source_file=source, chunk_index=idx, metadata={}))
            idx += 1
        i += size - overlap
    return chunks


# ── docx Q&A chunker ─────────────────────────────────────────────────────────

def _is_question(text: str) -> bool:
    """Return True if paragraph looks like a numbered question."""
    return bool(_Q_NUMBER_RE.match(text.strip()))


def _extract_question_number(text: str) -> str:
    m = _Q_NUMBER_RE.match(text.strip())
    return m.group(0).strip(" .") if m else ""


def chunk_docx(path: Path) -> List[Chunk]:
    """Extract Q&A pairs from a Word document.

    Each (question + following answer paragraphs) is one chunk.
    Section headers and non-Q paragraphs that don't belong to a
    question are collected as a standalone chunk.
    """
    from docx import Document  # lazy import; only needed for docx files

    doc = Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    chunks: List[Chunk] = []
    idx = 0
    i = 0

    while i < len(paras):
        if _is_question(paras[i]):
            q_text = paras[i]
            q_num = _extract_question_number(q_text)
            answer_parts: List[str] = []
            i += 1
            # Collect answer paragraphs until the next question or end
            while i < len(paras) and not _is_question(paras[i]):
                answer_parts.append(paras[i])
                i += 1

            content = q_text
            if answer_parts:
                content += "\n" + "\n".join(answer_parts)

            chunks.append(
                Chunk(
                    content=content,
                    source_file=path.name,
                    chunk_index=idx,
                    metadata={"question_number": q_num, "question": q_text[:200]},
                )
            )
            idx += 1
        else:
            # Section header or orphan paragraph — keep as small standalone chunk
            chunks.append(
                Chunk(
                    content=paras[i],
                    source_file=path.name,
                    chunk_index=idx,
                    metadata={"section_header": True},
                )
            )
            idx += 1
            i += 1

    return chunks


# ── csv chunker ───────────────────────────────────────────────────────────────

def chunk_csv(path: Path) -> List[Chunk]:
    chunks = []
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            content = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
            chunks.append(Chunk(content=content, source_file=path.name, chunk_index=idx, metadata=dict(row)))
    return chunks


# ── plain text chunker ────────────────────────────────────────────────────────

def chunk_text(path: Path) -> List[Chunk]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _sliding_window(text, path.name)


# ── public entry point ────────────────────────────────────────────────────────

def chunk_file(path: Path) -> List[Chunk]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return chunk_docx(path)
    elif suffix == ".csv":
        return chunk_csv(path)
    else:  # .txt, .md, fallback
        return chunk_text(path)

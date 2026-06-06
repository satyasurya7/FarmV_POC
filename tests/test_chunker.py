"""Unit tests for RAG chunker — no credentials required."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag.chunker import chunk_docx, chunk_text, _is_question, _extract_question_number, Chunk

KB_PATH = Path(__file__).parent.parent / "knowledge_base" / "rythunestam_kb_v1.4.docx"


# ── _is_question ──────────────────────────────────────────────────────────────

def test_is_question_simple():
    assert _is_question("1.2. ఈ స్థితి ఎందుకు వచ్చింది సార్?") is True

def test_is_question_deep_number():
    assert _is_question("109.3 చేపల చెరువులలో సున్నం...") is True

def test_is_question_section_header():
    # Section header starts with "1." but has no sub-number
    assert _is_question("1.ప్రస్తుత వ్యవసాయ పరిస్థితులు") is False

def test_is_question_plain_sentence():
    assert _is_question("చాలా ఆందోళనకరంగా ఉంది.") is False


# ── _extract_question_number ──────────────────────────────────────────────────

def test_extract_question_number():
    assert _extract_question_number("1.2. ఏదైనా") == "1.2"

def test_extract_question_number_long():
    assert _extract_question_number("109.3 చేపల") == "109.3"


# ── chunk_docx ────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_produces_chunks():
    chunks = chunk_docx(KB_PATH)
    assert len(chunks) > 2000, f"Expected >2000 chunks, got {len(chunks)}"

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_qa_chunks_have_metadata():
    chunks = chunk_docx(KB_PATH)
    qa_chunks = [c for c in chunks if "question_number" in c.metadata]
    assert len(qa_chunks) > 2000
    for c in qa_chunks[:10]:
        assert c.metadata["question_number"]
        assert c.metadata["question"]

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_content_nonempty():
    chunks = chunk_docx(KB_PATH)
    for c in chunks:
        assert c.content.strip(), f"Chunk {c.chunk_index} has empty content"

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_indexes_sequential():
    chunks = chunk_docx(KB_PATH)
    for i, c in enumerate(chunks):
        assert c.chunk_index == i

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_answer_appended_to_question():
    chunks = chunk_docx(KB_PATH)
    qa = [c for c in chunks if "question_number" in c.metadata]
    # For non-trivial Q&A chunks, content should be longer than just the question
    nontrivial = [c for c in qa if "\n" in c.content]
    assert len(nontrivial) > 100, "Expected most chunks to include answer text"


# ── chunk_text (sliding window) ───────────────────────────────────────────────

def test_chunk_text_sliding_window(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("word " * 1000, encoding="utf-8")
    chunks = chunk_text(f)
    assert len(chunks) > 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.content.strip()

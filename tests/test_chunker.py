"""Unit tests for RAG chunker — no credentials or KB file required."""

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag.chunker import (
    Chunk,
    _extract_question_number,
    _is_question,
    _sliding_window,
    chunk_csv,
    chunk_docx,
    chunk_file,
    chunk_text,
)

KB_PATH = Path(__file__).parent.parent / "knowledge_base" / "rythunestam_kb_v1.4.docx"

# ── _is_question ──────────────────────────────────────────────────────────────

def test_is_question_simple():
    assert _is_question("1.2. ఈ స్థితి ఎందుకు వచ్చింది సార్?") is True

def test_is_question_deep_number():
    assert _is_question("109.3 చేపల చెరువులలో సున్నం...") is True

def test_is_question_three_level():
    assert _is_question("1.2.3 మరో ప్రశ్న") is True

def test_is_question_space_after_number():
    assert _is_question("12.5 text") is True

def test_is_question_section_header_no_sub():
    # "1." without a sub-number must NOT match
    assert _is_question("1.ప్రస్తుత వ్యవసాయ పరిస్థితులు") is False

def test_is_question_plain_sentence():
    assert _is_question("చాలా ఆందోళనకరంగా ఉంది.") is False

def test_is_question_bare_integer():
    assert _is_question("12 some text") is False

def test_is_question_empty_string():
    assert _is_question("") is False

def test_is_question_leading_whitespace_stripped():
    assert _is_question("  1.2. question text") is True


# ── _extract_question_number ──────────────────────────────────────────────────

def test_extract_question_number_with_dot_separator():
    assert _extract_question_number("1.2. ఏదైనా") == "1.2"

def test_extract_question_number_with_space_separator():
    assert _extract_question_number("109.3 చేపల") == "109.3"

def test_extract_question_number_three_levels():
    assert _extract_question_number("1.2.3 text") == "1.2.3"

def test_extract_question_number_no_match_returns_empty():
    assert _extract_question_number("plain text") == ""

def test_extract_question_number_section_header_returns_empty():
    assert _extract_question_number("1.ప్రస్తుత") == ""


# ── chunk_docx with mocked Document ──────────────────────────────────────────

def _mock_doc(paragraphs: list[str]) -> MagicMock:
    doc = MagicMock()
    doc.paragraphs = [MagicMock(text=p) for p in paragraphs]
    return doc


def test_chunk_docx_qa_pair_creates_single_chunk():
    paragraphs = [
        "1.2. ఈ స్థితి ఎందుకు వచ్చింది?",
        "ఇది సాధారణ వ్యవసాయ సమస్య.",
    ]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert len(chunks) == 1
    assert "1.2." in chunks[0].content
    assert "సాధారణ వ్యవసాయ సమస్య" in chunks[0].content


def test_chunk_docx_multi_paragraph_answer_joined():
    paragraphs = [
        "1.2. ప్రశ్న?",
        "మొదటి సమాధానం భాగం.",
        "రెండవ సమాధానం భాగం.",
    ]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert len(chunks) == 1
    assert "మొదటి" in chunks[0].content
    assert "రెండవ" in chunks[0].content


def test_chunk_docx_two_questions_two_chunks():
    paragraphs = [
        "1.1. మొదటి ప్రశ్న?",
        "మొదటి సమాధానం.",
        "1.2. రెండవ ప్రశ్న?",
        "రెండవ సమాధానం.",
    ]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert len(chunks) == 2


def test_chunk_docx_section_header_is_standalone_chunk():
    paragraphs = [
        "Section Title",         # not a question → standalone chunk
        "1.1. ప్రశ్న?",
        "సమాధానం.",
    ]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert len(chunks) == 2
    assert chunks[0].metadata.get("section_header") is True


def test_chunk_docx_metadata_has_question_number():
    paragraphs = ["1.2. ఈ స్థితి?", "సమాధానం."]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert chunks[0].metadata["question_number"] == "1.2"


def test_chunk_docx_metadata_has_question_text():
    paragraphs = ["1.2. ఈ స్థితి ఎందుకు వచ్చింది?", "సమాధానం."]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    assert "1.2." in chunks[0].metadata["question"]


def test_chunk_docx_indexes_sequential():
    paragraphs = [
        "1.1. మొదటి?", "సమాధానం 1.",
        "1.2. రెండవ?", "సమాధానం 2.",
        "1.3. మూడవ?", "సమాధానం 3.",
    ]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_chunk_docx_empty_paragraphs_skipped():
    paragraphs = ["", "   ", "1.1. ప్రశ్న?", "", "సమాధానం."]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("fake.docx"))
    # Only 1 Q&A chunk — empty paragraphs are stripped and ignored
    assert len(chunks) == 1


def test_chunk_docx_source_file_set():
    paragraphs = ["1.1. ప్రశ్న?", "సమాధానం."]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_docx(Path("kb_v1.4.docx"))
    assert chunks[0].source_file == "kb_v1.4.docx"


# ── chunk_docx with real KB file (skipped when absent) ───────────────────────

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_real_produces_chunks():
    chunks = chunk_docx(KB_PATH)
    assert len(chunks) > 2000

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_real_qa_chunks_have_metadata():
    chunks = chunk_docx(KB_PATH)
    qa = [c for c in chunks if "question_number" in c.metadata]
    assert len(qa) > 2000
    for c in qa[:10]:
        assert c.metadata["question_number"]

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_real_content_nonempty():
    for c in chunk_docx(KB_PATH):
        assert c.content.strip()

@pytest.mark.skipif(not KB_PATH.exists(), reason="KB file not present")
def test_chunk_docx_real_answer_appended():
    qa = [c for c in chunk_docx(KB_PATH) if "question_number" in c.metadata]
    with_answers = [c for c in qa if "\n" in c.content]
    assert len(with_answers) > 100


# ── chunk_csv ─────────────────────────────────────────────────────────────────

def test_chunk_csv_count(tmp_path):
    f = tmp_path / "crops.csv"
    f.write_text("variety,duration\nBPT 2537,165\nMTU 7029,130\n", encoding="utf-8")
    chunks = chunk_csv(f)
    assert len(chunks) == 2


def test_chunk_csv_content_format(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nfoo,bar\n", encoding="utf-8")
    chunks = chunk_csv(f)
    assert "name: foo" in chunks[0].content
    assert "value: bar" in chunks[0].content


def test_chunk_csv_metadata_is_row_dict(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("variety,yield\nBPT 2537,6.5\n", encoding="utf-8")
    chunks = chunk_csv(f)
    assert chunks[0].metadata["variety"] == "BPT 2537"
    assert chunks[0].metadata["yield"] == "6.5"


def test_chunk_csv_empty_values_excluded_from_content(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b,c\nfoo,,bar\n", encoding="utf-8")
    chunks = chunk_csv(f)
    # Empty "b" field should be excluded from content
    assert "b:" not in chunks[0].content
    assert "a: foo" in chunks[0].content
    assert "c: bar" in chunks[0].content


def test_chunk_csv_indexes_sequential(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("x\n1\n2\n3\n", encoding="utf-8")
    chunks = chunk_csv(f)
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_chunk_csv_source_file(tmp_path):
    f = tmp_path / "kb.csv"
    f.write_text("x\nhello\n", encoding="utf-8")
    chunks = chunk_csv(f)
    assert chunks[0].source_file == "kb.csv"


# ── chunk_text / _sliding_window ─────────────────────────────────────────────

def test_chunk_text_produces_chunks(tmp_path):
    f = tmp_path / "text.txt"
    f.write_text("word " * 1000, encoding="utf-8")
    chunks = chunk_text(f)
    assert len(chunks) > 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.content.strip()


def test_sliding_window_overlap_produces_multiple_chunks():
    # 1000 tokens at size=300/overlap=50 → ceil((1000-50)/(300-50)) ≈ 3.8 → at least 4 chunks
    text = "word " * 1000
    chunks = _sliding_window(text, "test.txt")
    assert len(chunks) >= 3


def test_sliding_window_short_text_single_chunk():
    text = "hello world"  # well under 300 tokens
    chunks = _sliding_window(text, "test.txt")
    assert len(chunks) == 1
    assert "hello" in chunks[0].content


def test_sliding_window_indexes_sequential():
    text = "word " * 1000
    chunks = _sliding_window(text, "test.txt")
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_sliding_window_source_file_set():
    chunks = _sliding_window("hello world", "myfile.txt")
    assert chunks[0].source_file == "myfile.txt"


def test_chunk_text_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    chunks = chunk_text(f)
    assert chunks == []


# ── chunk_file dispatcher ─────────────────────────────────────────────────────

def test_chunk_file_routes_csv(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col\nval\n", encoding="utf-8")
    chunks = chunk_file(f)
    assert len(chunks) == 1


def test_chunk_file_routes_txt(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello world", encoding="utf-8")
    chunks = chunk_file(f)
    assert len(chunks) >= 1


def test_chunk_file_routes_md(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Title\n\nSome text.", encoding="utf-8")
    chunks = chunk_file(f)
    assert len(chunks) >= 1


def test_chunk_file_routes_docx():
    paragraphs = ["1.1. ప్రశ్న?", "సమాధానం."]
    with patch("docx.Document", return_value=_mock_doc(paragraphs)):
        chunks = chunk_file(Path("kb.docx"))
    assert len(chunks) == 1

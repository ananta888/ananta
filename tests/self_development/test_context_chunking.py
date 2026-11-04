"""Unit tests for agent.services.context_chunking.chunk_text (CMP-001)."""
from __future__ import annotations

import pytest

from agent.services.context_chunking import chunk_text


def test_empty_input_returns_empty_list():
    assert chunk_text("", 10) == []
    assert chunk_text("", 1) == []
    assert chunk_text("", 5, overlap=2) == []


def test_happy_path_no_overlap_reassembles_exactly():
    text = "Hello world this is a test"
    chunks = chunk_text(text, 10)
    assert all(len(c) <= 10 for c in chunks)
    assert "".join(chunks) == text


def test_happy_path_single_chunk_when_fits():
    assert chunk_text("short", 20) == ["short"]


def test_exact_fit_no_split():
    assert chunk_text("12345", 5) == ["12345"]


def test_whitespace_preferred_boundary():
    # "Hello world" with max_chars=6: last whitespace within first 6 chars is at index 5 (space).
    # Segment = "Hello " (len 6). Break after space -> chunk1="Hello ", chunk2 starts at 6.
    chunks = chunk_text("Hello world", 6)
    assert len(chunks) == 2
    assert chunks[0] == "Hello "
    assert chunks[1] == "world"
    assert "".join(chunks) == "Hello world"


def test_whitespace_preferred_boundary_multiple_spaces():
    text = "one two three four"
    chunks = chunk_text(text, 8)
    # First segment: "one two 
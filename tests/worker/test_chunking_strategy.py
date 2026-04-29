from __future__ import annotations

from worker.retrieval.chunking import split_into_chunks


def test_chunking_is_deterministic_for_identical_content() -> None:
    content = "class Service:\n    pass\n\ndef run():\n    return 1\n"
    one = split_into_chunks(path="src/service.py", content=content, max_chunk_bytes=32)
    two = split_into_chunks(path="src/service.py", content=content, max_chunk_bytes=32)
    assert one == two


def test_chunking_falls_back_for_unknown_file_types() -> None:
    chunks = split_into_chunks(path="README.unknown", content="hello\nworld\n", max_chunk_bytes=8)
    assert len(chunks) >= 1
    assert chunks[0]["metadata"]["language"] == "text"


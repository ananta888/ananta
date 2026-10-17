from __future__ import annotations

from agent.codecompass.index_header import strip_index_header


def test_strips_path_and_topic_header_lines() -> None:
    content = "# agent/x.py\n# Themen: alpha, beta\ndef target():\n    pass\n"

    source, header_lines = strip_index_header(content, "agent/x.py")

    assert header_lines == 2
    assert source == "def target():\n    pass\n"


def test_strips_path_only_header() -> None:
    content = "# src/example.java\npackage demo;\n"

    source, header_lines = strip_index_header(content, "src/example.java")

    assert header_lines == 1
    assert source == "package demo;\n"


def test_leaves_records_without_the_matching_header_untouched() -> None:
    content = "def target():\n    pass\n"

    source, header_lines = strip_index_header(content, "agent/x.py")

    assert header_lines == 0
    assert source == content


def test_does_not_strip_when_the_path_does_not_match() -> None:
    content = "# other/path.py\ndef target():\n    pass\n"

    source, header_lines = strip_index_header(content, "agent/x.py")

    assert header_lines == 0
    assert source == content

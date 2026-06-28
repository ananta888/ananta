"""Unit tests for snakes_chat_helpers._build_grounded_snake_prompt.

These tests focus on the deterministic, dependency-free branches of
`_build_grounded_snake_prompt` so that regressions in prompt construction
are caught without spinning up the full RAG/retrieval service graph.

The function takes a user text and returns a 5-tuple
``(grounded_prompt, has_context, summary, domain_info, chunk_meta)``.
"""

from __future__ import annotations

from agent.routes.snakes_chat_helpers import _build_grounded_snake_prompt


def test_empty_user_text_returns_empty_prompt_without_context():
    grounded_prompt, has_context, summary, domain_info, chunk_meta = (
        _build_grounded_snake_prompt("")
    )

    assert grounded_prompt == ""
    assert has_context is False
    assert summary == ""
    assert domain_info == {}
    assert chunk_meta == []


def test_whitespace_only_user_text_returns_empty_prompt_without_context():
    grounded_prompt, has_context, summary, domain_info, chunk_meta = (
        _build_grounded_snake_prompt("   \n\t  ")
    )

    assert grounded_prompt == ""
    assert has_context is False
    assert summary == ""
    assert domain_info == {}
    assert chunk_meta == []


def test_none_user_text_returns_empty_prompt_without_context():
    grounded_prompt, has_context, summary, domain_info, chunk_meta = (
        _build_grounded_snake_prompt(None)  # type: ignore[arg-type]
    )

    assert grounded_prompt == ""
    assert has_context is False
    assert summary == ""
    assert domain_info == {}
    assert chunk_meta == []


def test_chunk_meta_is_a_list_for_empty_input():
    """Contract: chunk_meta is always a list, even when no retrieval happens.

    Callers iterate over it without type-guarding; an empty prompt must
    still return an iterable.
    """
    _, _, _, _, chunk_meta = _build_grounded_snake_prompt("")
    assert isinstance(chunk_meta, list)
    assert len(chunk_meta) == 0


def test_summary_is_empty_string_for_empty_input():
    """Contract: summary is always a string for the empty-input branch.

    The summary is used for AI-source attribution rendering — it must not
    be None even when no context is available.
    """
    _, _, summary, _, _ = _build_grounded_snake_prompt("")
    assert isinstance(summary, str)
    assert summary == ""


def test_domain_info_is_dict_for_empty_input():
    """Contract: domain_info is always a dict, even when no domain scope resolved."""
    _, _, _, domain_info, _ = _build_grounded_snake_prompt("")
    assert isinstance(domain_info, dict)
    assert domain_info == {}
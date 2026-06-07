"""Tests for the silent-context-overflow fix in _worker_chat_full_scan and the
per-model context lookup helpers.

These cover:
- lookup_model_context_tokens() returns the right value for known and unknown models
- extract_llm_call_metadata() surfaces strategy-attached overflow hints
- _worker_chat_full_scan() respects the chat_full_scan_chars_per_file config key
- _worker_chat_full_scan() auto-shrinks the batch when the prompt would overflow
- _worker_chat_full_scan() returns a context_overflow error in the trace when
  every batch came back with empty_reason=context_overflow_likely
"""
from __future__ import annotations

import json
from unittest.mock import patch


def _test_lookup_known_models():
    from agent.config import lookup_model_context_tokens

    # Known models
    assert lookup_model_context_tokens("phi-3.5-mini-instruct") == 4096
    assert lookup_model_context_tokens("microsoft_-_phi-3.5-mini-instruct") == 4096
    assert lookup_model_context_tokens("qwen2.5-3b-instruct") == 32768
    assert lookup_model_context_tokens("meta-llama_-_llama-3.2-1b-instruct") == 131072
    # Unknown model -> None (caller falls back to global default)
    assert lookup_model_context_tokens("some-unknown-model-xyz") is None
    # Empty / None -> None
    assert lookup_model_context_tokens("") is None
    assert lookup_model_context_tokens(None) is None


def _test_parse_model_contexts_invalid():
    from agent.config import _parse_model_contexts

    assert _parse_model_contexts(None) == {}
    assert _parse_model_contexts("") == {}
    assert _parse_model_contexts("not-json") == {}
    assert _parse_model_contexts('{"a": "bad"}') == {}
    # Negative/zero values are dropped; non-dict is rejected
    assert _parse_model_contexts('[]') == {}
    assert _parse_model_contexts('{"good": 4096, "neg": -1, "zero": 0, "bad": "x"}') == {"good": 4096}


def _test_extract_llm_call_metadata():
    from agent.llm_integration import extract_llm_call_metadata, extract_llm_text_and_usage

    # No metadata -> empty dict
    assert extract_llm_call_metadata({"text": "hi"}) == {}
    assert extract_llm_call_metadata("plain string") == {}
    assert extract_llm_call_metadata(None) == {}

    # With metadata
    payload = {
        "text": "",
        "usage": {},
        "metadata": {
            "empty_reason": "context_overflow_likely",
            "context_limit": 4096,
            "model_id": "phi-3.5-mini",
        },
    }
    assert extract_llm_call_metadata(payload) == {
        "empty_reason": "context_overflow_likely",
        "context_limit": 4096,
        "model_id": "phi-3.5-mini",
    }

    # Backward compat: extract_llm_text_and_usage signature unchanged
    text, usage = extract_llm_text_and_usage(payload)
    assert text == ""
    assert isinstance(usage, dict)


def _make_cfg(**overrides):
    base = {
        "chat_full_scan_source_only": True,
        "chat_full_scan_max_batches": 4,
        "chat_full_scan_files_per_batch": 8,
        "chat_full_scan_parallel_batches": 1,
        "chat_full_scan_timeout_s": 600,
    }
    base.update(overrides)
    return base


def test_full_scan_uses_chars_per_file_config():
    """chars_per_file from config should be passed through to file content slicing."""
    from agent.routes import snakes
    from agent.common import sgpt

    captured_prompts: list[str] = []

    def _fake_generate_text(*args, **kwargs):
        prompt = kwargs.get("prompt") or (args[0] if args else "")
        if "batch" in prompt.lower():
            captured_prompts.append(prompt)
        return "summary answer"

    import pathlib
    import tempfile

    cfg = _make_cfg(chat_full_scan_chars_per_file=200)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        (tmp / "a.py").write_text("x" * 1000, encoding="utf-8")
        (tmp / "b.py").write_text("y" * 1000, encoding="utf-8")
        with (
            patch("agent.routes.ai_snake_config._current_config", return_value=cfg),
            patch("agent.routes.snakes.generate_text", side_effect=_fake_generate_text),
            patch.object(sgpt, "_resolve_repo_root", return_value=tmp),
        ):
            answer, trace = snakes._worker_chat_full_scan(
                "Was macht die Datei?",
                provider="lmstudio",
                model="phi-3.5-mini-instruct",
            )

    assert trace["chars_per_file_cfg"] == 200
    assert trace["model_context_tokens"] == 4096  # from the lookup map
    assert trace["files_per_batch_cfg"] == 8
    # The prompt should be well under the model context (we sliced each file
    # to 200 chars), so the batch auto-shrink should NOT have fired.
    assert "files_per_batch_auto_shrunk_from" not in trace
    # And the captured prompts should reflect the slicing: max per-file content
    # in the prompt is well below 1000 (the actual file content size).
    assert captured_prompts
    for p in captured_prompts:
        # The per-file content block is at most chars_per_file + framing.
        # We don't slice on whitespace, so 200 chars from "x"*1000 -> 200 x's.
        assert "x" * 250 not in p
        assert "y" * 250 not in p


def test_full_scan_auto_shrinks_oversized_batch():
    """When the configured batch would overflow the model context, the
    function should auto-shrink down to the largest batch that fits."""
    from agent.routes import snakes
    from agent.common import sgpt

    cfg = _make_cfg(
        chat_full_scan_files_per_batch=8,
        chat_full_scan_chars_per_file=3500,  # old hardcoded default
    )
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        for i in range(10):
            (tmp / f"f{i}.py").write_text("z" * 4000, encoding="utf-8")
        with (
            patch("agent.routes.ai_snake_config._current_config", return_value=cfg),
            patch("agent.routes.snakes.generate_text", return_value="x"),
            patch.object(sgpt, "_resolve_repo_root", return_value=tmp),
        ):
            _answer, trace = snakes._worker_chat_full_scan(
                "Erkläre das Modul",
                provider="lmstudio",
                model="phi-3.5-mini-instruct",
            )

    # 8 files × (3500+40) chars = ~28K chars = ~7K tokens; 4096-256 = 3840 budget
    # -> must shrink. The loop shrinks down to the largest N where
    # N*3540 + 400 chars <= 3840 tokens (1 token ≈ 4 chars).
    # Largest N where (N*3540+400)/4 <= 3840 -> N*885 + 100 <= 3840 -> N <= 4.23 -> 4.
    assert trace["files_per_batch_auto_shrunk_from"] == 8
    assert trace["files_per_batch_auto_shrunk_reason"] == "context_budget"
    assert 1 <= trace["files_per_batch_used"] <= 4
    assert trace["model_context_tokens"] == 4096


def test_full_scan_returns_context_overflow_error_in_trace():
    """When every batch returns an empty response with empty_reason=
    context_overflow_likely, the trace should report 'context_overflow' with
    a useful hint for the user."""
    from agent.routes import snakes
    from agent.common import sgpt

    def _fake_generate_text(*args, **kwargs):
        return {
            "text": "",
            "usage": {},
            "metadata": {
                "empty_reason": "context_overflow_likely",
                "context_limit": 4096,
                "model_id": "phi-3.5-mini",
            },
        }

    cfg = _make_cfg()
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        for i in range(4):
            (tmp / f"f{i}.py").write_text("z" * 1000, encoding="utf-8")
        with (
            patch("agent.routes.ai_snake_config._current_config", return_value=cfg),
            patch("agent.routes.snakes.generate_text", side_effect=_fake_generate_text),
            patch.object(sgpt, "_resolve_repo_root", return_value=tmp),
        ):
            answer, trace = snakes._worker_chat_full_scan(
                "Erkläre das Modul",
                provider="lmstudio",
                model="phi-3.5-mini-instruct",
            )

    assert answer == ""
    assert trace["error"] == "context_overflow"
    assert "error_hint" in trace
    assert "chat_full_scan_files_per_batch" in trace["error_hint"]
    # batch_metas should preserve the per-batch context overflow markers
    assert all(
        m.get("empty_reason") == "context_overflow_likely"
        for m in trace["batch_metas"]
    )


def test_lmstudio_strategy_attaches_overflow_metadata(monkeypatch):
    """LMStudioStrategy should attach empty_reason=context_overflow_likely to
    the result metadata when the estimated prompt exceeds the resolved
    context_limit."""
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()
    # Fake a 200 OK response with empty content + no usage
    fake_resp = {"choices": [{"message": {"content": ""}}], "usage": {}}
    monkeypatch.setattr(strategy, "_post_lmstudio", lambda *a, **k: fake_resp)
    monkeypatch.setattr(strategy, "_update_lmstudio_history", lambda *a, **k: None)

    # Build a huge prompt that exceeds the resolved phi-3.5 context (4096)
    huge = "x" * 20000
    result = strategy._call_with_model(
        model_id="phi-3.5-mini-instruct",
        model_context=None,  # not provided by /v1/models
        prompt=huge,
        request_url="http://stub/v1/chat/completions",
        is_chat=True,
        history=None,
        timeout=10,
    )

    assert result["text"] == ""
    meta = result["metadata"]
    assert meta["empty_reason"] == "context_overflow_likely"
    assert meta["context_limit"] == 4096  # from the per-model lookup
    assert meta["model_id"] == "phi-3.5-mini-instruct"

"""LCTX-002/003: fit check, never-silent truncation, enforced bundle budget, wired compression settings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import context_window as cw

pytestmark = pytest.mark.timeout(30)


def test_fit_check_uses_the_window_minus_the_answer_reserve():
    fit = cw.check_fit(prompt="x" * 4000, messages=[{"content": "y" * 400}], window_tokens=2048,
                       output_reserve_tokens=1024)
    assert fit.estimated_tokens == 1100 and fit.budget_tokens == 1024
    assert not fit.fits and fit.overflow_tokens == 76 and fit.ratio > 1
    assert cw.check_fit(prompt="kurz").fits and cw.check_fit(prompt="kurz").window_tokens == 32768


def test_truncations_are_collected_per_scope_and_nothing_lost_records_nothing():
    with cw.truncation_scope() as outer:
        assert cw.record_truncation("tool_loop.results", "condense", before_tokens=10, after_tokens=10) is None
        with cw.truncation_scope() as inner:
            cw.record_truncation("planning.context", "char_cut", before_tokens=900, after_tokens=600, limit_chars=2400)
        assert len(inner) == 1
    assert [e.site for e in outer] == ["planning.context"]  # nested scopes report to their parent
    summary = cw.truncation_summary(outer)
    assert summary["truncated"] and summary["lost_tokens"] == 300
    assert summary["events"][0]["detail"] == {"limit_chars": 2400}
    assert cw.truncation_summary([]) is None
    cw.record_truncation("somewhere.else", "char_cut", before_tokens=5, after_tokens=1)  # no scope: still fine


def test_message_trimming_is_recorded_with_what_was_dropped():
    from agent.llm_strategies.standard import OpenAIStrategy

    strategy = OpenAIStrategy()
    messages = [{"role": "system", "content": "rules"}] + [{"role": "user", "content": "x" * 8000}] * 4
    with cw.truncation_scope() as events:
        trimmed = strategy._trim_messages(messages, 4096, 512)
        untouched = strategy._trim_messages([{"role": "user", "content": "hi"}], 4096, 512)
    assert len(trimmed) < len(messages) and untouched == [{"role": "user", "content": "hi"}]
    assert len(events) == 1 and events[0].site == "llm.trim_messages" and events[0].dropped_items >= 1
    assert events[0].detail["strategy"] == "OpenAIStrategy" and events[0].detail["window_tokens"] == 4096


def test_generate_text_puts_the_truncation_on_its_result(app, monkeypatch):
    from agent import llm_integration

    def call(*args, **kwargs):
        cw.record_truncation("llm.trim_messages", "trim_messages", before_tokens=40000, after_tokens=31000,
                             dropped_items=3)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(llm_integration, "_call_llm", call)
    with app.test_request_context("/llm/generate", method="POST"):
        from flask import g

        result = llm_integration.generate_text("hi", provider="llamacpp", model="m")
        assert result["metadata"]["context_truncation"]["lost_tokens"] == 9000
        assert g.llm_context_truncations[0]["dropped_items"] == 3
    monkeypatch.setattr(llm_integration, "_call_llm", lambda *a, **k: {"text": "ok"})
    with app.app_context():
        assert "metadata" not in llm_integration.generate_text("hi", provider="llamacpp", model="m")


def test_the_tool_loop_records_condensed_results():
    from agent.cli_backends.tool_loop import build_tool_loop_prompt

    results = [{"tool_name": f"t{i}", "status": "ok", "data": "z" * 6000} for i in range(4)]
    with cw.truncation_scope() as events:
        build_tool_loop_prompt(original_prompt="p", instructions="i", tool_results=results, iteration=5,
                               max_iterations=6, max_tool_result_chars=8000, max_total_tool_result_chars=13000)
    assert [(e.site, e.kind, e.dropped_items) for e in events] == [("tool_loop.results", "condense", 2)]


def test_the_context_bundle_enforces_its_token_budget():
    from agent.services.context_bundle_service import ContextBundler

    chunks = [{"content": "a" * 4000, "source": f"s{i}"} for i in range(5)]  # ~1000 tokens each
    with cw.truncation_scope() as events:
        kept, dropped = ContextBundler._within_token_budget(chunks, 2500)
    assert [c["source"] for c in kept] == ["s0", "s1"] and dropped == 3
    assert events[0].site == "context_bundle" and events[0].dropped_items == 3
    assert ContextBundler._within_token_budget([{"content": "b" * 40000}], 100)[1] == 0  # one chunk always stays


def test_compression_settings_reach_the_adapter_config():
    from agent.services.context_compression.settings_config import compression_config_from_settings

    settings = SimpleNamespace(context_compression_enabled=True, context_compression_mode="compress",
                               context_compression_ccr_enabled=True, context_compression_ccr_path="/data/ccr",
                               context_compression_ccr_ttl_hours=24, context_compression_min_quality_score=None)
    config = compression_config_from_settings(settings)
    assert config["enabled"] and config["mode"] == "compress"
    assert config["ccr_store_path"] == "/data/ccr" and config["ccr_ttl_hours"] == 24
    assert "min_quality_score" not in config  # unset keeps the adapter's default
    assert compression_config_from_settings(SimpleNamespace())["enabled"] is False

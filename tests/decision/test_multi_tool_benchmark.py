"""Deterministic benchmark coverage and exact metrics without a live model."""

from __future__ import annotations

import pytest

from agent.services.multi_tool_benchmark import cases, normalize_calls, run_comparison


def test_comparison_has_all_modes_and_cases_with_honest_errors():
    def transport(path, body, deadline):
        if path == "/v1/decision":
            return {
                "object": "decision",
                "results": [{"fields": {key: {"value": False, "probability": 0.99} for key in body["schema"]}}],
            }, {"ttft_ms": None}
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}, {"ttft_ms": 1}

    report = run_comparison(transport, model="fake", model_sha256="0" * 64, hardware={"device": "test"}, repeats=1)
    assert len(report["rows"]) == 3 * len(cases())
    assert not report["production_release_evidence"]
    assert {r["mode"] for r in report["rows"]} == {"direct", "classic", "hybrid"}
    dependent = next(r for r in report["rows"] if r["mode"] == "direct" and r["case"] == "dependent")
    assert dependent["error"] == "requires_system2"
    assert report["summary"]["classic"]["accuracy"] == pytest.approx(1 / len(cases()))


def test_duplicate_and_unknown_tool_calls_are_failures():
    with pytest.raises(ValueError):
        normalize_calls([{"name": "shell", "arguments": {}}])
    with pytest.raises(ValueError):
        normalize_calls([{"name": "status", "arguments": {"detailed": True}}] * 2)

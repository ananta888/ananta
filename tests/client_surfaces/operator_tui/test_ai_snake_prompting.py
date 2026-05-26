from __future__ import annotations

from pathlib import Path

from client_surfaces.operator_tui.ai_snake_context import compact_observation_summary
from client_surfaces.operator_tui.ai_snake_prediction import QuickPrediction, build_prediction_trace
from client_surfaces.operator_tui.ai_snake_worker_client import AiSnakeWorkerClient


def test_prompt_templates_contain_required_contract_sections() -> None:
    template_dir = Path("prompts/ai_snake")
    required_files = [
        "predict_intent.j2",
        "explain_artifact.j2",
        "answer_chat.j2",
        "suggest_next_action.j2",
    ]
    for name in required_files:
        text = (template_dir / name).read_text(encoding="utf-8")
        assert "CONTROL:" in text
        assert "TASK:" in text
        assert "OBSERVATION:" in text
        assert "CONTEXT:" in text
        assert "OUTPUT:" in text
        assert "ai_snake_response.v1" in text
        assert "untrusted" in text.lower()


def test_worker_client_renders_prompt_and_truncates() -> None:
    client = AiSnakeWorkerClient()
    prompt = client.render_prompt(
        mode="predict_intent",
        observation_summary={"facts": ["section=tasks"]},
        context_envelope_ref={"context_hash": "ctx-1"},
        max_chars=600,
    )
    assert prompt
    assert len(prompt) <= 612
    assert "section=tasks" in prompt


def test_compact_observation_summary_prioritizes_main_facts() -> None:
    summary = {
        "facts": [
            "movement_trend=right",
            "misc=foo",
            "section=artifacts",
            "selected_ref=client_surfaces/operator_tui/renderer.py",
            "channel=ai:tutor",
            "last_command=:inspect",
        ],
        "notes_active": True,
        "event_count": 99,
    }
    compact = compact_observation_summary(summary, max_facts=5)
    assert compact["facts"][0].startswith("section=")
    assert compact["facts"][1].startswith("channel=")
    assert compact["facts"][2].startswith("selected_ref=")
    assert compact["notes_active"] is True


def test_prediction_trace_is_data_sparse() -> None:
    trace = build_prediction_trace(
        mode="predict_intent",
        prediction=QuickPrediction("chat", "ai:tutor", 0.73, ["ev-1"], 999.0),
        context_hash="ctx-abc",
        used_refs=[{"ref": "client_surfaces/operator_tui/interactive.py"}],
        provider_ref="worker:default",
        cache_hit=False,
        skipped_reason="",
    )
    assert trace["prediction_id"]
    assert trace["cache_state"] == "miss"
    assert "notes_context" not in str(trace).lower()
    assert "password" not in str(trace).lower()

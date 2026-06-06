from __future__ import annotations

from unittest.mock import patch


def _cfg() -> dict:
    return {
        "chat_use_codecompass": True,
        "chat_include_local_project": True,
        "chat_include_wikipedia": False,
        "chat_retrieval_profile": "auto",
        "chat_retrieval_domain_hint": "",
        "chat_code_questions_repo_first": False,
    }


def test_resolver_sets_full_scan_for_mermaid_architecture_request():
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile(
        "Bitte erstelle Mermaid Diagramme zur implementierten CodeCompass Worker Handoff Architektur",
        _cfg(),
    )

    assert profile.analysis_mode == "architecture_full_scan"
    assert profile.output_intent in {"mermaid_component_diagram", "mermaid_sequence_diagram"}
    assert profile.coverage_policy == "relation_expanded"
    assert profile.source_type_weights["repo"] >= profile.source_type_weights["artifact"]


def test_short_codecompass_question_stays_standard_mode():
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile("was ist CodeCompass?", _cfg())

    assert profile.analysis_mode == ""


def test_snake_ask_debug_trace_contains_full_scan_profile(client):
    with (
        patch("agent.routes.ai_snake_config._current_config", return_value=_cfg()),
        patch("agent.routes.snakes._pick_worker_for_ask", return_value=("", None)),
        patch("agent.routes.snakes.generate_text", return_value="fallback answer"),
    ):
        resp = client.post(
            "/snake/ask",
            json={
                "question": "Bitte erstelle ein Mermaid Diagramm zur implementierten CodeCompass Worker Handoff Architektur",
                "debug": True,
            },
        )

    assert resp.status_code == 200
    trace = resp.json["trace"]
    profile = trace["rag"]["retrieval_profile"]
    assert profile["analysis_mode"] == "architecture_full_scan"
    assert profile["output_intent"] in {"mermaid_component_diagram", "mermaid_sequence_diagram"}
    assert profile["coverage_policy"] == "relation_expanded"
    assert trace["full_scan"]["status"] == "not_run"
    assert trace["full_scan"]["reason"] == "hub_direct_fallback"


def test_snake_ask_payload_can_disable_full_scan_profile(client):
    with (
        patch("agent.routes.ai_snake_config._current_config", return_value=_cfg()),
        patch("agent.routes.snakes._pick_worker_for_ask", return_value=("", None)),
        patch("agent.routes.snakes.generate_text", return_value="fallback answer"),
    ):
        resp = client.post(
            "/snake/ask",
            json={
                "question": "Bitte erstelle ein Mermaid Diagramm zur implementierten CodeCompass Worker Handoff Architektur",
                "debug": True,
                "retrieval_config": {"chat_architecture_analysis_mode": "off"},
            },
        )

    assert resp.status_code == 200
    profile = resp.json["trace"]["rag"]["retrieval_profile"]
    assert profile["analysis_mode"] == "standard"


def test_snake_ask_payload_can_force_full_scan_profile(client):
    with (
        patch("agent.routes.ai_snake_config._current_config", return_value=_cfg()),
        patch("agent.routes.snakes._pick_worker_for_ask", return_value=("", None)),
        patch("agent.routes.snakes.generate_text", return_value="fallback answer"),
    ):
        resp = client.post(
            "/snake/ask",
            json={
                "question": "Gib mir einen kurzen Ueberblick",
                "debug": True,
                "retrieval_config": {"chat_architecture_analysis_mode": "full_scan"},
            },
        )

    assert resp.status_code == 200
    profile = resp.json["trace"]["rag"]["retrieval_profile"]
    assert profile["analysis_mode"] == "architecture_full_scan"

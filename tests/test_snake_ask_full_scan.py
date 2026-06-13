from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.manual_full_scan


def _cfg(analysis_mode: str | None = None) -> dict:
    """Build the UI config used by the full_scan tests.

    ``analysis_mode`` is the explicit user setting for
    ``chat_architecture_analysis_mode``. When ``None`` we default to
    ``"auto"`` (no explicit full_scan request), which is the realistic
    starting state for a freshly configured TUI/Angular client.

    The two legacy tests that asserted "Mermaid + Architektur" wording
    auto-escalates to full_scan were rewritten to pass
    ``analysis_mode="full_scan"`` explicitly — the old behaviour was a
    bug (word triggers hijacking the user's explicit ``auto`` preference).
    """
    cfg = {
        "chat_use_codecompass": True,
        "chat_include_local_project": True,
        "chat_include_wikipedia": False,
        "chat_retrieval_profile": "auto",
        "chat_retrieval_domain_hint": "",
        "chat_code_questions_repo_first": False,
        "chat_architecture_analysis_mode": analysis_mode or "auto",
    }
    return cfg


def test_resolver_sets_full_scan_for_mermaid_architecture_request():
    from agent.services.retrieval_profile_service import resolve_profile

    # User explicitly requested full_scan; the Mermaid+Architektur wording
    # is just flavour text in this scenario.
    profile = resolve_profile(
        "Bitte erstelle Mermaid Diagramme zur implementierten CodeCompass Worker Handoff Architektur",
        _cfg(analysis_mode="full_scan"),
    )

    assert profile.analysis_mode == "architecture_full_scan"
    assert profile.output_intent in {"mermaid_component_diagram", "mermaid_sequence_diagram"}
    assert profile.coverage_policy == "relation_expanded"
    assert profile.source_type_weights["repo"] >= profile.source_type_weights["artifact"]


def test_short_codecompass_question_stays_standard_mode():
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile("was ist CodeCompass?", _cfg())

    assert profile.analysis_mode == ""


def test_auto_mode_does_not_escalate_on_architecture_words():
    """Regression test: with ``chat_architecture_analysis_mode="auto"``
    (the realistic default for a freshly configured TUI), mentioning
    "Architektur" or "CodeCompass" in a Mermaid question must NOT
    trigger the expensive full_scan path. The previous word-trigger
    heuristic hijacked the user's explicit ``auto`` preference and
    forced every architecture-flavored question onto the slow path.
    """
    from agent.services.retrieval_profile_service import resolve_profile

    # All these questions mention architecture/code-related words but
    # must stay in standard mode under ``auto`` (the user's explicit
    # choice is "I have not decided, use the cheap default").
    queries = [
        "Bitte erstelle Mermaid Diagramme zur implementierten CodeCompass Worker Handoff Architektur",
        "erkläre mir die architektur",
        "wie funktioniert die worker handoff",
        "was ist CodeCompass?",
        "erkläre das codecompass-modul",
        "architekturüberblick im repository",
    ]
    for q in queries:
        profile = resolve_profile(q, _cfg(analysis_mode="auto"))
        assert profile.analysis_mode == "", (
            f"auto mode must not auto-escalate to full_scan for query {q!r}; "
            f"got analysis_mode={profile.analysis_mode!r}"
        )


def test_full_scan_keyword_still_triggers_under_auto():
    """The unambiguous "I want the whole picture" keywords must keep
    triggering full_scan even under ``auto`` mode, because they are
    explicit "give me everything" requests that justify the cost.
    """
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile(
        "Bitte erstelle ein Architekturdiagramm als full scan",
        _cfg(analysis_mode="auto"),
    )
    assert profile.analysis_mode == "architecture_full_scan"


def test_off_mode_disables_full_scan_even_with_keywords():
    """Explicit ``off`` overrides the keyword triggers too."""
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile(
        "Bitte erstelle ein Architekturdiagramm als full scan",
        _cfg(analysis_mode="off"),
    )
    assert profile.analysis_mode in {"", "standard"}


def test_snake_ask_debug_trace_contains_full_scan_profile(client):
    with (
        patch("agent.routes.ai_snake_config._current_config", return_value=_cfg(analysis_mode="full_scan")),
        patch("agent.routes.snakes._pick_worker_for_ask", return_value=("", None)),
        patch("agent.routes.snakes._worker_chat_full_scan", return_value=("", {"files_found": 0, "batches_completed": 0})),
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

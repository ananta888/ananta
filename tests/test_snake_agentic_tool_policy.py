from agent.services.snake_agentic_tool_policy import resolve_snake_agentic_tool_decision


def test_codecompass_explanation_gets_bounded_tools_and_fast_final_model() -> None:
    decision = resolve_snake_agentic_tool_decision(
        "Erkläre mir den CodeCompass",
        {"chat_use_codecompass": True, "chat_retrieval_profile": "repo_first"},
    )

    assert decision.enabled is True
    assert decision.trigger == "code_question"
    assert decision.max_tool_calls == 12
    assert decision.max_search_calls == 1
    assert decision.final_task_kind == "classification"


def test_plain_writing_question_does_not_get_repository_tools() -> None:
    decision = resolve_snake_agentic_tool_decision(
        "Verbessere bitte Stil und Struktur dieses Absatzes",
        {"chat_use_codecompass": True},
    )

    assert decision.enabled is False


def test_explicit_iterative_mode_keeps_heavy_unbounded_policy() -> None:
    decision = resolve_snake_agentic_tool_decision(
        "Analysiere die Architektur",
        {"chat_architecture_analysis_mode": "rag_iterative"},
    )

    assert decision.enabled is True
    assert decision.trigger == "explicit_session_mode"
    assert decision.max_tool_calls is None
    assert decision.final_task_kind == "repo_analysis"


def test_zero_code_question_budget_means_unlimited() -> None:
    decision = resolve_snake_agentic_tool_decision(
        "Erkläre mir den CodeCompass",
        {
            "chat_use_codecompass": True,
            "chat_code_question_max_tool_calls": 0,
            "chat_code_question_max_search_calls": 0,
        },
    )

    assert decision.enabled is True
    assert decision.max_tool_calls == 0
    assert decision.max_search_calls == 0

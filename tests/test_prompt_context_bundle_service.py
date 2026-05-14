from __future__ import annotations

from types import SimpleNamespace

from agent.services.prompt_context_bundle_service import PromptContextBundleService


def test_prompt_context_bundle_includes_contract_and_instruction_metadata() -> None:
    svc = PromptContextBundleService()
    context = SimpleNamespace(
        goal_id="g1",
        task_id="t1",
        task={
            "task_kind": "coding",
            "worker_execution_contract": {
                "execution_mode": "llm_first_with_guardrails",
                "strategy_mode": "openai_compatible_tool_calling",
                "expected_artifacts": [{"kind": "directory"}],
                "verification_gates": [{"id": "artifact_presence"}],
                "allowed_tool_classes": ["read", "write"],
            },
            "worker_execution_context": {
                "instruction_layers": {"profile_name": "research-helper"},
                "instruction_context": {"owner_username": "alice", "profile_id": "p1"},
            },
        },
        research_context={"chunks": [{"source_id": "s1", "content": "hello world", "metadata": {"sensitivity": "public"}}]},
        policy=SimpleNamespace(allow_shell_execution=False, requires_executable_step=True),
    )
    bundle = svc.build_for_propose_context(context).to_dict()
    assert bundle["contract_summary"]["expected_artifacts_count"] == 1
    assert bundle["context_summary"]["instruction_layers_present"] is True
    assert bundle["context_summary"]["instruction_selection"]["profile_id"] == "p1"


def test_prompt_context_bundle_filters_sensitive_chunks() -> None:
    svc = PromptContextBundleService()
    context = SimpleNamespace(
        goal_id="g1",
        task_id="t1",
        task={"task_kind": "research"},
        research_context={
            "chunks": [
                {"source_id": "a", "content": "public", "metadata": {"sensitivity": "public"}},
                {"source_id": "b", "content": "secret", "metadata": {"sensitivity": "secret"}},
            ]
        },
        policy=SimpleNamespace(allow_shell_execution=False, requires_executable_step=False),
    )
    bundle = svc.build_for_propose_context(context).to_dict()
    budget = bundle["context_summary"]["budget"]
    assert budget["input_count"] == 2
    assert budget["denied_count"] == 1
    assert budget["selected_count"] == 1


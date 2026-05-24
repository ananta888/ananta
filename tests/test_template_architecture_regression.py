from __future__ import annotations

from types import SimpleNamespace

from agent.services.prompt_context_bundle_service import PromptContextBundleService


def test_prompt_context_bundle_remains_valid_without_instruction_stack():
    svc = PromptContextBundleService()
    context = SimpleNamespace(
        goal_id="g-reg",
        task_id="t-reg",
        task={"task_kind": "coding", "worker_execution_context": {}},
        research_context={},
        policy=SimpleNamespace(allow_shell_execution=False, requires_executable_step=False),
    )
    bundle = svc.build_for_propose_context(context).to_dict()
    assert bundle["schema"] == "prompt_context_bundle.v1"
    assert bundle["context_summary"]["instruction_stack_present"] is False
    assert bundle["context_summary"]["instruction_stack_checksum"] is None


def test_prompt_context_bundle_exposes_blueprint_role_defaults_traceability():
    svc = PromptContextBundleService()
    context = SimpleNamespace(
        goal_id="g-reg",
        task_id="t-reg",
        task={
            "task_kind": "coding",
            "worker_execution_context": {
                "planning_provenance": {
                    "plan_id": "p-1",
                    "plan_node_id": "pn-1",
                    "goal_id": "g-reg",
                    "blueprint_id": "bp-1",
                    "blueprint_role_name": "qa",
                    "blueprint_role_defaults": {"verification_defaults": {"required": True}},
                }
            },
        },
        research_context={},
        policy=SimpleNamespace(allow_shell_execution=False, requires_executable_step=False),
    )
    bundle = svc.build_for_propose_context(context).to_dict()
    provenance = dict(bundle["context_summary"]["planning_provenance"] or {})
    assert provenance["blueprint_id"] == "bp-1"
    assert provenance["blueprint_role_name"] == "qa"
    assert isinstance(provenance["blueprint_role_defaults"], dict)


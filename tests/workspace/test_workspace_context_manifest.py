from __future__ import annotations

from types import SimpleNamespace

from agent.services.prompt_context_bundle_service import PromptContextBundleService


def test_workspace_context_manifest_visible_in_bundle_summary():
    context = SimpleNamespace(
        goal_id="g1",
        task_id="t1",
        task={
            "task_kind": "coding",
            "worker_execution_context": {
                "workspace": {"workspace_dir": "/tmp/ws/g1", "shared_goal_workspace": True},
                "planning_provenance": {"goal_id": "g1"},
            },
        },
        research_context={},
        policy=SimpleNamespace(allow_shell_execution=False, requires_executable_step=False),
    )
    bundle = PromptContextBundleService().build_for_propose_context(context).to_dict()
    assert bundle["context_summary"].get("planning_provenance") is not None

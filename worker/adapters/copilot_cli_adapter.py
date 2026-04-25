from __future__ import annotations

from typing import Any

from worker.adapters.coding_tool_base import AdapterDescriptor


class CopilotCliAdapter:
    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id="adapter.copilot_cli",
            display_name="Copilot CLI",
            kind="experimental",
            enabled=self._enabled,
            reason="explicit_opt_in_required",
        )

    def capabilities(self) -> dict[str, bool]:
        return {
            "plan": self._enabled,
            "propose_patch": self._enabled,
            "run_tests": False,
            "apply_patch": False,
        }

    def plan(self, *, task_id: str, capability_id: str, prompt: str) -> dict[str, Any]:
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "command": "echo '<copilot-cli plan only>'",
            "explanation": f"Experimental adapter plan. Prompt preview: {str(prompt)[:120]}",
            "risk_classification": "high",
            "required_approval": True,
            "working_directory": ".",
            "expected_effects": [
                "No direct execution. Use Ananta approval-gated worker paths for mutation.",
                "Requires local Copilot CLI auth outside OSS defaults.",
            ],
        }

    def propose_patch(self, *, task_id: str, capability_id: str, prompt: str, base_ref: str = "HEAD") -> dict[str, Any]:
        del task_id, capability_id, prompt, base_ref
        return {
            "status": "degraded",
            "reason": "experimental_adapter_does_not_apply_or_generate_patch_by_default",
            "llm_used": False,
        }

    def run_tests(self, *, task_id: str, command: str) -> dict[str, Any]:
        del task_id, command
        return {"status": "degraded", "reason": "experimental_adapter_does_not_run_tests"}

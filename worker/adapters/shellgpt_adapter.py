from __future__ import annotations

import shutil
import subprocess
from typing import Any

from worker.adapters.coding_tool_base import AdapterDescriptor


class ShellGptAdapter:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)

    def descriptor(self) -> AdapterDescriptor:
        binary_present = shutil.which("sgpt") is not None
        enabled = self._enabled and binary_present
        reason = "ready" if enabled else ("shellgpt_not_installed" if self._enabled else "adapter_disabled")
        kind = "optional" if binary_present else "unavailable"
        return AdapterDescriptor(
            adapter_id="adapter.shellgpt",
            display_name="ShellGPT",
            kind=kind,
            enabled=enabled,
            reason=reason,
        )

    def capabilities(self) -> dict[str, bool]:
        descriptor = self.descriptor()
        return {
            "plan": descriptor.enabled,
            "propose_patch": False,
            "run_tests": False,
            "apply_patch": False,
        }

    def plan(self, *, task_id: str, capability_id: str, prompt: str) -> dict[str, Any]:
        descriptor = self.descriptor()
        explanation = "ShellGPT-style command explanation plan."
        if descriptor.enabled:
            completed = subprocess.run(
                ["sgpt", "--shell", str(prompt)],
                text=True,
                capture_output=True,
                check=False,
            )
            generated = (completed.stdout or "").strip()
            if generated:
                explanation = generated.splitlines()[0]
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "command": "echo '<plan-only: run via worker.command.execute>'",
            "explanation": explanation,
            "risk_classification": "medium",
            "required_approval": True,
            "working_directory": ".",
            "expected_effects": [
                "Plan artifact only. Actual command execution must go through Ananta command executor.",
                f"Adapter status: {descriptor.reason}.",
            ],
        }

    def propose_patch(self, *, task_id: str, capability_id: str, prompt: str, base_ref: str = "HEAD") -> dict[str, Any]:
        del task_id, capability_id, prompt, base_ref
        return {"status": "degraded", "reason": "shellgpt_patch_proposal_not_supported"}

    def run_tests(self, *, task_id: str, command: str) -> dict[str, Any]:
        del task_id, command
        return {"status": "degraded", "reason": "shellgpt_test_execution_not_supported"}

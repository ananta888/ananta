from __future__ import annotations

import hashlib
import shutil
from typing import Any

from worker.adapters.coding_tool_base import AdapterDescriptor


class OpenCodeAdapter:
    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)

    def descriptor(self) -> AdapterDescriptor:
        available = shutil.which("opencode") is not None
        enabled = self._enabled and available
        reason = "ready" if enabled else ("opencode_unavailable_or_archived" if self._enabled else "explicit_opt_in_required")
        return AdapterDescriptor(
            adapter_id="adapter.opencode",
            display_name="OpenCode",
            kind="experimental",
            enabled=enabled,
            reason=reason,
        )

    def capabilities(self) -> dict[str, bool]:
        descriptor = self.descriptor()
        return {
            "plan": descriptor.enabled,
            "propose_patch": descriptor.enabled,
            "run_tests": False,
            "apply_patch": False,
        }

    def plan(self, *, task_id: str, capability_id: str, prompt: str) -> dict[str, Any]:
        descriptor = self.descriptor()
        command = "echo '<opencode experimental plan-only>'"
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "command": command,
            "command_hash": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "explanation": f"Experimental OpenCode adapter plan. Prompt preview: {str(prompt)[:120]}",
            "risk_classification": "high",
            "required_approval": True,
            "working_directory": ".",
            "expected_effects": [
                "No direct execution or apply path.",
                f"Adapter status: {descriptor.reason}.",
            ],
        }

    def propose_patch(self, *, task_id: str, capability_id: str, prompt: str, base_ref: str = "HEAD") -> dict[str, Any]:
        del task_id, capability_id, prompt, base_ref
        descriptor = self.descriptor()
        return {"status": "degraded", "reason": descriptor.reason, "llm_used": False}

    def run_tests(self, *, task_id: str, command: str) -> dict[str, Any]:
        del task_id, command
        return {"status": "degraded", "reason": "experimental_adapter_does_not_run_tests"}

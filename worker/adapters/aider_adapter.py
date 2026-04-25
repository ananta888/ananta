from __future__ import annotations

import hashlib
import shutil
import subprocess
from typing import Any

from worker.adapters.coding_tool_base import AdapterDescriptor


class AiderAdapter:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)

    def descriptor(self) -> AdapterDescriptor:
        binary_present = shutil.which("aider") is not None
        enabled = self._enabled and binary_present
        reason = "ready" if enabled else ("aider_not_installed" if self._enabled else "adapter_disabled")
        kind = "optional" if binary_present else "unavailable"
        return AdapterDescriptor(
            adapter_id="adapter.aider",
            display_name="Aider",
            kind=kind,
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
        command = f"aider --message {prompt!r}"
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "command": command,
            "command_hash": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "explanation": "Plan-only preview for aider patch proposal.",
            "risk_classification": "high",
            "required_approval": True,
            "working_directory": ".",
            "expected_effects": [
                "Potentially generate patch suggestions through external aider CLI.",
                f"Adapter status: {descriptor.reason}.",
            ],
        }

    def propose_patch(self, *, task_id: str, capability_id: str, prompt: str, base_ref: str = "HEAD") -> dict[str, Any]:
        descriptor = self.descriptor()
        if not descriptor.enabled:
            return {
                "status": "degraded",
                "adapter_id": descriptor.adapter_id,
                "reason": descriptor.reason,
                "llm_used": False,
            }
        completed = subprocess.run(
            ["aider", "--message", str(prompt), "--no-git"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "status": "degraded",
                "adapter_id": descriptor.adapter_id,
                "reason": "aider_command_failed",
                "stderr_preview": (completed.stderr or "")[:240],
                "llm_used": False,
            }
        patch_text = (completed.stdout or "").strip()
        if not patch_text:
            return {
                "status": "degraded",
                "adapter_id": descriptor.adapter_id,
                "reason": "aider_empty_output",
                "llm_used": False,
            }
        patch_hash = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        return {
            "schema": "patch_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "base_ref": str(base_ref).strip() or "HEAD",
            "patch": patch_text,
            "patch_hash": patch_hash,
            "changed_files": [],
            "risk_classification": "high",
            "expected_effects": ["External aider generated patch candidate."],
        }

    def run_tests(self, *, task_id: str, command: str) -> dict[str, Any]:
        del task_id, command
        return {"status": "degraded", "reason": "adapter_does_not_run_tests"}

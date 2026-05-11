"""WorkerHandoffBundle v1 — file-based handoff contracts for workers."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExpectedArtifact:
    kind: str
    relative_path: str
    required: bool = False
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "required": self.required,
            "description": self.description,
        }


@dataclass
class WorkerHandoffBundle:
    task_id: str
    goal_id: str
    execution_id: str
    trace_id: str
    manifest_output_path: str
    instructions_ref: str = "instructions.md"
    context_envelope_ref: str | None = None
    expected_artifacts: list[ExpectedArtifact] = field(default_factory=list)
    workspace_constraints: dict[str, Any] | None = None
    policy_decision_ref: str | None = None
    completion_policy_ref: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "worker_handoff_bundle.v1",
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "execution_id": self.execution_id,
            "trace_id": self.trace_id,
            "instructions_ref": self.instructions_ref,
            "context_envelope_ref": self.context_envelope_ref,
            "expected_artifacts": [a.to_dict() for a in self.expected_artifacts],
            "workspace_constraints": self.workspace_constraints,
            "policy_decision_ref": self.policy_decision_ref,
            "manifest_output_path": self.manifest_output_path,
            "completion_policy_ref": self.completion_policy_ref,
            "created_at": self.created_at,
        }

    def materialize(self, handoff_dir: Path, *, instructions: str = "") -> None:
        """Write all handoff files into handoff_dir. handoff_dir must be inside workspace."""
        handoff_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = handoff_dir / "worker_handoff.json"
        bundle_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        instructions_path = handoff_dir / self.instructions_ref
        manifest_instruction = (
            f"\n\n## REQUIRED: Artifact Manifest\n\n"
            f"After completing all tasks, you MUST write the artifact manifest to:\n"
            f"  `{self.manifest_output_path}`\n\n"
            f"The manifest must follow the `artifact_manifest.v1` schema. "
            f"The Hub uses this file — not your chat response — to confirm task completion.\n"
            f"Your final chat response is a summary only.\n"
        )
        instructions_path.write_text(str(instructions) + manifest_instruction, encoding="utf-8")

        if self.expected_artifacts:
            artifacts_path = handoff_dir / "expected_artifacts.json"
            artifacts_path.write_text(
                json.dumps([a.to_dict() for a in self.expected_artifacts], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @classmethod
    def build(
        cls,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        expected_artifacts: list[dict[str, Any]] | None = None,
        workspace_constraints: dict[str, Any] | None = None,
    ) -> "WorkerHandoffBundle":
        manifest_rel = f".ananta/handoff/{execution_id}/artifact_manifest.v1.json"
        return cls(
            task_id=task_id,
            goal_id=goal_id,
            execution_id=execution_id,
            trace_id=trace_id,
            manifest_output_path=manifest_rel,
            expected_artifacts=[
                ExpectedArtifact(
                    kind=str(a.get("kind") or "other"),
                    relative_path=str(a.get("relative_path") or a.get("path") or ""),
                    required=bool(a.get("required", False)),
                    description=str(a.get("description") or "") or None,
                )
                for a in (expected_artifacts or [])
                if str(a.get("relative_path") or a.get("path") or "").strip()
            ],
            workspace_constraints=workspace_constraints,
        )

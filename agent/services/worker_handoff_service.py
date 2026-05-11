"""WorkerHandoffService — materializes handoff files before worker execution."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from worker.core.artifact_completion_policy import ArtifactCompletionPolicy
from worker.core.worker_handoff_bundle import WorkerHandoffBundle

log = logging.getLogger(__name__)


class WorkerHandoffService:
    def create_handoff(
        self,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        instructions: str,
        expected_artifacts: list[dict[str, Any]] | None = None,
        completion_policy: ArtifactCompletionPolicy | None = None,
        workspace_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create .ananta/handoff/<execution_id>/ directory with all handoff files.

        Returns a dict with handoff_dir, bundle_path, manifest_output_path.
        All paths are workspace-bound.
        """
        # Safety: workspace_root must be a real directory
        workspace_root = workspace_root.resolve()
        if not workspace_root.is_dir():
            raise ValueError(f"workspace_root does not exist: {workspace_root}")

        handoff_dir = workspace_root / ".ananta" / "handoff" / execution_id
        handoff_dir.mkdir(parents=True, exist_ok=True)

        bundle = WorkerHandoffBundle.build(
            task_id=task_id,
            goal_id=goal_id,
            execution_id=execution_id,
            trace_id=trace_id,
            workspace_root=workspace_root,
            expected_artifacts=expected_artifacts,
            workspace_constraints=workspace_constraints,
        )

        bundle.materialize(handoff_dir, instructions=instructions)

        if completion_policy is not None:
            policy_path = handoff_dir / "completion_policy.json"
            policy_path.write_text(
                json.dumps(completion_policy.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            bundle.completion_policy_ref = "completion_policy.json"

        log.info(
            "WorkerHandoffService: created handoff dir %s for execution %s task %s",
            handoff_dir, execution_id, task_id,
        )

        return {
            "handoff_dir": str(handoff_dir),
            "bundle_path": str(handoff_dir / "worker_handoff.json"),
            "manifest_output_path": str(workspace_root / bundle.manifest_output_path),
            "manifest_relative_path": bundle.manifest_output_path,
            "instructions_path": str(handoff_dir / bundle.instructions_ref),
            "execution_id": execution_id,
        }


worker_handoff_service = WorkerHandoffService()


def get_worker_handoff_service() -> WorkerHandoffService:
    return worker_handoff_service

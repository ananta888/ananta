"""WorkerOutputCollectorService — reads artifact manifest after worker execution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.services.artifact_manifest_service import get_artifact_manifest_service
from agent.services.workspace_diff_service import get_workspace_diff_service

log = logging.getLogger(__name__)


class WorkerOutputCollectorService:
    def collect(
        self,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        manifest_relative_path: str,
        allow_synthesized_fallback: bool = False,
        before_snapshot_id: str | None = None,
        before_snapshot: dict[str, str] | None = None,
        after_snapshot_id: str | None = None,
        after_snapshot: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Collect and validate artifacts after worker execution.

        Returns collection result with: manifest_valid, artifacts, errors, warnings, synthesized.
        The Hub uses this to make completion decisions — not the final chat response.
        """
        manifest_svc = get_artifact_manifest_service()
        manifest_path = workspace_root / manifest_relative_path

        # Safety: manifest_path must be inside workspace
        try:
            resolved = manifest_path.resolve()
            if not resolved.is_relative_to(workspace_root.resolve()):
                return {
                    "manifest_valid": False,
                    "artifacts": [],
                    "errors": ["manifest_path_escapes_workspace"],
                    "warnings": [],
                    "synthesized": False,
                    "collection_method": "manifest",
                }
        except (ValueError, OSError) as exc:
            return {
                "manifest_valid": False,
                "artifacts": [],
                "errors": [f"manifest_path_error:{exc}"],
                "warnings": [],
                "synthesized": False,
                "collection_method": "manifest",
            }

        validation = manifest_svc.load_and_validate(manifest_path, workspace_root=workspace_root)

        if validation["valid"]:
            log.info(
                "WorkerOutputCollector: manifest valid for task %s, %d artifacts",
                task_id, len(validation.get("artifacts") or []),
            )
            return {
                "manifest_valid": True,
                "artifacts": validation.get("artifacts") or [],
                "errors": [],
                "warnings": validation.get("warnings") or [],
                "synthesized": bool(validation.get("synthesized", False)),
                "manifest_id": validation.get("manifest_id", ""),
                "collection_method": "manifest",
            }

        errors = list(validation.get("errors") or [])
        manifest_missing = any("manifest_file_missing" in e for e in errors)

        if allow_synthesized_fallback and before_snapshot is not None and after_snapshot is not None:
            log.info(
                "WorkerOutputCollector: manifest missing/invalid for task %s, synthesizing from workspace diff",
                task_id,
            )
            diff_svc = get_workspace_diff_service()
            fcs = diff_svc.compute_diff(
                task_id=task_id,
                execution_id=execution_id,
                workspace_root=workspace_root,
                before_snapshot_id=before_snapshot_id or "before",
                before_snapshot=before_snapshot,
                after_snapshot_id=after_snapshot_id or "after",
                after_snapshot=after_snapshot,
            )
            synth_manifest = diff_svc.synthesize_manifest(
                file_change_set=fcs,
                workspace_root=workspace_root,
                task_id=task_id,
                goal_id=goal_id,
                execution_id=execution_id,
                trace_id=trace_id,
            )
            synth_validation = manifest_svc.validate_manifest(synth_manifest, workspace_root=workspace_root)
            return {
                "manifest_valid": synth_validation["valid"],
                "artifacts": synth_validation.get("artifacts") or [],
                "errors": synth_validation.get("errors") or [],
                "warnings": ["manifest_synthesized_from_workspace_diff"] + list(synth_validation.get("warnings") or []),
                "synthesized": True,
                "manifest_id": synth_validation.get("manifest_id", ""),
                "collection_method": "synthesized_from_diff",
            }

        log.warning(
            "WorkerOutputCollector: manifest invalid for task %s, errors=%s",
            task_id, errors,
        )
        return {
            "manifest_valid": False,
            "artifacts": [],
            "errors": errors,
            "warnings": validation.get("warnings") or [],
            "synthesized": False,
            "collection_method": "manifest",
        }


worker_output_collector_service = WorkerOutputCollectorService()


def get_worker_output_collector_service() -> WorkerOutputCollectorService:
    return worker_output_collector_service

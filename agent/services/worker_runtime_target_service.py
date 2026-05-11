"""Worker runtime target helpers.

DRR-T047 foundation: validate and expose concrete runtime targets separately
from worker/backend selection.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from worker.core.runtime_target import (
    RuntimeDataBoundary,
    RuntimeHealthState,
    SecretAccessPolicy,
    WorkerRuntimeKind,
    WorkerRuntimeTarget,
)


class WorkerRuntimeTargetError(ValueError):
    """Raised when runtime target config cannot be validated."""


class WorkerRuntimeTargetService:
    def from_config(self, payload: dict[str, Any] | WorkerRuntimeTarget) -> WorkerRuntimeTarget:
        if isinstance(payload, WorkerRuntimeTarget):
            return payload
        try:
            return WorkerRuntimeTarget.model_validate(payload)
        except ValidationError as exc:
            raise WorkerRuntimeTargetError(str(exc)) from exc

    def validate_or_error(self, payload: dict[str, Any]) -> tuple[WorkerRuntimeTarget | None, str | None]:
        try:
            return self.from_config(payload), None
        except WorkerRuntimeTargetError as exc:
            return None, str(exc)

    def local_process_default(
        self,
        runtime_target_id: str = "local-process-default",
        *,
        workspace_scope: str = ".",
        allowed_capabilities: list[str] | None = None,
    ) -> WorkerRuntimeTarget:
        return WorkerRuntimeTarget(
            runtime_target_id=runtime_target_id,
            runtime_kind=WorkerRuntimeKind.local_process,
            location="local",
            workspace_scope=workspace_scope,
            os_family="linux",
            containerized=False,
            network_zone="local",
            allowed_capabilities=allowed_capabilities or ["planning", "code_read", "repair.execute.inspect", "repair.verify"],
            available_tools=["file.read", "command.probe"],
            data_boundary=RuntimeDataBoundary.local_only,
            secret_access_policy=SecretAccessPolicy.deny,
            health_state=RuntimeHealthState.ready,
        )

    def docker_default(
        self,
        runtime_target_id: str = "docker-worker-default",
        *,
        workspace_scope: str = "/workspace",
        allowed_capabilities: list[str] | None = None,
    ) -> WorkerRuntimeTarget:
        return WorkerRuntimeTarget(
            runtime_target_id=runtime_target_id,
            runtime_kind=WorkerRuntimeKind.docker_container,
            location="local-docker",
            workspace_scope=workspace_scope,
            os_family="linux",
            containerized=True,
            network_zone="local",
            allowed_capabilities=allowed_capabilities or [
                "planning",
                "code_read",
                "repair.execute.inspect",
                "repair.execute.low_risk",
                "repair.verify",
            ],
            available_tools=["file.read", "command.probe", "command.execute"],
            data_boundary=RuntimeDataBoundary.project_private,
            secret_access_policy=SecretAccessPolicy.deny,
            health_state=RuntimeHealthState.ready,
        )


__all__ = [
    "RuntimeDataBoundary",
    "RuntimeHealthState",
    "SecretAccessPolicy",
    "WorkerRuntimeKind",
    "WorkerRuntimeTarget",
    "WorkerRuntimeTargetError",
    "WorkerRuntimeTargetService",
]

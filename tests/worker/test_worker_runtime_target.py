import pytest
from pydantic import ValidationError

from agent.services.worker_runtime_target_service import WorkerRuntimeTargetService
from worker.core.runtime_target import (
    RuntimeDataBoundary,
    RuntimeHealthState,
    SecretAccessPolicy,
    WorkerRuntimeKind,
    WorkerRuntimeTarget,
)


def test_local_docker_runtime_target_valid():
    target = WorkerRuntimeTarget(
        runtime_target_id="docker-local-01",
        runtime_kind=WorkerRuntimeKind.docker_container,
        workspace_scope="/workspace",
        allowed_capabilities=["repair.execute.inspect", "repair.execute.low_risk"],
        data_boundary=RuntimeDataBoundary.project_private,
        secret_access_policy=SecretAccessPolicy.deny,
        health_state=RuntimeHealthState.ready,
    )
    assert target.is_local is True
    assert target.is_cloud is False
    assert target.requires_workspace_scope is True


def test_mutation_capable_runtime_requires_workspace_scope():
    with pytest.raises(ValidationError):
        WorkerRuntimeTarget(
            runtime_target_id="bad-runtime",
            runtime_kind=WorkerRuntimeKind.docker_container,
            allowed_capabilities=["repair.execute.low_risk"],
            data_boundary=RuntimeDataBoundary.project_private,
        )


def test_secret_capable_runtime_requires_explicit_policy():
    with pytest.raises(ValidationError):
        WorkerRuntimeTarget(
            runtime_target_id="secret-runtime",
            runtime_kind=WorkerRuntimeKind.local_process,
            workspace_scope=".",
            allowed_capabilities=["secret_read"],
            data_boundary=RuntimeDataBoundary.local_only,
            secret_access_policy=SecretAccessPolicy.deny,
        )


def test_cloud_runtime_target_classification():
    target = WorkerRuntimeTarget(
        runtime_target_id="cloud-worker-01",
        runtime_kind=WorkerRuntimeKind.cloud_worker,
        allowed_capabilities=["planning"],
        data_boundary=RuntimeDataBoundary.cloud,
        health_state=RuntimeHealthState.ready,
    )
    assert target.is_cloud is True
    assert target.is_external is True
    assert target.is_local is False


def test_supports_capabilities_reports_missing():
    target = WorkerRuntimeTarget(
        runtime_target_id="inspect-only",
        runtime_kind=WorkerRuntimeKind.local_process,
        allowed_capabilities=["repair.execute.inspect"],
        data_boundary=RuntimeDataBoundary.local_only,
    )
    ok, missing = target.supports_capabilities(["repair.execute.inspect", "repair.verify"])
    assert ok is False
    assert missing == ["repair.verify"]


def test_runtime_target_service_defaults_are_valid():
    service = WorkerRuntimeTargetService()
    local = service.local_process_default()
    docker = service.docker_default()
    assert local.runtime_kind == WorkerRuntimeKind.local_process
    assert docker.runtime_kind == WorkerRuntimeKind.docker_container
    assert docker.workspace_scope == "/workspace"

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.services.workflow_control_service import (
    RuntimeSelection,
    WorkflowControlCommand,
    WorkflowControlService,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_runtime import ExecutionPlan
from agent.services.workflow_runtime.commands import WorkflowCommandIssuer
from agent.services.workflow_runtime.security import HmacKeyRing


class Authorization:
    def authorize(self, **kwargs):
        return "allowed"


class Selection:
    def __init__(self, capabilities=("retrieval",), mode="live") -> None:
        self.capabilities = frozenset(capabilities)
        self.mode = mode

    def select(self, **kwargs):
        return RuntimeSelection(
            runtime_id="native",
            capabilities=self.capabilities,
            mode=self.mode,
            reason_code="selected",
        )


@dataclass
class Bridge:
    starts: int = 0
    signals: int = 0
    last_command: object | None = None

    def start(self, **kwargs):
        self.starts += 1
        return WorkflowRunHandle(
            tenant_id=kwargs["principal"].tenant_id,
            workflow_id=kwargs["plan"].workflow_id,
            run_id=kwargs["run_id"],
            runtime_id=kwargs["selection"].runtime_id,
            status="created",
            task_ref="task-1",
        )

    def query(self, **kwargs):
        return {"status": "running"}

    def signal(self, **kwargs):
        self.signals += 1
        self.last_command = kwargs["command"]
        return {"status": "signalled"}

    def cancel(self, **kwargs):
        return {"status": "cancelled"}

    def history(self, **kwargs):
        return ({"sequence": 1},)


def plan() -> ExecutionPlan:
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-1",
            "plan_id": "plan-1",
            "workflow_id": "workflow-1",
            "policy_version": "policy-v1",
            "capabilities": ["retrieval"],
            "nodes": [{"id": "step-1", "required_capabilities": ["retrieval"]}],
        }
    )


def command_issuer() -> WorkflowCommandIssuer:
    return WorkflowCommandIssuer(
        HmacKeyRing({"control-key": "x" * 32}, active_key_id="control-key"),
        clock=lambda: 100.0,
    )


def test_start_delegates_only_through_hub_bridge() -> None:
    bridge = Bridge()
    service = WorkflowControlService(
        authorization=Authorization(),
        selection=Selection(),
        bridge=bridge,
    )

    handle = service.start(
        principal=WorkflowPrincipal("tenant-1", "user-1"),
        plan=plan(),
        run_id="run-1",
        authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
    )

    assert handle.status == "created"
    assert bridge.starts == 1


def test_runtime_without_required_capability_is_incompatible() -> None:
    service = WorkflowControlService(
        authorization=Authorization(),
        selection=Selection(capabilities=()),
        bridge=Bridge(),
    )

    with pytest.raises(RuntimeError, match="workflow_runtime_incompatible"):
        service.start(
            principal=WorkflowPrincipal("tenant-1", "user-1"),
            plan=plan(),
            run_id="run-1",
            authorization_envelope={},
        )


def test_cross_tenant_start_is_denied_before_bridge() -> None:
    bridge = Bridge()
    service = WorkflowControlService(
        authorization=Authorization(),
        selection=Selection(),
        bridge=bridge,
    )

    with pytest.raises(PermissionError, match="tenant_binding_mismatch"):
        service.start(
            principal=WorkflowPrincipal("tenant-2", "user-1"),
            plan=plan(),
            run_id="run-1",
            authorization_envelope={},
        )

    assert bridge.starts == 0


def test_command_requires_checkpoint_and_bound_authorization() -> None:
    service = WorkflowControlService(
        authorization=Authorization(),
        selection=Selection(),
        bridge=Bridge(),
        command_issuer=command_issuer(),
    )
    command = WorkflowControlCommand(
        command_id="command-1",
        command_type="approve",
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-1",
        expected_revision=4,
        plan_hash=plan().plan_hash,
        policy_version="policy-v1",
        authorization_envelope={"signature": "opaque"},
    )

    result = service.command(
        principal=WorkflowPrincipal("tenant-1", "user-1"),
        command=command,
    )

    assert result == {"status": "signalled"}
    assert service._bridge.last_command.expected_revision == 4  # noqa: SLF001
    assert service._bridge.last_command.signature  # noqa: SLF001


def test_control_command_rejects_nested_embedded_secrets_before_bridge() -> None:
    bridge = Bridge()
    service = WorkflowControlService(
        authorization=Authorization(),
        selection=Selection(),
        bridge=bridge,
        command_issuer=command_issuer(),
    )
    command = WorkflowControlCommand(
        command_id="command-secret",
        command_type="signal",
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-1",
        expected_revision=4,
        plan_hash=plan().plan_hash,
        policy_version="policy-v1",
        authorization_envelope={"signature": "opaque"},
        payload={"signal_payload": {"api_key": "must-not-cross-control-boundary"}},
    )

    with pytest.raises(ValueError, match="control_command_embedded_secret_denied"):
        service.command(principal=WorkflowPrincipal("tenant-1", "user-1"), command=command)

    assert bridge.signals == 0

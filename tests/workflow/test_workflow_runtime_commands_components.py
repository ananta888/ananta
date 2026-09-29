from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.components import (
    WorkflowComponent,
    WorkflowComponentCompiler,
    WorkflowComponentRegistry,
)
from agent.services.workflow_runtime.errors import ContractValidationError
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    InMemoryReplayNonceStore,
)


def keys() -> HmacKeyRing:
    return HmacKeyRing({"key": "x" * 32}, active_key_id="key")


def command(key_ring: HmacKeyRing, **changes) -> SignedWorkflowCommand:
    values = {
        "key_ring": key_ring,
        "command_type": "approve",
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "checkpoint_id": "checkpoint-a",
        "expected_revision": 3,
        "plan_hash": "f" * 64,
        "policy_version": "policy-v1",
        "actor_id": "operator-a",
        "actor_roles": ("operator",),
        "now": 100.0,
        "nonce": "nonce-a",
    }
    values.update(changes)
    return SignedWorkflowCommand.issue(**values)


def test_commands_are_bound_signed_replay_safe_and_secret_free() -> None:
    key_ring = keys()
    value = command(key_ring)
    verifier = WorkflowCommandVerifier(key_ring, InMemoryReplayNonceStore(clock=lambda: 110.0))
    bindings = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "checkpoint_id": "checkpoint-a",
        "expected_revision": 3,
        "plan_hash": "f" * 64,
        "policy_version": "policy-v1",
        "now": 110.0,
    }
    verifier.verify_once(value, **bindings)

    with pytest.raises(Exception, match="replay"):
        verifier.verify_once(value, **bindings)
    with pytest.raises(Exception, match="signature_invalid"):
        replace(value, actor_roles=("admin",)).verify(key_ring=key_ring, **bindings)
    with pytest.raises(Exception, match="embedded_secret"):
        command(key_ring, command_type="edit", payload={"api_token": "raw"})


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "reason_code"),
    (
        pytest.param(
            "expected_revision",
            "not-an-int",
            "workflow_command_revision_invalid",
            id="revision",
        ),
        pytest.param(
            "issued_at",
            True,
            "workflow_command_expiry_invalid",
            id="issued-at",
        ),
        pytest.param(
            "expires_at",
            float("inf"),
            "workflow_command_expiry_invalid",
            id="expires-at",
        ),
        pytest.param(
            "expires_at",
            "9" * 5_000,
            "workflow_command_expiry_invalid",
            id="expires-at-huge-decimal-string",
        ),
    ),
)
def test_command_wire_numeric_failures_keep_hub_contract_vocabulary(
    field_name: str,
    invalid_value: object,
    reason_code: str,
) -> None:
    raw_command = command(keys()).to_dict()
    raw_command[field_name] = invalid_value

    with pytest.raises(ContractValidationError, match=f"^{reason_code}$"):
        SignedWorkflowCommand.from_mapping(raw_command)


@pytest.mark.parametrize("expected_revision", (True, 1.5))
def test_command_issuer_does_not_pre_normalize_invalid_revisions(
    expected_revision: object,
) -> None:
    with pytest.raises(
        ContractValidationError,
        match="^workflow_command_revision_invalid$",
    ):
        command(keys(), expected_revision=expected_revision)


@pytest.mark.parametrize("now", (True, float("nan"), float("inf"), float("-inf")))
def test_command_issuer_does_not_pre_normalize_invalid_timestamps(
    now: object,
) -> None:
    with pytest.raises(
        ContractValidationError,
        match="^workflow_command_expiry_invalid$",
    ):
        command(keys(), now=now)


def component_plan(*, nested: dict | None = None, policy: str = "policy-v1") -> ExecutionPlan:
    node = (
        {
            "id": "nested",
            "node_type": "component",
            "output_artifacts": ["component-out"],
            "metadata": {"component": nested, "component_input": {}},
        }
        if nested
        else {"id": "inner", "output_artifacts": ["component-out"]}
    )
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "component-tenant",
            "plan_id": "component-plan",
            "workflow_id": "component-workflow",
            "policy_version": policy,
            "nodes": [node],
            "artifacts": [{"id": "component-out"}],
        }
    )


def root_plan(*, policy: str = "policy-v1") -> ExecutionPlan:
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-a",
            "plan_id": "root-plan",
            "workflow_id": "root-workflow",
            "policy_version": policy,
            "nodes": [
                {
                    "id": "reuse",
                    "node_type": "component",
                    "output_artifacts": ["final"],
                    "metadata": {
                        "component": {"id": "summarizer", "version": "1.0.0"},
                        "component_input": {"language": "de"},
                    },
                }
            ],
            "artifacts": [{"id": "final"}],
        }
    )


def component(*, version: str = "1.0.0", policy: str = "policy-v1", nested=None):
    return WorkflowComponent(
        component_id="summarizer",
        version=version,
        policy_version=policy,
        plan=component_plan(nested=nested, policy=policy),
        input_schema={
            "type": "object",
            "required": ["language"],
            "properties": {"language": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        output_artifacts=("component-out",),
    )


def test_component_compiler_flattens_versioned_component_and_maps_artifacts() -> None:
    registry = WorkflowComponentRegistry()
    registry.register(component())

    compiled = WorkflowComponentCompiler(registry).compile(root_plan())

    assert [node.node_id for node in compiled.nodes] == ["reuse/inner"]
    assert compiled.nodes[0].output_artifacts == ("final",)
    assert compiled.nodes[0].metadata["component_version"] == "1.0.0"
    assert compiled.metadata["compiled_components"] == {"reuse": "summarizer@1.0.0"}
    compiled.assert_valid()


def test_component_policy_narrowing_schema_cycle_and_n_minus_one_compatibility() -> None:
    registry = WorkflowComponentRegistry()
    registry.register(component())
    with pytest.raises(ValueError, match="input_invalid"):
        WorkflowComponentCompiler(registry).compile(
            replace(
                root_plan(),
                nodes=(
                    replace(
                        root_plan().nodes[0],
                        metadata={
                            "component": {"id": "summarizer", "version": "1.0.0"},
                            "component_input": {"language": 7},
                        },
                    ),
                ),
            )
        )

    policy_registry = WorkflowComponentRegistry()
    policy_registry.register(component(policy="policy-v2"))
    with pytest.raises(ValueError, match="policy_escalation"):
        WorkflowComponentCompiler(policy_registry).compile(root_plan())

    compatible = replace(component(version="1.1.0"), compatible_versions=("1.0.0",))
    compatibility_registry = WorkflowComponentRegistry()
    compatibility_registry.register(compatible)
    assert compatibility_registry.resolve("summarizer", "1.0.0").version == "1.1.0"

    recursive_registry = WorkflowComponentRegistry()
    recursive = component(nested={"id": "summarizer", "version": "1.0.0"})
    recursive_registry.register(recursive)
    with pytest.raises(ValueError, match="recursive_cycle"):
        WorkflowComponentCompiler(recursive_registry).compile(root_plan())

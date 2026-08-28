from __future__ import annotations

import base64
import builtins
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent.services.temporal_workflow_backend import TemporalWorkflowBackend
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_runtime import (
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_runtime.commands import WorkflowCommandIssuer
from ananta_contracts.hub_task_gateway import HubTaskContractError
from ananta_contracts.runtime_authorization_crypto import (
    ED25519_VERIFICATION_KEYRING_SCHEMA,
    Ed25519SigningKeyRing,
)
from ananta_contracts.temporal_workflow import (
    ActivityClass,
    AnantaWorkflowInput,
    AuthorizationEnvelopeRef,
    TemporalContractError,
    TemporalWorkflowStep,
    WorkflowCommand,
    WorkflowPhase,
)
from worker.temporal.authorization import RuntimeAuthorizationVerifier
from worker.temporal.config import TemporalWorkerConfig, TemporalWorkerConfigError
from worker.temporal.health import WorkerHealthServer, WorkerHealthState
from worker.temporal.hub_gateway import HubTaskReceipt
from worker.temporal.retry_profiles import (
    redacted_heartbeat_details,
    retry_profile_for,
    validate_all_retry_profiles,
)
from worker.temporal.workflows import AnantaWorkflow

ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "temporal-test-signing-key-32-bytes"


def _issued_envelope(
    *,
    workflow_id: str = "wf-1",
    run_id: str = "run-1",
    step_id: str = "step-1",
    now: float | None = None,
) -> RuntimeAuthorizationEnvelope:
    ring = HmacKeyRing({"runtime-key-1": SIGNING_KEY}, active_key_id="runtime-key-1")
    return RuntimeAuthorizationEnvelope.issue(
        key_ring=ring,
        tenant_id="tenant-1",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_tools=("read_file",),
        allowed_artifacts=("artifact-1",),
        budgets={"retries": 3},
        ttl_seconds=600,
        now=now,
        envelope_id=f"env-{step_id}",
        nonce=f"nonce-{step_id}",
    )


def test_temporal_authorization_wire_contract_matches_shared_runtime_contract() -> None:
    issued = _issued_envelope()
    transported = AuthorizationEnvelopeRef.from_mapping(issued.to_dict())

    assert transported.to_dict() == issued.to_dict()
    verifier = RuntimeAuthorizationVerifier(
        keys={"runtime-key-1": SIGNING_KEY},
        active_key_id="runtime-key-1",
    )
    verifier.verify(transported, now=issued.issued_at + 1)


def test_temporal_authorization_rejects_tampering_and_stale_binding() -> None:
    issued = _issued_envelope()
    tampered = {**issued.to_dict(), "plan_hash": "b" * 64}
    transported = AuthorizationEnvelopeRef.from_mapping(tampered)
    verifier = RuntimeAuthorizationVerifier(
        keys={"runtime-key-1": SIGNING_KEY},
        active_key_id="runtime-key-1",
    )
    with pytest.raises(TemporalContractError) as caught:
        verifier.verify(transported, now=issued.issued_at + 1)
    assert caught.value.reason_code == "signature_invalid"
    with pytest.raises(TemporalContractError, match="binding"):
        transported.validate_binding(
            tenant_id="tenant-other",
            workflow_id="wf-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="b" * 64,
        )


def test_temporal_worker_verifies_hub_ed25519_without_signing_material() -> None:
    trusted = Ed25519SigningKeyRing(
        {"runtime-key-1": base64.b64encode(b"t" * 32)},
        active_key_id="runtime-key-1",
    )
    rogue = Ed25519SigningKeyRing(
        {"runtime-key-1": base64.b64encode(b"r" * 32)},
        active_key_id="runtime-key-1",
    )

    def issue(signer: Ed25519SigningKeyRing) -> AuthorizationEnvelopeRef:
        return AuthorizationEnvelopeRef.from_mapping(
            RuntimeAuthorizationEnvelope.issue(
                key_ring=signer,
                tenant_id="tenant-1",
                workflow_id="wf-1",
                run_id="run-1",
                step_id="step-1",
                plan_hash="a" * 64,
                policy_version="policy-v1",
                now=100,
                ttl_seconds=60,
            ).to_dict()
        )

    verifier = RuntimeAuthorizationVerifier.from_config_mapping(trusted.verification_mapping())
    verifier.verify(issue(trusted), now=101)
    with pytest.raises(TemporalContractError) as caught:
        verifier.verify(issue(rogue), now=101)
    assert caught.value.reason_code == "signature_invalid"


def test_workflow_input_is_framework_neutral_bounded_and_secret_free() -> None:
    auth = AuthorizationEnvelopeRef.from_mapping(_issued_envelope().to_dict())
    step = TemporalWorkflowStep(
        step_id="step-1",
        title="Compile",
        operation_id="operation-1",
        authorization_envelope=auth,
        activity_class=ActivityClass.LONG_RUNNING,
    )
    value = AnantaWorkflowInput(
        tenant_id="tenant-1",
        workflow_id="wf-1",
        run_id="run-1",
        correlation_id="correlation-1",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        steps=(step,),
        retry_budget_remaining=3,
        mutable_parameters=("mode",),
        parameters={"mode": "safe"},
    )
    assert value.to_dict()["steps"][0]["activity_class"] == "long_running"
    assert value.to_dict()["retry_budget_maximum"] == 3
    assert value.to_dict()["max_parallel_steps"] == 1

    with pytest.raises(TemporalContractError, match="references, not secrets"):
        AnantaWorkflowInput(
            tenant_id="tenant-1",
            workflow_id="wf-1",
            run_id="run-1",
            correlation_id="correlation-1",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            steps=(step,),
            retry_budget_remaining=3,
            mutable_parameters=("api_key",),
            parameters={"api_key": "do-not-store"},
        )


def test_temporal_parallel_and_merge_contracts_are_explicit_and_bounded() -> None:
    workflow_id = "wf-parallel"
    run_id = "run-parallel"
    branch = TemporalWorkflowStep(
        step_id="branch",
        title="Branch",
        operation_id="operation-branch",
        authorization_envelope=AuthorizationEnvelopeRef.from_mapping(
            _issued_envelope(
                workflow_id=workflow_id,
                run_id=run_id,
                step_id="branch",
            ).to_dict()
        ),
    )
    merge = TemporalWorkflowStep(
        step_id="merge",
        title="Merge",
        operation_id="operation-merge",
        authorization_envelope=AuthorizationEnvelopeRef.from_mapping(
            _issued_envelope(
                workflow_id=workflow_id,
                run_id=run_id,
                step_id="merge",
            ).to_dict()
        ),
        depends_on=("branch",),
        node_type="merge",
        merge_strategy="ordered_artifact_refs",
        partial_failure="omit",
    )
    value = AnantaWorkflowInput(
        tenant_id="tenant-1",
        workflow_id=workflow_id,
        run_id=run_id,
        correlation_id="correlation-parallel",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        steps=(branch, merge),
        retry_budget_remaining=2,
        max_parallel_steps=8,
        tenant_parallel_limit=3,
        worker_parallel_limit=4,
    )

    round_trip = AnantaWorkflowInput.from_mapping(value.to_dict())

    assert round_trip.max_parallel_steps == 8
    assert round_trip.tenant_parallel_limit == 3
    assert round_trip.worker_parallel_limit == 4
    assert round_trip.steps[1].merge_strategy == "ordered_artifact_refs"
    assert round_trip.steps[1].partial_failure == "omit"

    with pytest.raises(TemporalContractError, match="merge strategy"):
        TemporalWorkflowStep(
            step_id="invalid-merge",
            title="Invalid",
            operation_id="operation-invalid",
            authorization_envelope=AuthorizationEnvelopeRef.from_mapping(
                _issued_envelope(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id="invalid-merge",
                ).to_dict()
            ),
            depends_on=("branch",),
            node_type="merge",
        )
    with pytest.raises(TemporalContractError, match="max_parallel_steps"):
        AnantaWorkflowInput(
            tenant_id="tenant-1",
            workflow_id=workflow_id,
            run_id=run_id,
            correlation_id="correlation-invalid",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            steps=(branch,),
            retry_budget_remaining=0,
            max_parallel_steps=0,
        )


def test_temporal_retry_profiles_are_finite_and_non_idempotent_is_never_blindly_retried() -> None:
    validate_all_retry_profiles()
    non_idempotent = retry_profile_for(ActivityClass.NON_IDEMPOTENT)
    options = non_idempotent.temporal_options(retry_budget_remaining=100)
    assert options["retry_policy"].maximum_attempts == 1
    assert retry_profile_for(ActivityClass.READ_ONLY).maximum_attempts == 5
    assert retry_profile_for(ActivityClass.LONG_RUNNING).heartbeat_seconds == 20


def test_heartbeat_details_are_content_free() -> None:
    details = redacted_heartbeat_details(
        operation_id="operation-1",
        hub_task_id="task-1",
        checkpoint_ref="artifact://checkpoint-1",
    )
    assert set(details) == {"schema", "operation_id", "hub_task_id", "checkpoint_ref", "stage"}
    assert "prompt" not in json.dumps(details).lower()


def test_worker_config_requires_absolute_credential_references_and_supports_keyring(tmp_path: Path) -> None:
    asymmetric = Ed25519SigningKeyRing(
        {"runtime-key-1": base64.b64encode(bytes(range(32))).decode("ascii")},
        active_key_id="runtime-key-1",
    )
    keyring = tmp_path / "runtime-keyring.json"
    keyring.write_text(
        json.dumps(asymmetric.verification_mapping()),
        encoding="utf-8",
    )
    keyring.chmod(0o600)
    token = tmp_path / "hub-token"
    token.write_text("signed-hub-service-token", encoding="utf-8")
    token.chmod(0o600)
    config = TemporalWorkerConfig.from_env(
        {
            "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE": str(keyring),
            "ANANTA_TEMPORAL_HUB_URL": "http://ai-agent-hub:5000",
            "ANANTA_TEMPORAL_HUB_TOKEN_FILE": str(token),
            "ANANTA_WORKFLOW_SERVICE_ID": "ananta-temporal-worker",
        }
    )
    assert config.productive_gateway_configured is True
    assert config.read_authorization_keyring()["schema"] == ED25519_VERIFICATION_KEYRING_SCHEMA
    assert config.read_hub_token() == "signed-hub-service-token"

    with pytest.raises(TemporalWorkerConfigError, match="service identity"):
        TemporalWorkerConfig.from_env(
            {
                "ANANTA_TEMPORAL_HUB_URL": "http://ai-agent-hub:5000",
                "ANANTA_TEMPORAL_HUB_TOKEN_FILE": str(token),
            }
        )

    legacy = tmp_path / "legacy-runtime-keyring.json"
    legacy.write_text(
        json.dumps(
            {
                "active_key_id": "runtime-key-1",
                "keys": {"runtime-key-1": SIGNING_KEY},
            }
        ),
        encoding="utf-8",
    )
    legacy.chmod(0o600)
    with pytest.raises(TemporalWorkerConfigError, match="legacy HMAC"):
        TemporalWorkerConfig.from_env({"ANANTA_TEMPORAL_AUTH_KEYRING_FILE": str(legacy)})
    compatibility = TemporalWorkerConfig.from_env(
        {
            "ANANTA_TEMPORAL_AUTH_KEYRING_FILE": str(legacy),
            "ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING": "1",
        }
    )
    assert compatibility.authorization_keyring_file == str(legacy)

    with pytest.raises(TemporalWorkerConfigError, match="absolute"):
        TemporalWorkerConfig.from_env({"ANANTA_TEMPORAL_API_KEY_FILE": "relative-secret"})


def test_native_import_path_does_not_require_temporal_sdk() -> None:
    script = r"""
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "temporalio" or name.startswith("temporalio."):
        raise ModuleNotFoundError("temporal intentionally unavailable")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import worker.temporal
import agent.services.temporal_workflow_backend
print("native-import-ok")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "native-import-ok"


def test_temporal_backend_reports_lazy_sdk_unavailability(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "temporalio.client":
            raise ModuleNotFoundError("temporal disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    backend = TemporalWorkflowBackend()
    status = backend.get_workflow_status("wf-1")
    assert status["status"] == "degraded"
    assert status["reason"] == "temporalio_unavailable:ModuleNotFoundError"


def test_temporal_backend_maps_typed_worker_rejection_to_bound_command_result() -> None:
    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.exceptions import ApplicationError

    signed = WorkflowCommandIssuer(
        HmacKeyRing({"command-key": "x" * 32}, active_key_id="command-key"),
        clock=lambda: 10.0,
    ).issue(
        command_id="command-rejected",
        command_type="pause",
        tenant_id="tenant-1",
        workflow_id="wf-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-0",
        expected_revision=0,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        actor_id="owner-1",
        actor_roles=("operator",),
        payload={},
    )

    class RejectedBackend(TemporalWorkflowBackend):
        @staticmethod
        def _temporal_unavailable() -> str:
            return ""

        async def _update(self, *_args, **_kwargs):
            raise WorkflowUpdateFailedError(
                ApplicationError(
                    "workflow command rejected",
                    type="stale_workflow_revision",
                    non_retryable=True,
                )
            )

        async def _query(self, _workflow_id: str, _query_name: str):
            return {
                "schema": "ananta.temporal-workflow-status.v1",
                "workflow_id": "wf-1",
                "run_id": "run-1",
                "plan_hash": "a" * 64,
                "revision": 1,
                "status": "running",
            }

    result = RejectedBackend().update_workflow(
        "wf-1",
        WorkflowCommand.from_mapping(signed.to_dict()).to_dict(),
        update_id=signed.command_id,
    )

    assert result == {
        "schema": "ananta.temporal-workflow-command-result.v2",
        "command_id": signed.command_id,
        "accepted": False,
        "revision": 1,
        "status": "running",
        "reason_code": "stale_workflow_revision",
    }


@pytest.mark.parametrize("phase", [WorkflowPhase.RUNNING, WorkflowPhase.COMPLETED])
def test_temporal_worker_retry_is_typed_rejection_without_revision_mutation(
    phase: WorkflowPhase,
) -> None:
    signed = WorkflowCommandIssuer(
        HmacKeyRing({"command-key": "x" * 32}, active_key_id="command-key"),
        clock=lambda: 10.0,
    ).issue(
        command_id=f"command-retry-{phase.value}",
        command_type="retry",
        tenant_id="tenant-1",
        workflow_id="wf-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-0",
        expected_revision=0,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        actor_id="owner-1",
        actor_roles=("operator",),
        payload={},
    )
    subject = AnantaWorkflow()
    subject._input = object()
    subject._phase = phase
    subject._revision = 0

    with pytest.raises(TemporalContractError) as rejected:
        subject._validate_command(WorkflowCommand.from_mapping(signed.to_dict()))

    assert rejected.value.reason_code == "temporal_retry_unsupported"
    assert subject._revision == 0


@pytest.mark.parametrize("command_type", ["edit", "request_changes"])
def test_temporal_worker_plan_edit_is_typed_rejection_without_revision_mutation(
    command_type: str,
) -> None:
    signed = WorkflowCommandIssuer(
        HmacKeyRing({"command-key": "x" * 32}, active_key_id="command-key"),
        clock=lambda: 10.0,
    ).issue(
        command_id=f"command-{command_type}",
        command_type=command_type,
        tenant_id="tenant-1",
        workflow_id="wf-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-0",
        expected_revision=0,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        actor_id="owner-1",
        actor_roles=("operator",),
        payload={
            "replacement_plan": {"nodes": []},
            "replacement_plan_hash": "b" * 64,
        },
    )
    subject = AnantaWorkflow()
    subject._input = object()
    subject._phase = WorkflowPhase.RUNNING
    subject._revision = 0

    with pytest.raises(TemporalContractError) as rejected:
        subject._validate_command(WorkflowCommand.from_mapping(signed.to_dict()))

    assert rejected.value.reason_code == "temporal_plan_edit_unsupported"
    assert subject._revision == 0


def test_temporal_backend_rejects_incomplete_runtime_binding_before_network_call() -> None:
    backend = TemporalWorkflowBackend(address="127.0.0.1:1")
    request = WorkflowRequest(
        workflow_id="wf-1",
        steps=(WorkflowStepRequest(step_id="step-1", policy_scope={"write": False}),),
        policy_scope={"write": False},
    )
    status = backend.start_workflow(request)
    assert status["status"] == "degraded"
    assert status["reason"] == "invalid_temporal_workflow_input"


def test_hub_receipt_requires_authorization_and_ledger_confirmation() -> None:
    valid = HubTaskReceipt.from_mapping(
        {
            "schema": "ananta.temporal-hub-task-receipt.v1",
            "hub_task_id": "task-1",
            "operation_id": "operation-1",
            "status": "created",
            "authorization_state": "valid",
            "ledger_state": "authorized",
            "artifact_refs": [],
            "canonical_event_refs": [],
        }
    )
    assert valid.hub_task_id == "task-1"
    with pytest.raises(HubTaskContractError, match="hub_authorization_not_revalidated"):
        HubTaskReceipt.from_mapping(
            {
                "schema": "ananta.temporal-hub-task-receipt.v1",
                "hub_task_id": "task-1",
                "operation_id": "operation-1",
                "status": "created",
                "authorization_state": "unknown",
                "ledger_state": "authorized",
                "artifact_refs": [],
                "canonical_event_refs": [],
            }
        )


def test_health_server_removes_readiness_before_liveness() -> None:
    state = WorkerHealthState(build_id="build-1", identity="worker-1")
    server = WorkerHealthServer(host="127.0.0.1", port=0, state=state)
    server.start()
    host, port = server.address
    try:
        with pytest.raises(urllib.error.HTTPError) as starting:
            urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=2)
        assert starting.value.code == 503
        state.ready()
        assert json.load(urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=2))["ready"] is True
        state.draining()
        with pytest.raises(urllib.error.HTTPError) as draining:
            urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=2)
        assert draining.value.code == 503
        assert json.load(urllib.request.urlopen(f"http://{host}:{port}/live", timeout=2))["live"] is True
    finally:
        server.close()

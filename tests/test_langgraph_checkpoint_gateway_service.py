from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent.services.langgraph_checkpoint_gateway_service import (
    LangGraphCheckpointGatewayError,
    LangGraphCheckpointGatewayService,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    RuntimeAuthorizationEnvelope,
    SignedCheckpoint,
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
    WorkflowState,
)
from agent.services.workflow_worker_assignment_service import (
    InMemoryWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
)
from ananta_contracts.langgraph_checkpoint import (
    LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
    LangGraphCheckpointBinding,
)


class _Clock:
    value = 1_000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def checkpoint_runtime():
    clock = _Clock()
    key_ring = HmacKeyRing({"runtime-v1": "k" * 32}, active_key_id="runtime-v1")
    ownership = InMemoryExecutionOwnershipStore()
    claim = ownership.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="task-a",
        owner_id="workflow-adapter-task-queue",
        lease_seconds=600,
        maximum_retries=3,
        now=clock.value,
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="task-a",
        plan_hash="plan-a",
        policy_version="policy-a",
        ttl_seconds=3_600,
        now=clock.value,
    )
    binding = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "task-a",
        "task_id": "task-a",
        "plan_hash": "plan-a",
        "policy_version": "policy-a",
        "fencing_token": claim.ownership.fencing_token,
        "authorization_envelope": envelope.to_dict(),
    }
    store = InMemoryCheckpointStore()
    assignments = InMemoryWorkflowWorkerAssignmentStore()
    assignments.bind(
        WorkflowWorkerAssignment(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="task-a",
            attempt_id=claim.ownership.attempt_id,
            fencing_token=claim.ownership.fencing_token,
            hub_task_id="workflow-adapter-task-1",
            worker_id="worker-a",
            worker_url="http://worker-a:5000",
            assigned_at=clock.value,
        )
    )
    service = LangGraphCheckpointGatewayService(
        checkpoints=store,
        ownership=ownership,
        key_ring=key_ring,
        authorization=AuthorizationVerifier(key_ring),
        commands=WorkflowCommandVerifier(
            key_ring,
            InMemoryReplayNonceStore(clock=clock),
        ),
        assignments=assignments,
        clock=clock,
    )
    return service, store, ownership, key_ring, clock, binding


def _config(checkpoint_id: str = "") -> dict:
    configurable = {"thread_id": "task-a", "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _command(operation: str, binding: dict, **values) -> dict:
    return {
        "schema": LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
        "operation": operation,
        "binding": binding,
        **values,
    }


def test_authenticated_langgraph_worker_must_own_checkpoint_lease(
    checkpoint_runtime,
) -> None:
    service, _store, _ownership, _key_ring, _clock, binding = checkpoint_runtime
    command = _command("get", binding, config=_config())

    accepted = service.execute(
        command,
        authenticated_worker_id="worker-a",
        authenticated_worker_url="http://worker-a:5000",
    )
    assert accepted["snapshot"] is None

    with pytest.raises(LangGraphCheckpointGatewayError) as foreign:
        service.execute(
            command,
            authenticated_worker_id="worker-b",
            authenticated_worker_url="http://worker-b:5000",
        )
    assert foreign.value.status_code == 403
    assert foreign.value.reason_code == (
        "langgraph_checkpoint_authenticated_owner_mismatch"
    )


def _put(service, binding: dict, checkpoint_id: str, revision: int, **checkpoint_values):
    return service.execute(
        _command(
            "put",
            binding,
            config=_config(),
            checkpoint={"id": checkpoint_id, "channel_values": {}, **checkpoint_values},
            metadata={"source": "loop"},
            expected_revision=revision,
        )
    )["snapshot"]


def _control_command(
    *,
    key_ring: HmacKeyRing,
    binding: dict,
    checkpoint: SignedCheckpoint,
    command_type: str,
    clock: _Clock,
    command_id: str,
    payload: dict | None = None,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=key_ring,
        command_type=command_type,
        tenant_id=binding["tenant_id"],
        workflow_id=binding["workflow_id"],
        run_id=binding["run_id"],
        step_id=binding["step_id"],
        checkpoint_id=checkpoint.checkpoint_id,
        expected_revision=checkpoint.revision,
        plan_hash=binding["plan_hash"],
        policy_version=binding["policy_version"],
        actor_id="operator-a",
        actor_roles=("operator",),
        payload=payload,
        now=clock.value,
        command_id=command_id,
        nonce=f"nonce-{command_id}",
    )


def test_signed_checkpoint_round_trip_list_and_pending_writes(checkpoint_runtime) -> None:
    service, store, _ownership, key_ring, _clock, binding = checkpoint_runtime

    first = _put(service, binding, "checkpoint-1", 0)
    writes = service.execute(
        _command(
            "put_writes",
            binding,
            config=_config("checkpoint-1"),
            pending_writes=[["node-task", "messages", {"value": "ok"}]],
            expected_revision=1,
        )
    )["snapshot"]
    found = service.execute(_command("get", binding, config=_config()))["snapshot"]
    listed = service.execute(
        _command(
            "list",
            binding,
            config=_config(),
            metadata_filter={"source": "loop"},
            limit=10,
        )
    )["snapshots"]

    assert first["revision"] == 1
    assert writes["revision"] == 2
    assert found["pending_writes"] == [["node-task", "messages", {"value": "ok"}]]
    # LangGraph lists logical checkpoints, not the Hub's immutable storage
    # revisions used to append pending writes for that checkpoint.
    assert [item["revision"] for item in listed] == [2]
    latest = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
    assert latest is not None
    latest.verify(
        key_ring=key_ring,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        task_id="task-a",
        plan_hash="plan-a",
        policy_version="policy-a",
    )


def test_put_is_idempotent_but_concurrent_payload_conflicts(checkpoint_runtime) -> None:
    service, *_rest, binding = checkpoint_runtime
    first = _put(service, binding, "checkpoint-1", 0)
    duplicate = _put(service, binding, "checkpoint-1", 0)

    assert duplicate["signed_checkpoint_ref"] == first["signed_checkpoint_ref"]
    with pytest.raises(LangGraphCheckpointGatewayError, match="checkpoint_revision_conflict"):
        _put(service, binding, "checkpoint-2", 0)
    with pytest.raises(LangGraphCheckpointGatewayError, match="langgraph_checkpoint_id_payload_conflict"):
        _put(service, binding, "checkpoint-1", 1, changed=True)


def test_compare_and_set_allows_only_one_concurrent_writer(checkpoint_runtime) -> None:
    service, store, *_rest, binding = checkpoint_runtime

    def write(checkpoint_id: str) -> tuple[str, str]:
        try:
            snapshot = _put(service, binding, checkpoint_id, 0)
            return "stored", str(snapshot["checkpoint"]["id"])
        except LangGraphCheckpointGatewayError as exc:
            return "rejected", exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(write, ("checkpoint-a", "checkpoint-b")))

    assert [status for status, _value in outcomes].count("stored") == 1
    assert [status for status, _value in outcomes].count("rejected") == 1
    assert any("checkpoint_revision_conflict" in value for status, value in outcomes if status == "rejected")
    assert len(store.list_history(tenant_id="tenant-a", run_id="run-a", task_id="task-a")) == 1


def test_pending_writes_for_older_checkpoint_do_not_replace_logical_head(
    checkpoint_runtime,
) -> None:
    service, *_rest, binding = checkpoint_runtime
    _put(service, binding, "checkpoint-1", 0)
    _put(service, binding, "checkpoint-2", 1)

    updated_old = service.execute(
        _command(
            "put_writes",
            binding,
            config=_config("checkpoint-1"),
            pending_writes=[["node-task", "messages", {"value": "late"}]],
            expected_revision=2,
        )
    )["snapshot"]
    latest = service.execute(_command("get", binding, config=_config()))["snapshot"]
    selected_old = service.execute(_command("get", binding, config=_config("checkpoint-1")))["snapshot"]
    listed = service.execute(_command("list", binding, config=_config(), metadata_filter={}, limit=10))["snapshots"]

    assert updated_old["revision"] == 3
    assert latest["checkpoint"]["id"] == "checkpoint-2"
    assert latest["revision"] == 2
    assert latest["head_revision"] == 3
    assert selected_old["pending_writes"] == [["node-task", "messages", {"value": "late"}]]
    assert [snapshot["checkpoint"]["id"] for snapshot in listed] == [
        "checkpoint-2",
        "checkpoint-1",
    ]


def test_tampered_signature_and_cross_runtime_checkpoint_fail_closed(
    checkpoint_runtime,
) -> None:
    service, store, _ownership, key_ring, clock, binding = checkpoint_runtime
    _put(service, binding, "checkpoint-1", 0)
    latest = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
    assert latest is not None

    tampered_store = InMemoryCheckpointStore()
    tampered_store.save(replace(latest, signature="0" * 64), expected_revision=0)
    tampered = LangGraphCheckpointGatewayService(
        checkpoints=tampered_store,
        ownership=_ownership,
        key_ring=key_ring,
        authorization=AuthorizationVerifier(key_ring),
        clock=clock,
    )
    with pytest.raises(LangGraphCheckpointGatewayError, match="signature_invalid"):
        tampered.execute(_command("get", binding, config=_config()))

    native_store = InMemoryCheckpointStore()
    native = SignedCheckpoint.issue(
        key_ring=key_ring,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        task_id="task-a",
        plan_hash="plan-a",
        policy_version="policy-a",
        runtime_id="ananta-native",
        runtime_version="1",
        state=WorkflowState(business_data={"native": {"value": 1}}),
        revision=1,
        fencing_token=1,
        now=clock.value,
    )
    native_store.save(native, expected_revision=0)
    cross_runtime = LangGraphCheckpointGatewayService(
        checkpoints=native_store,
        ownership=_ownership,
        key_ring=key_ring,
        authorization=AuthorizationVerifier(key_ring),
        clock=clock,
    )
    with pytest.raises(LangGraphCheckpointGatewayError, match="langgraph_checkpoint_cross_runtime_rejected"):
        cross_runtime.execute(_command("get", binding, config=_config()))


def test_stale_fence_cross_tenant_and_embedded_secrets_are_rejected(
    checkpoint_runtime,
) -> None:
    service, _store, ownership, _key_ring, clock, binding = checkpoint_runtime

    with pytest.raises(LangGraphCheckpointGatewayError, match="workflow_state_embedded_secret_denied"):
        _put(service, binding, "checkpoint-secret", 0, api_key="not-a-reference")
    _put(service, binding, "checkpoint-before-crash", 0)

    foreign = dict(binding)
    foreign["tenant_id"] = "tenant-b"
    with pytest.raises(LangGraphCheckpointGatewayError):
        service.execute(_command("get", foreign, config=_config()))

    clock.value = 2_000.0
    recovered = ownership.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="task-a",
        owner_id="worker-b",
        lease_seconds=600,
        maximum_retries=3,
        now=clock.value,
    )
    assert recovered.ownership.fencing_token > binding["fencing_token"]
    with pytest.raises(
        LangGraphCheckpointGatewayError,
        match="langgraph_checkpoint_fencing_or_ownership_mismatch",
    ):
        service.execute(_command("get", binding, config=_config()))
    recovered_binding = dict(binding)
    recovered_binding["fencing_token"] = recovered.ownership.fencing_token
    resumed = service.execute(_command("get", recovered_binding, config=_config()))["snapshot"]
    assert resumed["checkpoint"]["id"] == "checkpoint-before-crash"


def test_hub_signed_langgraph_commands_are_revision_bound_and_replay_safe(
    checkpoint_runtime,
) -> None:
    service, store, _ownership, key_ring, clock, binding = checkpoint_runtime
    _put(service, binding, "checkpoint-1", 0)
    current = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
    assert current is not None
    pause = _control_command(
        key_ring=key_ring,
        binding=binding,
        checkpoint=current,
        command_type="pause",
        clock=clock,
        command_id="pause-1",
    )
    result = service.apply_workflow_command(
        binding=LangGraphCheckpointBinding.from_mapping(binding),
        raw_command=pause.to_dict(),
    )
    assert result["status"] == "paused"
    assert result["revision"] == 2

    with pytest.raises(LangGraphCheckpointGatewayError, match="mismatch|replay"):
        service.apply_workflow_command(
            binding=LangGraphCheckpointBinding.from_mapping(binding),
            raw_command=pause.to_dict(),
        )

    stale = _control_command(
        key_ring=key_ring,
        binding=binding,
        checkpoint=current,
        command_type="resume",
        clock=clock,
        command_id="stale-resume",
    )
    with pytest.raises(LangGraphCheckpointGatewayError, match="mismatch"):
        service.apply_workflow_command(
            binding=LangGraphCheckpointBinding.from_mapping(binding),
            raw_command=stale.to_dict(),
        )


def test_langgraph_control_transitions_cover_pause_resume_edit_and_request_changes(
    checkpoint_runtime,
) -> None:
    service, store, _ownership, key_ring, clock, binding = checkpoint_runtime
    parsed_binding = LangGraphCheckpointBinding.from_mapping(binding)
    _put(service, binding, "checkpoint-1", 0)

    def apply(command_type: str, command_id: str, payload: dict | None = None):
        current = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
        assert current is not None
        command = _control_command(
            key_ring=key_ring,
            binding=binding,
            checkpoint=current,
            command_type=command_type,
            clock=clock,
            command_id=command_id,
            payload=payload,
        )
        return service.apply_workflow_command(
            binding=parsed_binding,
            raw_command=command.to_dict(),
        )

    assert apply("pause", "pause-1")["status"] == "paused"
    assert apply("resume", "resume-1")["status"] == "running"
    edited = apply(
        "edit",
        "edit-1",
        {
            "plan_ref": "artifact://plans/replacement-1",
            "replacement_plan_hash": "a" * 64,
        },
    )
    assert edited["status"] == "paused"
    assert edited["plan_revision"] == 2
    assert edited["plan_hash"] == "a" * 64
    with pytest.raises(
        LangGraphCheckpointGatewayError,
        match="plan_reauthorization_required",
    ):
        apply("resume", "resume-without-reauthorization")

    requested = apply(
        "request_changes",
        "request-changes-1",
        {
            "plan_ref": "artifact://plans/replacement-2",
            "replacement_plan_hash": "b" * 64,
        },
    )
    assert requested["status"] == "paused"
    assert requested["plan_revision"] == 3


def test_langgraph_approve_and_reject_require_an_open_checkpoint_gate(
    checkpoint_runtime,
) -> None:
    service, store, _ownership, key_ring, clock, binding = checkpoint_runtime
    parsed_binding = LangGraphCheckpointBinding.from_mapping(binding)
    _put(service, binding, "checkpoint-1", 0)

    def append_open_gate() -> SignedCheckpoint:
        current = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
        assert current is not None
        state = WorkflowState(
            business_data=dict(current.state.business_data),
            runtime_metadata=dict(current.state.runtime_metadata),
            secret_refs=current.state.secret_refs,
            artifact_refs=current.state.artifact_refs,
            open_gates=(binding["step_id"],),
        )
        gate = SignedCheckpoint.issue(
            key_ring=key_ring,
            tenant_id=current.tenant_id,
            workflow_id=current.workflow_id,
            run_id=current.run_id,
            task_id=current.task_id,
            plan_hash=current.plan_hash,
            policy_version=current.policy_version,
            runtime_id=current.runtime_id,
            runtime_version=current.runtime_version,
            state=state,
            revision=current.revision + 1,
            fencing_token=current.fencing_token,
            now=clock.value,
        )
        return store.save(gate, expected_revision=current.revision)

    def apply(command_type: str, command_id: str):
        current = store.get_latest(tenant_id="tenant-a", run_id="run-a", task_id="task-a")
        assert current is not None
        command = _control_command(
            key_ring=key_ring,
            binding=binding,
            checkpoint=current,
            command_type=command_type,
            clock=clock,
            command_id=command_id,
        )
        return service.apply_workflow_command(
            binding=parsed_binding,
            raw_command=command.to_dict(),
        )

    append_open_gate()
    approved = apply("approve", "approve-1")
    assert approved["accepted"] is True
    assert approved["status"] == "running"

    append_open_gate()
    rejected = apply("reject", "reject-1")
    assert rejected["status"] == "rejected"

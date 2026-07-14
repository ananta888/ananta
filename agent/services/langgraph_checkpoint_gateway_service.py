"""Hub-owned checkpoint gateway for an isolated LangGraph worker.

The service owns persistence, signatures, authorization revalidation and
fencing.  It intentionally depends only on neutral LangGraph wire contracts;
the Hub never imports the Worker or LangGraph packages.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    CheckpointStore,
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
    SignedCheckpoint,
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
    WorkflowState,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    ContractValidationError,
    FencingTokenError,
    OptimisticConcurrencyError,
    SignatureValidationError,
)
from agent.services.workflow_runtime.ownership import ExecutionOwnership
from agent.services.workflow_worker_assignment_service import (
    WorkflowWorkerAssignmentStore,
)
from ananta_contracts.langgraph_checkpoint import (
    LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
    LANGGRAPH_CHECKPOINT_OPERATIONS,
    LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA,
    LANGGRAPH_CHECKPOINT_RUNTIME_ID,
    LANGGRAPH_CHECKPOINT_RUNTIME_VERSION,
    MAX_LANGGRAPH_CHECKPOINT_HISTORY,
    LangGraphCheckpointBinding,
    LangGraphCheckpointContractError,
    LangGraphCheckpointSnapshot,
    assert_json_mapping,
    assert_langgraph_config_binding,
    normalize_pending_writes,
)

_STATE_KEY = "langgraph_checkpoint"


class LangGraphCheckpointGatewayError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class CheckpointHistoryStore(CheckpointStore, Protocol):
    def list_history(self, *, tenant_id: str, run_id: str, task_id: str) -> tuple[SignedCheckpoint, ...]: ...


class OwnershipReadPort(Protocol):
    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None: ...


class LangGraphCommandPolicyPort(Protocol):
    """Hub policy/budget/side-effect revalidation for control decisions."""

    def authorize(
        self,
        *,
        command: SignedWorkflowCommand,
        checkpoint: SignedCheckpoint,
    ) -> tuple[bool, str]: ...


class BoundLangGraphCommandPolicy:
    """Conservative policy for a checkpoint-bound LangGraph control action."""

    _TERMINAL = frozenset({"completed", "failed", "cancelled", "rejected"})

    def authorize(
        self,
        *,
        command: SignedWorkflowCommand,
        checkpoint: SignedCheckpoint,
    ) -> tuple[bool, str]:
        control = _control_state(checkpoint.state)
        status = str(control.get("status") or "running")
        if status in self._TERMINAL:
            return False, "langgraph_command_terminal_state"
        if command.command_type == "resume":
            if status != "paused":
                return False, "langgraph_command_not_paused"
            if bool(control.get("reauthorization_required")):
                return False, "langgraph_command_plan_reauthorization_required"
            budget_remaining = control.get("budget_remaining")
            if isinstance(budget_remaining, (int, float)) and budget_remaining <= 0:
                return False, "langgraph_command_budget_exhausted"
            side_effect_state = str(control.get("side_effect_state") or "idle")
            if side_effect_state not in {"idle", "completed", "compensated"}:
                return False, "langgraph_command_side_effect_unresolved"
        return True, "allowed"


class LangGraphCheckpointGatewayService:
    """Translate strict wire commands into signed, immutable Hub checkpoints."""

    def __init__(
        self,
        *,
        checkpoints: CheckpointHistoryStore,
        ownership: OwnershipReadPort,
        key_ring: HmacKeyRing,
        authorization: AuthorizationVerifier,
        commands: WorkflowCommandVerifier | None = None,
        command_policy: LangGraphCommandPolicyPort | None = None,
        assignments: WorkflowWorkerAssignmentStore | None = None,
        clock: Any = time.time,
    ) -> None:
        self._checkpoints = checkpoints
        self._ownership = ownership
        self._key_ring = key_ring
        self._authorization = authorization
        self._commands = commands
        self._command_policy = command_policy or BoundLangGraphCommandPolicy()
        self._assignments = assignments
        self._clock = clock

    def apply_workflow_command(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        raw_command: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply one Hub-signed command as an immutable CAS checkpoint."""

        if self._commands is None:
            self._deny("langgraph_workflow_command_verifier_required", 503)
        self._authorize(binding, writing=True)
        command = SignedWorkflowCommand.from_mapping(dict(raw_command))
        history = self._history(binding)
        if not history:
            self._deny("langgraph_checkpoint_not_found", 404)
        current = history[-1]
        assert self._commands is not None
        try:
            self._commands.verify_once(
                command,
                tenant_id=binding.tenant_id,
                workflow_id=binding.workflow_id,
                run_id=binding.run_id,
                step_id=binding.step_id,
                checkpoint_id=current.checkpoint_id,
                expected_revision=current.revision,
                plan_hash=binding.plan_hash,
                policy_version=binding.policy_version,
                now=float(self._clock()),
            )
        except (ContractValidationError, SignatureValidationError) as exc:
            self._deny(str(exc), 409)
        control = _control_state(current.state)
        processed = tuple(str(value) for value in control.get("processed_command_ids") or ())
        if command.command_id in processed:
            self._deny("workflow_command_replay_detected", 409)
        allowed, reason = self._command_policy.authorize(command=command, checkpoint=current)
        if not allowed:
            self._deny(reason or "langgraph_workflow_command_policy_denied", 403)
        updated_state = self._apply_control_transition(current.state, command, processed=processed)
        signed = SignedCheckpoint.issue(
            key_ring=self._key_ring,
            tenant_id=current.tenant_id,
            workflow_id=current.workflow_id,
            run_id=current.run_id,
            task_id=current.task_id,
            plan_hash=current.plan_hash,
            policy_version=current.policy_version,
            runtime_id=current.runtime_id,
            runtime_version=current.runtime_version,
            state=updated_state,
            revision=current.revision + 1,
            fencing_token=current.fencing_token,
            now=float(self._clock()),
        )
        stored = self._checkpoints.save(signed, expected_revision=current.revision)
        return {
            "schema": "ananta.langgraph-workflow-command-result.v1",
            "command_id": command.command_id,
            "command_type": command.command_type,
            "accepted": True,
            "revision": stored.revision,
            "checkpoint_id": stored.checkpoint_id,
            "status": str(_control_state(stored.state).get("status") or "running"),
            "plan_revision": int(_control_state(stored.state).get("plan_revision") or 1),
            "plan_hash": str(_control_state(stored.state).get("plan_hash") or stored.plan_hash),
        }

    @staticmethod
    def _apply_control_transition(
        state: WorkflowState,
        command: SignedWorkflowCommand,
        *,
        processed: tuple[str, ...],
    ) -> WorkflowState:
        control = _control_state(state)
        status = str(control.get("status") or "running")
        gates = set(state.open_gates)
        approved = set(str(value) for value in control.get("approved_gates") or ())
        if command.command_type in {"approve", "reject"}:
            if command.step_id not in gates:
                raise LangGraphCheckpointGatewayError("langgraph_command_gate_not_open", status_code=409)
            gates.discard(command.step_id)
            if command.command_type == "approve":
                approved.add(command.step_id)
                status = "running"
            else:
                status = "rejected"
        elif command.command_type == "pause":
            status = "paused"
        elif command.command_type == "resume":
            status = "running"
        elif command.command_type in {"edit", "request_changes"}:
            checkpoint_payload = state.business_data.get(_STATE_KEY)
            pending_writes = (
                checkpoint_payload.get("pending_writes")
                if isinstance(checkpoint_payload, Mapping)
                else ()
            )
            if pending_writes:
                raise LangGraphCheckpointGatewayError(
                    "langgraph_plan_edit_pending_writes_denied",
                    status_code=409,
                )
            control["plan_revision"] = int(control.get("plan_revision") or 1) + 1
            control["plan_hash"] = str(command.payload["replacement_plan_hash"])
            control["plan_ref"] = str(command.payload.get("plan_ref") or "inline-plan")[:256]
            control["reauthorization_required"] = True
            status = "paused"
        else:
            raise LangGraphCheckpointGatewayError(
                "langgraph_workflow_command_type_unsupported",
                status_code=422,
            )
        control.update(
            {
                "status": status,
                "approved_gates": sorted(approved),
                "last_command_id": command.command_id,
                "last_command_type": command.command_type,
                "last_actor_id": command.actor_id,
                "processed_command_ids": [*processed[-127:], command.command_id],
            }
        )
        runtime_metadata = dict(state.runtime_metadata)
        runtime_metadata["workflow_control"] = control
        return WorkflowState(
            business_data=dict(state.business_data),
            runtime_metadata=runtime_metadata,
            secret_refs=state.secret_refs,
            artifact_refs=state.artifact_refs,
            open_gates=tuple(sorted(gates)),
        )

    def execute(
        self,
        raw: Mapping[str, Any],
        *,
        authenticated_worker_id: str = "",
        authenticated_worker_url: str = "",
    ) -> dict[str, Any]:
        try:
            if raw.get("schema") != LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA:
                self._deny("langgraph_checkpoint_command_invalid", 400)
            operation = str(raw.get("operation") or "")
            if operation not in LANGGRAPH_CHECKPOINT_OPERATIONS:
                self._deny("langgraph_checkpoint_operation_unsupported", 422)
            binding = LangGraphCheckpointBinding.from_mapping(raw.get("binding"))
            self._assert_authenticated_worker_owns_lease(
                binding,
                authenticated_worker_id=authenticated_worker_id,
                authenticated_worker_url=authenticated_worker_url,
            )
            writing = operation in {"put", "put_writes"}
            self._authorize(binding, writing=writing)
            if operation == "get":
                response = self._get(binding, raw)
            elif operation == "list":
                response = self._list(binding, raw)
            elif operation == "put":
                response = self._put(binding, raw)
            else:
                response = self._put_writes(binding, raw)
            return {"schema": LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA, **response}
        except LangGraphCheckpointGatewayError:
            raise
        except LangGraphCheckpointContractError as exc:
            raise LangGraphCheckpointGatewayError(exc.reason_code, status_code=422) from exc
        except (ContractValidationError, SignatureValidationError) as exc:
            raise LangGraphCheckpointGatewayError(str(exc), status_code=403) from exc
        except FencingTokenError as exc:
            raise LangGraphCheckpointGatewayError(str(exc), status_code=409) from exc
        except OptimisticConcurrencyError as exc:
            raise LangGraphCheckpointGatewayError(str(exc), status_code=409) from exc
        except (TypeError, ValueError) as exc:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_command_invalid", status_code=422) from exc

    def _assert_authenticated_worker_owns_lease(
        self,
        binding: LangGraphCheckpointBinding,
        *,
        authenticated_worker_id: str,
        authenticated_worker_url: str,
    ) -> None:
        worker_id = str(authenticated_worker_id or "").strip()
        worker_url = str(authenticated_worker_url or "").strip()
        if not worker_id and not worker_url:
            return
        if (
            not worker_id
            or not worker_url
            or len(worker_id) > 256
            or len(worker_url) > 2_048
            or "\x00" in worker_id
            or "\x00" in worker_url
        ):
            self._deny("langgraph_checkpoint_authenticated_identity_invalid", 403)
        ownership = self._ownership.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if ownership is None:
            self._deny("langgraph_checkpoint_ownership_required", 403)
        assert ownership is not None
        if self._assignments is None:
            self._deny("langgraph_checkpoint_assignment_store_unavailable", 503)
        assert self._assignments is not None
        assignment = self._assignments.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if (
            assignment is None
            or assignment.workflow_id != binding.workflow_id
            or assignment.attempt_id != ownership.attempt_id
            or assignment.fencing_token != binding.fencing_token
            or assignment.worker_id != worker_id
            or assignment.worker_url != worker_url
        ):
            self._deny("langgraph_checkpoint_authenticated_owner_mismatch", 403)

    def _authorize(self, binding: LangGraphCheckpointBinding, *, writing: bool) -> None:
        envelope = RuntimeAuthorizationEnvelope.from_mapping(dict(binding.authorization_envelope))
        ownership = self._current_ownership(binding)
        self._authorization.authorize(
            envelope,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            writing=writing,
            hub_revalidator=(lambda _envelope: self._ownership_matches(binding, ownership)),
            now=float(self._clock()),
        )

    def _current_ownership(self, binding: LangGraphCheckpointBinding) -> ExecutionOwnership:
        ownership = self._ownership.get(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
        )
        if ownership is None:
            self._deny("langgraph_checkpoint_ownership_required", 403)
        assert ownership is not None
        if not self._ownership_matches(binding, ownership):
            self._deny("langgraph_checkpoint_fencing_or_ownership_mismatch", 409)
        return ownership

    def _ownership_matches(
        self,
        binding: LangGraphCheckpointBinding,
        ownership: ExecutionOwnership,
    ) -> bool:
        return bool(
            ownership.tenant_id == binding.tenant_id
            and ownership.workflow_id == binding.workflow_id
            and ownership.run_id == binding.run_id
            and ownership.step_id == binding.step_id
            and ownership.status == "active"
            and ownership.fencing_token == binding.fencing_token
            and ownership.lease_expires_at > float(self._clock())
        )

    def _get(self, binding: LangGraphCheckpointBinding, raw: Mapping[str, Any]) -> dict[str, Any]:
        config = assert_langgraph_config_binding(raw.get("config"), task_id=binding.task_id)
        checkpoint_id = _config_checkpoint_id(config)
        history = self._history(binding)
        checkpoint = (
            self._by_graph_checkpoint_id(history, checkpoint_id)
            if checkpoint_id
            else _latest_graph_checkpoint(history)
        )
        head_revision = history[-1].revision if history else 0
        return {
            "snapshot": (
                self._snapshot(
                    binding,
                    checkpoint,
                    head_revision=head_revision,
                ).to_dict()
                if checkpoint
                else None
            )
        }

    def _list(self, binding: LangGraphCheckpointBinding, raw: Mapping[str, Any]) -> dict[str, Any]:
        config = assert_langgraph_config_binding(raw.get("config"), task_id=binding.task_id)
        try:
            limit = int(raw.get("limit") or MAX_LANGGRAPH_CHECKPOINT_HISTORY)
        except (TypeError, ValueError) as exc:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_limit_invalid") from exc
        if limit < 1 or limit > MAX_LANGGRAPH_CHECKPOINT_HISTORY:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_limit_invalid")
        metadata_filter = assert_json_mapping(
            raw.get("metadata_filter") or {}, reason_code="langgraph_checkpoint_filter_invalid"
        )
        before_config = raw.get("before_config")
        before_id = ""
        if before_config is not None:
            before_id = _config_checkpoint_id(assert_langgraph_config_binding(before_config, task_id=binding.task_id))

        history = self._history(binding)
        head_revision = history[-1].revision if history else 0
        latest_by_id: dict[str, SignedCheckpoint] = {}
        for checkpoint in history:
            graph_checkpoint_id = _graph_checkpoint_id(checkpoint)
            if graph_checkpoint_id:
                latest_by_id[graph_checkpoint_id] = checkpoint
        ordered = sorted(
            latest_by_id.values(),
            key=lambda checkpoint: (
                _graph_checkpoint_order(checkpoint),
                checkpoint.revision,
            ),
            reverse=True,
        )
        values: list[LangGraphCheckpointSnapshot] = []
        seen_before = not before_id
        for checkpoint in ordered:
            snapshot = self._snapshot(
                binding,
                checkpoint,
                head_revision=head_revision,
            )
            graph_checkpoint_id = str(snapshot.checkpoint.get("id") or "")
            if not seen_before:
                if graph_checkpoint_id == before_id:
                    seen_before = True
                continue
            if any(snapshot.metadata.get(key) != value for key, value in metadata_filter.items()):
                continue
            if _config_namespace(snapshot.config) != _config_namespace(config):
                continue
            values.append(snapshot)
            if len(values) >= limit:
                break
        return {"snapshots": [value.to_dict() for value in values]}

    def _put(self, binding: LangGraphCheckpointBinding, raw: Mapping[str, Any]) -> dict[str, Any]:
        checkpoint = assert_json_mapping(raw.get("checkpoint"), reason_code="langgraph_checkpoint_payload_invalid")
        if not checkpoint:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_payload_invalid")
        metadata = assert_json_mapping(raw.get("metadata") or {}, reason_code="langgraph_checkpoint_metadata_invalid")
        incoming_config = assert_langgraph_config_binding(raw.get("config"), task_id=binding.task_id)
        graph_checkpoint_id = str(checkpoint.get("id") or "").strip()
        if not graph_checkpoint_id or len(graph_checkpoint_id) > 256:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_id_invalid")
        expected_revision = _expected_revision(raw)

        history = self._history(binding)
        current_revision = history[-1].revision if history else 0
        duplicate = self._by_graph_checkpoint_id(history, graph_checkpoint_id)
        if duplicate is not None:
            snapshot = self._snapshot(
                binding,
                duplicate,
                head_revision=current_revision,
            )
            expected = (checkpoint, metadata, _config_namespace(incoming_config))
            actual = (dict(snapshot.checkpoint), dict(snapshot.metadata), _config_namespace(snapshot.config))
            if canonical_json(actual) != canonical_json(expected):
                self._deny("langgraph_checkpoint_id_payload_conflict", 409)
            return {"snapshot": snapshot.to_dict()}

        if expected_revision != current_revision:
            self._deny(
                f"checkpoint_revision_conflict:expected={expected_revision}:actual={current_revision}",
                409,
            )
        latest_graph = _latest_graph_checkpoint(history)
        parent_config = (
            self._snapshot(
                binding,
                latest_graph,
                head_revision=current_revision,
            ).config
            if latest_graph is not None
            else None
        )
        checkpoint_order = max(
            (_graph_checkpoint_order(value) for value in history),
            default=0,
        ) + 1
        stored_config = _checkpoint_config(incoming_config, graph_checkpoint_id, current_revision + 1)
        state = WorkflowState(
            business_data={
                _STATE_KEY: {
                    "checkpoint": checkpoint,
                    "metadata": metadata,
                    "pending_writes": [],
                    "config": stored_config,
                    "parent_config": dict(parent_config) if parent_config is not None else None,
                }
            },
            runtime_metadata={
                "runtime_id": LANGGRAPH_CHECKPOINT_RUNTIME_ID,
                "step_id": binding.step_id,
                "langgraph_checkpoint_order": checkpoint_order,
            },
        )
        signed = SignedCheckpoint.issue(
            key_ring=self._key_ring,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            task_id=binding.task_id,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            runtime_id=LANGGRAPH_CHECKPOINT_RUNTIME_ID,
            runtime_version=LANGGRAPH_CHECKPOINT_RUNTIME_VERSION,
            state=state,
            revision=current_revision + 1,
            fencing_token=binding.fencing_token,
            now=float(self._clock()),
        )
        stored = self._checkpoints.save(signed, expected_revision=current_revision)
        return {
            "snapshot": self._snapshot(
                binding,
                stored,
                head_revision=stored.revision,
            ).to_dict()
        }

    def _put_writes(self, binding: LangGraphCheckpointBinding, raw: Mapping[str, Any]) -> dict[str, Any]:
        config = assert_langgraph_config_binding(raw.get("config"), task_id=binding.task_id)
        writes = normalize_pending_writes(raw.get("pending_writes"))
        if not writes:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_writes_invalid")
        expected_revision = _expected_revision(raw)
        history = self._history(binding)
        if not history:
            self._deny("langgraph_checkpoint_not_found", 404)
        head_revision = history[-1].revision
        if head_revision != expected_revision:
            self._deny(
                f"checkpoint_revision_conflict:expected={expected_revision}:actual={head_revision}",
                409,
            )
        requested_id = _config_checkpoint_id(config)
        target = (
            self._by_graph_checkpoint_id(history, requested_id)
            if requested_id
            else _latest_graph_checkpoint(history)
        )
        if target is None:
            self._deny("langgraph_checkpoint_not_found", 404)
        snapshot = self._snapshot(
            binding,
            target,
            head_revision=head_revision,
        )
        current_id = str(snapshot.checkpoint.get("id") or "")
        if all(value in snapshot.pending_writes for value in writes):
            return {"snapshot": snapshot.to_dict()}

        combined = tuple(snapshot.pending_writes) + tuple(
            value for value in writes if value not in snapshot.pending_writes
        )
        state = WorkflowState(
            business_data={
                _STATE_KEY: {
                    "checkpoint": dict(snapshot.checkpoint),
                    "metadata": dict(snapshot.metadata),
                    "pending_writes": [list(value) for value in combined],
                    "config": _checkpoint_config(snapshot.config, current_id, head_revision + 1),
                    "parent_config": (dict(snapshot.parent_config) if snapshot.parent_config is not None else None),
                }
            },
            runtime_metadata={
                "runtime_id": LANGGRAPH_CHECKPOINT_RUNTIME_ID,
                "step_id": binding.step_id,
                "langgraph_checkpoint_order": _graph_checkpoint_order(target),
            },
        )
        signed = SignedCheckpoint.issue(
            key_ring=self._key_ring,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            task_id=binding.task_id,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            runtime_id=LANGGRAPH_CHECKPOINT_RUNTIME_ID,
            runtime_version=LANGGRAPH_CHECKPOINT_RUNTIME_VERSION,
            state=state,
            revision=head_revision + 1,
            fencing_token=binding.fencing_token,
            now=float(self._clock()),
        )
        stored = self._checkpoints.save(signed, expected_revision=head_revision)
        return {
            "snapshot": self._snapshot(
                binding,
                stored,
                head_revision=stored.revision,
            ).to_dict()
        }

    def _history(self, binding: LangGraphCheckpointBinding) -> tuple[SignedCheckpoint, ...]:
        values = self._checkpoints.list_history(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            task_id=binding.task_id,
        )
        for checkpoint in values:
            self._verify_checkpoint(binding, checkpoint)
        return values

    def _by_graph_checkpoint_id(
        self,
        history: tuple[SignedCheckpoint, ...],
        checkpoint_id: str,
    ) -> SignedCheckpoint | None:
        for value in reversed(history):
            raw = value.state.business_data.get(_STATE_KEY)
            if isinstance(raw, Mapping) and isinstance(raw.get("checkpoint"), Mapping):
                if str(raw["checkpoint"].get("id") or "") == checkpoint_id:
                    return value
        return None

    def _verify_checkpoint(self, binding: LangGraphCheckpointBinding, checkpoint: SignedCheckpoint) -> None:
        checkpoint.verify(
            key_ring=self._key_ring,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            task_id=binding.task_id,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
        )
        if (
            checkpoint.runtime_id != LANGGRAPH_CHECKPOINT_RUNTIME_ID
            or checkpoint.runtime_version != LANGGRAPH_CHECKPOINT_RUNTIME_VERSION
        ):
            self._deny("langgraph_checkpoint_cross_runtime_rejected", 409)
        if checkpoint.state.runtime_metadata.get("step_id") != binding.step_id:
            self._deny("langgraph_checkpoint_step_binding_mismatch", 409)

    def _snapshot(
        self,
        binding: LangGraphCheckpointBinding,
        checkpoint: SignedCheckpoint,
        *,
        head_revision: int,
    ) -> LangGraphCheckpointSnapshot:
        self._verify_checkpoint(binding, checkpoint)
        raw = checkpoint.state.business_data.get(_STATE_KEY)
        if not isinstance(raw, Mapping):
            self._deny("langgraph_checkpoint_state_invalid", 409)
        assert isinstance(raw, Mapping)
        try:
            snapshot = LangGraphCheckpointSnapshot(
                checkpoint=assert_json_mapping(raw.get("checkpoint"), reason_code="langgraph_checkpoint_state_invalid"),
                metadata=assert_json_mapping(
                    raw.get("metadata") or {}, reason_code="langgraph_checkpoint_state_invalid"
                ),
                pending_writes=normalize_pending_writes(raw.get("pending_writes")),
                config=assert_langgraph_config_binding(raw.get("config"), task_id=binding.task_id),
                parent_config=(
                    assert_langgraph_config_binding(raw.get("parent_config"), task_id=binding.task_id)
                    if raw.get("parent_config") is not None
                    else None
                ),
                revision=checkpoint.revision,
                head_revision=int(head_revision),
                signed_checkpoint_ref=checkpoint.checkpoint_id,
            )
            return LangGraphCheckpointSnapshot.from_mapping(snapshot.to_dict())
        except LangGraphCheckpointContractError as exc:
            raise LangGraphCheckpointGatewayError(exc.reason_code, status_code=409) from exc

    @staticmethod
    def _deny(reason_code: str, status_code: int) -> None:
        raise LangGraphCheckpointGatewayError(reason_code, status_code=status_code)


def _expected_revision(raw: Mapping[str, Any]) -> int:
    try:
        raw_value = raw.get("expected_revision")
        if raw_value is None:
            raise ValueError
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_revision_invalid") from exc
    if value < 0:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_revision_invalid")
    return value


def _control_state(state: WorkflowState) -> dict[str, Any]:
    value = state.runtime_metadata.get("workflow_control")
    return dict(value) if isinstance(value, Mapping) else {}


def _config_checkpoint_id(config: Mapping[str, Any]) -> str:
    configurable = config.get("configurable")
    return str(configurable.get("checkpoint_id") or "") if isinstance(configurable, Mapping) else ""


def _config_namespace(config: Mapping[str, Any]) -> str:
    configurable = config.get("configurable")
    return str(configurable.get("checkpoint_ns") or "") if isinstance(configurable, Mapping) else ""


def _checkpoint_config(config: Mapping[str, Any], checkpoint_id: str, revision: int) -> dict[str, Any]:
    value = dict(config)
    configurable = dict(value.get("configurable") or {})
    configurable.update(
        {
            "checkpoint_id": str(checkpoint_id),
            "ananta_checkpoint_revision": int(revision),
        }
    )
    value["configurable"] = configurable
    return value


def _graph_checkpoint_id(checkpoint: SignedCheckpoint) -> str:
    raw = checkpoint.state.business_data.get(_STATE_KEY)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("checkpoint"), Mapping):
        return ""
    return str(raw["checkpoint"].get("id") or "")


def _graph_checkpoint_order(checkpoint: SignedCheckpoint) -> int:
    try:
        return int(
            checkpoint.state.runtime_metadata.get("langgraph_checkpoint_order")
            or checkpoint.revision
        )
    except (TypeError, ValueError):
        return int(checkpoint.revision)


def _latest_graph_checkpoint(
    history: tuple[SignedCheckpoint, ...],
) -> SignedCheckpoint | None:
    if not history:
        return None
    return max(
        history,
        key=lambda checkpoint: (
            _graph_checkpoint_order(checkpoint),
            checkpoint.revision,
        ),
    )


__all__ = [
    "CheckpointHistoryStore",
    "LangGraphCheckpointGatewayError",
    "LangGraphCheckpointGatewayService",
    "OwnershipReadPort",
]

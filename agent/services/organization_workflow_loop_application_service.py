"""Hub application boundary for persistent bounded workflow loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping

from agent.services.organization_workflow_loop_service import (
    OrganizationLoopPolicy,
    OrganizationLoopState,
    OrganizationWorkflowLoopService,
    OrganizationWorkflowLoopStorePort,
)


class OrganizationWorkflowLoopApplicationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class CreateOrganizationLoopCommand:
    loop_instance_id: str
    organization_id: str
    workflow_id: str | None
    task_id: str | None
    unit_id: str | None
    team_id: str | None
    definition_revision: str
    snapshot_hash: str
    policy: OrganizationLoopPolicy
    phase_capabilities: Mapping[str, frozenset[str]]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class TransitionOrganizationLoopCommand:
    loop_instance_id: str
    expected_revision: int
    artifact_version: str
    incremental_cost: str
    exit_condition_satisfied: bool
    idempotency_key: str


class OrganizationWorkflowLoopApplicationService:
    """Coordinates domain decisions and one CAS write owned by the Hub."""

    def __init__(
        self,
        *,
        organization_id: str,
        store: OrganizationWorkflowLoopStorePort,
        domain: OrganizationWorkflowLoopService | None = None,
    ) -> None:
        self._organization_id = organization_id
        self._store = store
        self._domain = domain or OrganizationWorkflowLoopService()

    def create(self, command: CreateOrganizationLoopCommand) -> dict[str, object]:
        if command.organization_id != self._organization_id:
            raise OrganizationWorkflowLoopApplicationError("organization_loop_scope_mismatch")
        if any(
            not str(value or "").strip()
            for value in (
                command.loop_instance_id,
                command.workflow_id,
                command.definition_revision,
                command.snapshot_hash,
                command.idempotency_key,
            )
        ):
            raise OrganizationWorkflowLoopApplicationError("organization_loop_binding_missing")
        timestamp = _now_iso()
        request_digest = _digest(
            {
                "loop_instance_id": command.loop_instance_id,
                "organization_id": command.organization_id,
                "workflow_id": command.workflow_id,
                "task_id": command.task_id,
                "unit_id": command.unit_id,
                "team_id": command.team_id,
                "definition_revision": command.definition_revision,
                "snapshot_hash": command.snapshot_hash,
                "policy": asdict(command.policy),
            }
        )
        existing = self._store.get(command.loop_instance_id)
        if existing is not None:
            if (
                existing.get("last_idempotency_key") != command.idempotency_key
                or existing.get("last_request_digest") != request_digest
            ):
                raise OrganizationWorkflowLoopApplicationError("organization_loop_idempotency_conflict")
            return {**existing, "replayed": True}
        issues = self._domain.validate_policy(
            command.policy,
            phase_capabilities=command.phase_capabilities,
        )
        if issues:
            raise OrganizationWorkflowLoopApplicationError(issues[0])
        inserted, state = self._store.create_once(
            {
                "loop_instance_id": command.loop_instance_id,
                "loop_id": command.policy.loop_id,
                "workflow_id": command.workflow_id,
                "task_id": command.task_id,
                "unit_id": command.unit_id,
                "team_id": command.team_id,
                "definition_revision": command.definition_revision,
                "snapshot_hash": command.snapshot_hash,
                "policy": asdict(command.policy),
                "iteration": 0,
                "status": "running",
                "started_at": timestamp,
                "updated_at": timestamp,
                "accumulated_cost": "0",
                "artifact_versions": [],
                "selected_transition": None,
                "reason_code": "loop_started",
                "last_idempotency_key": command.idempotency_key,
                "last_request_digest": request_digest,
            }
        )
        if not inserted and (
            state.get("last_idempotency_key") != command.idempotency_key
            or state.get("last_request_digest") != request_digest
        ):
            raise OrganizationWorkflowLoopApplicationError("organization_loop_idempotency_conflict")
        return {**state, "replayed": not inserted}

    def transition(
        self,
        command: TransitionOrganizationLoopCommand,
    ) -> dict[str, object]:
        if (
            not command.loop_instance_id
            or not command.artifact_version
            or not command.idempotency_key
            or command.expected_revision < 1
        ):
            raise OrganizationWorkflowLoopApplicationError("organization_loop_transition_binding_invalid")
        current = self._store.get(command.loop_instance_id)
        if current is None:
            raise OrganizationWorkflowLoopApplicationError("organization_loop_not_found")
        request_digest = _digest(
            {
                "loop_instance_id": command.loop_instance_id,
                "expected_revision": command.expected_revision,
                "artifact_version": command.artifact_version,
                "incremental_cost": command.incremental_cost,
                "exit_condition_satisfied": command.exit_condition_satisfied,
            }
        )
        if current.get("last_idempotency_key") == command.idempotency_key:
            if current.get("last_request_digest") != request_digest:
                raise OrganizationWorkflowLoopApplicationError("organization_loop_idempotency_conflict")
            return {**current, "replayed": True}
        if int(current.get("revision") or 0) != command.expected_revision:
            raise OrganizationWorkflowLoopApplicationError("organization_loop_revision_stale")
        if str(current.get("status") or "") in {
            "completed",
            "blocked",
            "escalated",
            "cancelled",
        }:
            raise OrganizationWorkflowLoopApplicationError("organization_loop_already_terminal")
        policy = OrganizationLoopPolicy(**dict(current.get("policy") or {}))
        state = OrganizationLoopState(
            loop_id=str(current.get("loop_id") or ""),
            iteration=int(current.get("iteration") or 0),
            status=str(current.get("status") or "running"),
            started_at=str(current.get("started_at") or ""),
            updated_at=str(current.get("updated_at") or ""),
            accumulated_cost=str(current.get("accumulated_cost") or "0"),
            artifact_versions=tuple(current.get("artifact_versions") or ()),
            selected_transition=(str(current.get("selected_transition") or "") or None),
        )
        decision = self._domain.request_rework(
            policy=policy,
            state=state,
            artifact_version=command.artifact_version,
            incremental_cost=_normalized_cost(command.incremental_cost),
            exit_condition_satisfied=command.exit_condition_satisfied,
            timed_out=_timed_out(state.started_at, policy.timeout_seconds),
        )
        next_value = {
            **current,
            **asdict(decision.state),
            "reason_code": decision.reason_code,
            "last_idempotency_key": command.idempotency_key,
            "last_request_digest": request_digest,
        }
        if not self._store.save_if_revision(
            loop_instance_id=command.loop_instance_id,
            expected_revision=command.expected_revision,
            value=next_value,
        ):
            raced = self._store.get(command.loop_instance_id)
            if (
                raced is not None
                and raced.get("last_idempotency_key") == command.idempotency_key
                and raced.get("last_request_digest") == request_digest
            ):
                return {**raced, "replayed": True}
            raise OrganizationWorkflowLoopApplicationError("organization_loop_revision_race")
        stored = self._store.get(command.loop_instance_id)
        if stored is None:
            raise OrganizationWorkflowLoopApplicationError("organization_loop_persistence_failed")
        return {
            **stored,
            "creates_dependency_edge": decision.creates_dependency_edge,
            "replayed": False,
        }


def _normalized_cost(value: str) -> str:
    try:
        result = Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise OrganizationWorkflowLoopApplicationError("loop_cost_invalid") from exc
    if not result.is_finite() or result < 0:
        raise OrganizationWorkflowLoopApplicationError("loop_cost_invalid")
    return str(result)


def _timed_out(started_at: str, timeout_seconds: int) -> bool:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) >= started + timedelta(seconds=timeout_seconds)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


__all__ = [
    "CreateOrganizationLoopCommand",
    "OrganizationWorkflowLoopApplicationError",
    "OrganizationWorkflowLoopApplicationService",
    "TransitionOrganizationLoopCommand",
]

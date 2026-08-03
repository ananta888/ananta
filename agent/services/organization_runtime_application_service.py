"""Hub composition boundary for persistent Organization runtime services."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Protocol

from sqlmodel import Session, select

from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationInstanceDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB
from agent.models.organization_models import canonical_definition_sha256
from agent.repositories.organization_runtime import (
    SqlArtifactVersionReader,
    SqlAssignmentEvidenceVerifier,
    SqlHandoffStateStore,
    SqlOrganizationBudgetLedger,
    SqlOrganizationEventStore,
    SqlOrganizationWorkflowLoopStore,
)
from agent.repositories.organizations.definitions import (
    SqlOrganizationDefinitionRepository,
)
from agent.services.organization_budget_service import (
    OrganizationBudgetDecision,
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
    OrganizationBudgetService,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
    get_organization_definition_catalog,
)
from agent.services.organization_event_service import OrganizationEventService
from agent.services.organization_workflow_loop_application_service import (
    CreateOrganizationLoopCommand,
    OrganizationWorkflowLoopApplicationService,
    TransitionOrganizationLoopCommand,
)
from agent.services.separation_of_duties_service import DutyAssignment
from agent.services.team_handoff_service import (
    TeamHandoffAcceptanceCheck,
    TeamHandoffContract,
    TeamHandoffDecision,
    TeamHandoffService,
)


class OrganizationDispatchBudgetPort(Protocol):
    """Hub dispatch seam; policy resolution remains outside the ledger."""

    def reserve_before_dispatch(
        self,
        *,
        request: OrganizationBudgetRequest,
        authoritative_limits: Iterable[OrganizationBudgetLimit],
    ) -> OrganizationBudgetDecision: ...

    def settle_after_dispatch(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal | str,
        actual_wall_seconds: int,
    ) -> bool: ...


class OrganizationRuntimeApplicationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationRuntimeApplicationService:
    """Scope-bound facade used by routes and Hub orchestration only."""

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        catalog: OrganizationDefinitionCatalogService | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.organization_id = organization_id
        self._catalog = catalog or get_organization_definition_catalog()
        self._budget_ledger = SqlOrganizationBudgetLedger(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        self._event_store = SqlOrganizationEventStore(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        self.handoff_store = SqlHandoffStateStore(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        self.loop_store = SqlOrganizationWorkflowLoopStore(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )

    def reserve_before_dispatch(
        self,
        *,
        request: OrganizationBudgetRequest,
        authoritative_limits: Iterable[OrganizationBudgetLimit],
    ) -> OrganizationBudgetDecision:
        # The caller must be the Hub policy resolver.  No HTTP surface accepts
        # limits, which prevents a Worker or stewardship role from increasing
        # its own allowance.
        decision = OrganizationBudgetService(ledger=self._budget_ledger).reserve(
            request=request,
            limits=authoritative_limits,
        )
        self.emit_event(
            event_type=("budget_reserved" if decision.allowed else "budget_exhausted"),
            correlation_id=request.task_id,
            idempotency_key=f"budget-reservation:{request.reservation_id}",
            payload={
                "reservation_id": request.reservation_id,
                "task_id": request.task_id,
                "team_id": request.team_id,
                "workflow_id": request.workflow_id,
                "tokens": request.tokens,
                "cost": str(request.cost),
                "wall_seconds": request.wall_seconds,
                "parallel_slots": request.parallel_slots,
                "reason_code": decision.reason_code,
                "exceeded_scopes": list(decision.exceeded_scopes),
            },
        )
        return decision

    def settle_after_dispatch(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal | str,
        actual_wall_seconds: int,
    ) -> bool:
        settled = OrganizationBudgetService(ledger=self._budget_ledger).settle(
            reservation_id=reservation_id,
            actual_tokens=actual_tokens,
            actual_cost=actual_cost,
            actual_wall_seconds=actual_wall_seconds,
        )
        if settled:
            self.emit_event(
                event_type="budget_settled",
                correlation_id=reservation_id,
                idempotency_key=f"budget-settlement:{reservation_id}",
                payload={
                    "reservation_id": reservation_id,
                    "actual_tokens": actual_tokens,
                    "actual_cost": str(actual_cost),
                    "actual_wall_seconds": actual_wall_seconds,
                },
            )
        return settled

    def event_service(self) -> OrganizationEventService:
        return OrganizationEventService(store=self._event_store)

    def handoff_service(self) -> TeamHandoffService:
        return TeamHandoffService(
            artifacts=SqlArtifactVersionReader(),
            evidence=SqlAssignmentEvidenceVerifier(),
            store=self.handoff_store,
        )

    def submit_handoff(
        self,
        *,
        contract: TeamHandoffContract,
        assignment_id: str,
        dispatch_lease_id: str,
        idempotency_key: str,
    ) -> TeamHandoffDecision:
        # Exact retries must be able to replay a committed handoff even when
        # the dispatch lease has since completed.  Only a first insert needs
        # the live topology/lease checks below.
        if self.handoff_store.get(contract.handoff_id) is None:
            contract = self.validate_handoff_contract(
                contract,
                assignment_id=assignment_id,
                dispatch_lease_id=dispatch_lease_id,
            )
        return self.handoff_service().submit(
            contract=contract,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            idempotency_key=idempotency_key,
        )

    def validate_handoff_contract(
        self,
        contract: TeamHandoffContract,
        *,
        assignment_id: str,
        dispatch_lease_id: str,
    ) -> TeamHandoffContract:
        """Validate every runtime endpoint against the scoped topology/task truth."""

        if contract.organization_id != self.organization_id:
            raise OrganizationRuntimeApplicationError("handoff_organization_scope_mismatch")
        bound_contract = contract
        with Session(_engine()) as session:
            producer = self._task(session, contract.producer_task_id)
            consumer = self._task(session, contract.consumer_task_id)
            if producer is None or consumer is None:
                raise OrganizationRuntimeApplicationError("handoff_task_binding_invalid")
            if producer.id == consumer.id:
                raise OrganizationRuntimeApplicationError("handoff_task_endpoints_equal")
            expected_bindings = (
                (
                    producer,
                    contract.producer_unit_id,
                    contract.producer_team_id,
                    contract.producer_role_slot_id,
                ),
                (
                    consumer,
                    contract.consumer_unit_id,
                    contract.consumer_team_id,
                    contract.consumer_role_slot_id,
                ),
            )
            if any(
                task.unit_id != unit_id or task.team_id != team_id or task.role_slot_id != role_slot_id
                for task, unit_id, team_id, role_slot_id in expected_bindings
            ):
                raise OrganizationRuntimeApplicationError("handoff_task_topology_binding_invalid")
            if str(producer.goal_id or "") != contract.goal_id or str(consumer.goal_id or "") != contract.goal_id:
                raise OrganizationRuntimeApplicationError("handoff_goal_binding_invalid")
            job = session.get(WorkerJobDB, dispatch_lease_id)
            slot_lease = (
                session.get(WorkerSlotLeaseDB, str(job.slot_lease_id or ""))
                if job is not None and job.slot_lease_id
                else None
            )
            if (
                job is None
                or str(job.subtask_id or "") != assignment_id
                or str(job.parent_task_id or "") != contract.producer_task_id
                or str(producer.current_worker_job_id or "") != dispatch_lease_id
                or str(job.status or "") not in {"delegated", "running"}
                or job.finished_at is not None
                or (job.slot_lease_id and slot_lease is None)
                or (
                    slot_lease is not None
                    and (
                        slot_lease.status != "active"
                        or float(slot_lease.deadline_at) <= time.time()
                        or slot_lease.released_at is not None
                        or str(slot_lease.parent_task_id or "") not in {"", contract.producer_task_id}
                        or str(slot_lease.worker_job_id or "") not in {"", dispatch_lease_id}
                    )
                )
            ):
                raise OrganizationRuntimeApplicationError("handoff_dispatch_lease_binding_invalid")
            for _task, unit_id, team_id, role_slot_id in expected_bindings:
                unit = self._scoped_row(session, OrganizationUnitDB, unit_id)
                slot = self._scoped_row(
                    session,
                    OrganizationRoleSlotDB,
                    role_slot_id,
                )
                link = session.exec(
                    select(OrganizationTeamLinkDB)
                    .where(OrganizationTeamLinkDB.tenant_id == self.tenant_id)
                    .where(OrganizationTeamLinkDB.project_id == self.project_id)
                    .where(OrganizationTeamLinkDB.organization_id == self.organization_id)
                    .where(OrganizationTeamLinkDB.unit_id == unit_id)
                    .where(OrganizationTeamLinkDB.team_id == team_id)
                ).first()
                if (
                    unit is None
                    or unit.lifecycle not in {"active", "draining"}
                    or slot is None
                    or slot.lifecycle != "active"
                    or slot.unit_id != unit_id
                    or link is None
                    or link.lifecycle not in {"active", "draining"}
                ):
                    raise OrganizationRuntimeApplicationError("handoff_topology_binding_invalid")
            if contract.handoff_definition_ref:
                definition_key, raw_version = contract.handoff_definition_ref.rsplit("@", 1)
                if not raw_version.isdigit():
                    raise OrganizationRuntimeApplicationError("handoff_definition_ref_invalid")
                definition_version = int(raw_version)
                definitions = self._definition_repository(session)
                definition = definitions.get_handoff(
                    self.tenant_id,
                    self.project_id,
                    definition_key,
                    definition_version,
                )
                definition_payload = self._active_definition_payload(definition)
                scoped_units = session.exec(
                    select(OrganizationUnitDB)
                    .where(OrganizationUnitDB.tenant_id == self.tenant_id)
                    .where(OrganizationUnitDB.project_id == self.project_id)
                    .where(OrganizationUnitDB.organization_id == self.organization_id)
                ).all()
                parents = {row.id: row.parent_unit_id for row in scoped_units}
                producer_ancestors = _unit_ancestors(
                    contract.producer_unit_id,
                    parents,
                )
                consumer_ancestors = _unit_ancestors(
                    contract.consumer_unit_id,
                    parents,
                )
                relation = session.exec(
                    select(OrganizationRelationDB)
                    .where(OrganizationRelationDB.tenant_id == self.tenant_id)
                    .where(OrganizationRelationDB.project_id == self.project_id)
                    .where(OrganizationRelationDB.organization_id == self.organization_id)
                    .where(OrganizationRelationDB.source_unit_id.in_(producer_ancestors))
                    .where(OrganizationRelationDB.target_unit_id.in_(consumer_ancestors))
                    .where(OrganizationRelationDB.handoff_definition_key == definition_key)
                    .where(OrganizationRelationDB.handoff_definition_version == definition_version)
                    .where(OrganizationRelationDB.lifecycle == "active")
                ).first()
                artifact_kinds = {row.artifact_kind for row in contract.artifact_refs}
                required_artifact_kinds = {
                    str(value) for value in list(definition_payload.get("required_artifact_kinds") or [])
                }
                if not definition_payload or relation is None or not required_artifact_kinds.issubset(artifact_kinds):
                    raise OrganizationRuntimeApplicationError("handoff_definition_binding_invalid")
                gate_ref = str(
                    definition_payload.get("acceptance_gate_ref")
                    or getattr(definition, "acceptance_gate_ref", "")
                    or ""
                )
                if "@" not in gate_ref:
                    raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_ref_invalid")
                gate_key, gate_version_value = gate_ref.rsplit("@", 1)
                if not gate_version_value.isdigit() or int(gate_version_value) < 1:
                    raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_ref_invalid")
                gate = definitions.get_policy(
                    self.tenant_id,
                    self.project_id,
                    gate_key,
                    int(gate_version_value),
                )
                gate_definition = self._active_definition_payload(gate)
                gate_artifacts = {str(value) for value in list(gate_definition.get("required_artifact_kinds") or [])}
                allowed_decisions = tuple(
                    sorted(
                        {
                            str(value).strip().lower()
                            for value in list(gate_definition.get("allowed_decisions") or [])
                            if str(value).strip()
                        }
                    )
                )
                if (
                    gate is None
                    or gate_definition.get("policy_type") != "acceptance_gate"
                    or not gate_artifacts.issubset(artifact_kinds)
                    or not allowed_decisions
                ):
                    raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_binding_invalid")
                authoritative_checks = (
                    tuple(
                        TeamHandoffAcceptanceCheck(
                            check_id=f"artifact-present:{kind}",
                            check_kind="artifact_present",
                            expected=kind,
                            status="pending",
                        )
                        for kind in sorted(required_artifact_kinds)
                    )
                    + tuple(
                        TeamHandoffAcceptanceCheck(
                            check_id=(
                                "digest-matches:"
                                + canonical_definition_sha256(
                                    {
                                        "artifact_id": reference.artifact_id,
                                        "version": reference.version,
                                    }
                                )[:16]
                            ),
                            check_kind="digest_matches",
                            expected=reference.digest,
                            status="pending",
                        )
                        for reference in sorted(
                            contract.artifact_refs,
                            key=lambda value: (value.artifact_id, value.version),
                        )
                    )
                    + (
                        TeamHandoffAcceptanceCheck(
                            check_id="evidence-verified",
                            check_kind="evidence_verified",
                            expected="hub_verified",
                            status="pending",
                        ),
                        TeamHandoffAcceptanceCheck(
                            check_id="policy-gate",
                            check_kind="policy_gate",
                            expected=gate_ref,
                            status="pending",
                        ),
                    )
                )
                bound_contract = replace(
                    contract,
                    acceptance_checks=authoritative_checks,
                    acceptance_gate_ref=gate_ref,
                    acceptance_gate_hash=str(gate.content_hash or ""),
                    acceptance_gate_allowed_decisions=allowed_decisions,
                    acceptance_gate_self_approval_allowed=bool(gate_definition.get("self_approval_allowed", False)),
                )
        return bound_contract

    def validate_handoff_acceptance_binding(self, *, handoff_id: str) -> None:
        """Re-read the immutable gate binding before a positive decision."""

        state = self.handoff_store.get(handoff_id)
        contract = dict((state or {}).get("contract") or {})
        definition_ref = str(contract.get("handoff_definition_ref") or "")
        gate_ref = str(contract.get("acceptance_gate_ref") or "")
        gate_hash = str(contract.get("acceptance_gate_hash") or "")
        if not definition_ref or not gate_ref or not gate_hash:
            raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_binding_missing")
        try:
            definition_key, definition_version_value = definition_ref.rsplit("@", 1)
            gate_key, gate_version_value = gate_ref.rsplit("@", 1)
            definition_version = int(definition_version_value)
            gate_version = int(gate_version_value)
        except (TypeError, ValueError) as exc:
            raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_binding_invalid") from exc
        with Session(_engine()) as session:
            definitions = self._definition_repository(session)
            definition = definitions.get_handoff(
                self.tenant_id,
                self.project_id,
                definition_key,
                definition_version,
            )
            gate = definitions.get_policy(
                self.tenant_id,
                self.project_id,
                gate_key,
                gate_version,
            )
            definition_payload = self._active_definition_payload(definition)
            gate_definition = self._active_definition_payload(gate)
        current_decisions = sorted(
            {
                str(value).strip().lower()
                for value in list(gate_definition.get("allowed_decisions") or [])
                if str(value).strip()
            }
        )
        if (
            not definition_payload
            or not gate_definition
            or str(
                definition_payload.get("acceptance_gate_ref") or getattr(definition, "acceptance_gate_ref", "") or ""
            )
            != gate_ref
            or str(gate.content_hash or "") != gate_hash
            or gate_definition.get("policy_type") != "acceptance_gate"
            or current_decisions != sorted(contract.get("acceptance_gate_allowed_decisions") or [])
            or bool(gate_definition.get("self_approval_allowed", False))
            != bool(contract.get("acceptance_gate_self_approval_allowed", False))
        ):
            raise OrganizationRuntimeApplicationError("handoff_acceptance_gate_binding_stale")

    def _definition_repository(self, session: Session):
        """Resolve tenant overrides first and production-file definitions second."""

        return FileCatalogDefinitionRepositoryAdapter(
            SqlOrganizationDefinitionRepository(session),
            self._catalog,
            session,
        )

    @staticmethod
    def _active_definition_payload(row) -> dict:
        """Fail closed for inactive or content-tampered definition revisions."""

        if row is None or str(getattr(row, "lifecycle", "")) != "active":
            return {}
        payload = dict(getattr(row, "definition_json", None) or {})
        expected_hash = str(getattr(row, "content_hash", "") or "")
        if not payload or not expected_hash:
            return {}
        if canonical_definition_sha256(payload) != expected_hash:
            raise OrganizationRuntimeApplicationError("organization_referenced_definition_hash_mismatch")
        return payload

    def handoff_decision_assignments(
        self,
        *,
        handoff_id: str,
        decision_assignment_id: str,
        actor_principal_id: str,
    ) -> tuple[DutyAssignment, ...]:
        handoff = self.handoff_store.get(handoff_id)
        contract = dict((handoff or {}).get("contract") or {})
        if not contract:
            raise OrganizationRuntimeApplicationError("handoff_not_found")
        consumer_slot_id = str(contract.get("consumer_role_slot_id") or "")
        producer_slot_id = str(contract.get("producer_role_slot_id") or "")
        with Session(_engine()) as session:
            decision_assignment = self._scoped_row(
                session,
                OrganizationRoleAssignmentDB,
                decision_assignment_id,
            )
            if (
                decision_assignment is None
                or decision_assignment.lifecycle != "active"
                or decision_assignment.role_slot_id != consumer_slot_id
                or str(dict(decision_assignment.assignment_metadata or {}).get("principal_id") or "")
                != actor_principal_id
            ):
                raise OrganizationRuntimeApplicationError("handoff_consumer_assignment_invalid")
            rows = session.exec(
                select(OrganizationRoleAssignmentDB)
                .where(OrganizationRoleAssignmentDB.tenant_id == self.tenant_id)
                .where(OrganizationRoleAssignmentDB.project_id == self.project_id)
                .where(OrganizationRoleAssignmentDB.organization_id == self.organization_id)
                .where(OrganizationRoleAssignmentDB.lifecycle == "active")
            ).all()
            if any(
                row.role_slot_id == producer_slot_id
                and str(dict(row.assignment_metadata or {}).get("principal_id") or "") == actor_principal_id
                for row in rows
            ):
                raise OrganizationRuntimeApplicationError("sod_principal_collision")
            slots = {
                row.id: row
                for row in session.exec(
                    select(OrganizationRoleSlotDB)
                    .where(OrganizationRoleSlotDB.tenant_id == self.tenant_id)
                    .where(OrganizationRoleSlotDB.project_id == self.project_id)
                    .where(OrganizationRoleSlotDB.organization_id == self.organization_id)
                ).all()
            }
            team_by_unit = {
                row.unit_id: row.team_id
                for row in session.exec(
                    select(OrganizationTeamLinkDB)
                    .where(OrganizationTeamLinkDB.tenant_id == self.tenant_id)
                    .where(OrganizationTeamLinkDB.project_id == self.project_id)
                    .where(OrganizationTeamLinkDB.organization_id == self.organization_id)
                ).all()
            }
        assignments: list[DutyAssignment] = []
        for row in rows:
            metadata = dict(row.assignment_metadata or {})
            principal_id = str(metadata.get("principal_id") or "")
            slot = slots.get(row.role_slot_id)
            if not principal_id or slot is None:
                continue
            assignments.append(
                DutyAssignment(
                    principal_id=principal_id,
                    role_slot_id=row.role_slot_id,
                    team_id=str(team_by_unit.get(slot.unit_id) or ""),
                    duties=frozenset(str(value) for value in list(metadata.get("duties") or []) if str(value)),
                )
            )
        return tuple(assignments)

    def workflow_loop_service(self) -> OrganizationWorkflowLoopApplicationService:
        return OrganizationWorkflowLoopApplicationService(
            organization_id=self.organization_id,
            store=self.loop_store,
        )

    def create_workflow_loop(
        self,
        command: CreateOrganizationLoopCommand,
    ) -> dict[str, object]:
        if self.loop_store.get(command.loop_instance_id) is None:
            organization, snapshot = self._organization_binding()
            if (
                command.definition_revision != organization.definition_revision
                or command.snapshot_hash != snapshot.snapshot_hash
            ):
                raise OrganizationRuntimeApplicationError("organization_loop_runtime_binding_stale")
            if command.task_id:
                with Session(_engine()) as session:
                    task = self._task(session, command.task_id)
                    if task is None:
                        raise OrganizationRuntimeApplicationError("organization_loop_task_binding_invalid")
                    if (command.unit_id and command.unit_id != task.unit_id) or (
                        command.team_id and command.team_id != task.team_id
                    ):
                        raise OrganizationRuntimeApplicationError("organization_loop_task_topology_binding_invalid")
        result = self.workflow_loop_service().create(command)
        self.emit_event(
            event_type="workflow_loop_started",
            correlation_id=command.workflow_id or command.loop_instance_id,
            idempotency_key=f"workflow-loop-create:{command.idempotency_key}",
            payload={
                "loop_instance_id": command.loop_instance_id,
                "workflow_id": command.workflow_id,
                "task_id": command.task_id,
            },
            definition_revision=command.definition_revision,
            snapshot_hash=command.snapshot_hash,
        )
        return result

    def transition_workflow_loop(
        self,
        command: TransitionOrganizationLoopCommand,
    ) -> dict[str, object]:
        result = self.workflow_loop_service().transition(command)
        status = str(result.get("status") or "")
        reason_code = str(result.get("reason_code") or "")
        if status == "completed":
            event_type = "workflow_loop_completed"
        elif status in {"blocked", "escalated"}:
            event_type = "workflow_loop_exhausted"
        else:
            event_type = "workflow_rework_requested"
        self.emit_event(
            event_type=event_type,
            correlation_id=str(result.get("workflow_id") or command.loop_instance_id),
            idempotency_key=f"workflow-loop-transition:{command.idempotency_key}",
            payload={
                "loop_instance_id": command.loop_instance_id,
                "workflow_id": result.get("workflow_id"),
                "task_id": result.get("task_id"),
                "artifact_version": command.artifact_version,
                "reason_code": reason_code,
            },
        )
        return result

    def emit_event(
        self,
        *,
        event_type: str,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, object],
        definition_revision: str | None = None,
        snapshot_hash: str | None = None,
    ) -> dict[str, object]:
        organization, snapshot = self._organization_binding()
        event = self.event_service().emit(
            event_type=event_type,
            organization_id=self.organization_id,
            definition_revision=(str(definition_revision or "").strip() or organization.definition_revision),
            snapshot_hash=str(snapshot_hash or "").strip() or snapshot.snapshot_hash,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        return asdict(event)

    def read_runtime(self, *, event_limit: int = 500) -> dict[str, object]:
        if not 1 <= event_limit <= 1_000:
            raise OrganizationRuntimeApplicationError("organization_runtime_event_limit_invalid")
        organization, snapshot = self._organization_binding()
        event_service = self.event_service()
        events = self._event_store.list_for_organization(self.organization_id)
        projection = event_service.runtime_projection(
            organization_id=self.organization_id,
            events=events,
        )
        with Session(_engine()) as session:
            units = session.exec(
                select(OrganizationUnitDB)
                .where(OrganizationUnitDB.tenant_id == self.tenant_id)
                .where(OrganizationUnitDB.project_id == self.project_id)
                .where(OrganizationUnitDB.organization_id == self.organization_id)
                .order_by(OrganizationUnitDB.id)
            ).all()
            team_links = session.exec(
                select(OrganizationTeamLinkDB)
                .where(OrganizationTeamLinkDB.tenant_id == self.tenant_id)
                .where(OrganizationTeamLinkDB.project_id == self.project_id)
                .where(OrganizationTeamLinkDB.organization_id == self.organization_id)
                .order_by(OrganizationTeamLinkDB.team_id)
            ).all()
            tasks = session.exec(
                select(TaskDB)
                .where(TaskDB.tenant_id == self.tenant_id)
                .where(TaskDB.project_id == self.project_id)
                .where(TaskDB.organization_id == self.organization_id)
                .order_by(TaskDB.id)
            ).all()
            dependencies = session.exec(
                select(CrossTeamTaskDependencyDB)
                .where(CrossTeamTaskDependencyDB.tenant_id == self.tenant_id)
                .where(CrossTeamTaskDependencyDB.project_id == self.project_id)
                .where(CrossTeamTaskDependencyDB.organization_id == self.organization_id)
                .order_by(CrossTeamTaskDependencyDB.id)
            ).all()
        projection["units"] = {
            unit.id: {
                "status": unit.lifecycle,
                "unit_kind": unit.unit_kind,
                "parent_unit_id": unit.parent_unit_id,
                "authoritative": True,
            }
            for unit in units
        }
        projection["teams"] = {
            link.team_id: {
                "status": link.lifecycle,
                "unit_id": link.unit_id,
                "authoritative": True,
            }
            for link in team_links
        }
        projection["status"] = organization.lifecycle
        projection["definition_revision"] = organization.definition_revision
        projection["snapshot_hash"] = snapshot.snapshot_hash
        projection["runtime_overlay_stale"] = bool(
            events
            and (
                events[-1].definition_revision != organization.definition_revision
                or events[-1].snapshot_hash != snapshot.snapshot_hash
            )
        )
        projection["tasks"] = {
            task.id: {
                "status": task.status,
                "unit_id": task.unit_id,
                "team_id": task.team_id,
                "role_slot_id": task.role_slot_id,
                "goal_id": task.goal_id,
                "authoritative": True,
            }
            for task in tasks
        }
        projection["dependencies"] = {
            row.id: {
                "status": row.status,
                "source_task_id": row.source_task_id,
                "target_task_id": row.target_task_id,
                "source_team_id": row.source_team_id,
                "target_team_id": row.target_team_id,
                "gate_ref": row.gate_ref,
                "authoritative": True,
            }
            for row in dependencies
        }
        budget_usage = self._budget_ledger.usage()
        loop_states = list(self.loop_store.list_states())
        workflows: dict[str, dict[str, object]] = {}
        for loop in loop_states:
            workflow_id = str(loop.get("workflow_id") or "")
            if not workflow_id:
                continue
            row = workflows.setdefault(
                workflow_id,
                {
                    "status": str(loop.get("status") or "unknown"),
                    "loop_instance_ids": [],
                    "authoritative": True,
                },
            )
            row["status"] = str(loop.get("status") or "unknown")
            loop_ids = row.get("loop_instance_ids")
            if isinstance(loop_ids, list):
                loop_ids.append(str(loop.get("loop_instance_id") or ""))
        projection["workflows"] = workflows
        return {
            "organization_id": self.organization_id,
            "definition_revision": organization.definition_revision,
            "snapshot_hash": snapshot.snapshot_hash,
            "projection": projection,
            "events": [asdict(event) for event in events[-event_limit:]],
            "event_window_truncated": len(events) > event_limit,
            "budget_usage": {
                key: {
                    "tokens": value.tokens,
                    "cost": str(value.cost),
                    "wall_seconds": value.wall_seconds,
                    "parallel_slots": value.parallel_slots,
                }
                for key, value in budget_usage.items()
            },
            "handoffs": list(self.handoff_store.list_states()),
            "workflow_loops": loop_states,
        }

    def _organization_binding(
        self,
    ) -> tuple[OrganizationInstanceDB, OrganizationTopologySnapshotDB]:
        with Session(_engine()) as session:
            organization = session.exec(
                select(OrganizationInstanceDB)
                .where(OrganizationInstanceDB.tenant_id == self.tenant_id)
                .where(OrganizationInstanceDB.project_id == self.project_id)
                .where(OrganizationInstanceDB.organization_id == self.organization_id)
            ).first()
            snapshot = session.exec(
                select(OrganizationTopologySnapshotDB)
                .where(OrganizationTopologySnapshotDB.tenant_id == self.tenant_id)
                .where(OrganizationTopologySnapshotDB.project_id == self.project_id)
                .where(OrganizationTopologySnapshotDB.organization_id == self.organization_id)
                .order_by(OrganizationTopologySnapshotDB.revision.desc())
                .limit(1)
            ).first()
            if organization is None:
                raise OrganizationRuntimeApplicationError("organization_runtime_organization_not_found")
            if snapshot is None:
                raise OrganizationRuntimeApplicationError("organization_runtime_snapshot_missing")
            # Detach the immutable fields used after the session closes.
            session.expunge(organization)
            session.expunge(snapshot)
            return organization, snapshot

    def _task(self, session: Session, task_id: str) -> TaskDB | None:
        return session.exec(
            select(TaskDB)
            .where(TaskDB.tenant_id == self.tenant_id)
            .where(TaskDB.project_id == self.project_id)
            .where(TaskDB.organization_id == self.organization_id)
            .where(TaskDB.id == task_id)
        ).first()

    def _scoped_row(self, session: Session, model, identifier: str):
        return session.exec(
            select(model)
            .where(model.tenant_id == self.tenant_id)
            .where(model.project_id == self.project_id)
            .where(model.organization_id == self.organization_id)
            .where(model.id == identifier)
        ).first()


def _engine():
    from agent.database import engine

    return engine


def _unit_ancestors(
    unit_id: str,
    parents: dict[str, str | None],
) -> tuple[str, ...]:
    ancestors: list[str] = []
    seen: set[str] = set()
    current: str | None = unit_id
    while current and current not in seen:
        seen.add(current)
        ancestors.append(current)
        current = parents.get(current)
    return tuple(ancestors)


__all__ = [
    "OrganizationDispatchBudgetPort",
    "OrganizationRuntimeApplicationError",
    "OrganizationRuntimeApplicationService",
]

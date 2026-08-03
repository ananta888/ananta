from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, text
from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from agent.db_models import (
    PlanningAmendmentInputDB,
    PlanningArtifactRevisionDB,
    PlanningLineageDB,
    PlanningOperationReceiptDB,
    PlanningTaskDispatchDB,
    PlanningTaskMappingDB,
    WorkerTaskProposalDB,
)


class PlanningArtifactRepository:
    """Session-bound persistence port; transaction ownership stays in the UoW."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_revision(self, revision: PlanningArtifactRevisionDB) -> PlanningArtifactRevisionDB:
        self._session.add(revision)
        self._session.flush()
        return revision

    def acquire_scope_lock(self, scope_key: str) -> None:
        """Serialize a planning aggregate across Hub processes on PostgreSQL.

        Development databases retain deterministic unique/CAS semantics and
        are additionally protected by application-local locks in the service
        layer where a count-plus-insert invariant is required.
        """
        if self._supports_row_lock():
            self._session.exec(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_key, 0))"),
                params={"scope_key": str(scope_key or "")},
            )

    def get_revision(
        self,
        revision_id: str,
        *,
        for_update: bool = False,
    ) -> PlanningArtifactRevisionDB | None:
        statement = select(PlanningArtifactRevisionDB).where(PlanningArtifactRevisionDB.id == str(revision_id or ""))
        if for_update and self._supports_row_lock():
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()

    def get_revision_by_artifact(
        self,
        *,
        artifact_id: str,
        revision: int,
        for_update: bool = False,
    ) -> PlanningArtifactRevisionDB | None:
        statement = select(PlanningArtifactRevisionDB).where(
            PlanningArtifactRevisionDB.artifact_id == str(artifact_id or ""),
            PlanningArtifactRevisionDB.revision == int(revision),
        )
        if for_update and self._supports_row_lock():
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()

    def latest_revision(
        self,
        *,
        artifact_id: str,
        artifact_type: str | None = None,
    ) -> PlanningArtifactRevisionDB | None:
        statement = (
            select(PlanningArtifactRevisionDB)
            .where(PlanningArtifactRevisionDB.artifact_id == str(artifact_id or ""))
            .order_by(PlanningArtifactRevisionDB.revision.desc())  # type: ignore[attr-defined]
        )
        if artifact_type:
            statement = statement.where(PlanningArtifactRevisionDB.artifact_type == str(artifact_type))
        return self._session.exec(statement).first()

    def next_revision_number(self, *, artifact_id: str) -> int:
        value = self._session.exec(
            select(func.max(PlanningArtifactRevisionDB.revision)).where(
                PlanningArtifactRevisionDB.artifact_id == str(artifact_id or "")
            )
        ).one()
        return int(value or 0) + 1

    def list_revisions(
        self,
        *,
        goal_id: str,
        organization_id: str,
        artifact_type: str | None = None,
    ) -> list[PlanningArtifactRevisionDB]:
        statement = select(PlanningArtifactRevisionDB).where(
            PlanningArtifactRevisionDB.goal_id == str(goal_id or ""),
            PlanningArtifactRevisionDB.organization_id == str(organization_id or ""),
        )
        if artifact_type:
            statement = statement.where(PlanningArtifactRevisionDB.artifact_type == str(artifact_type))
        statement = statement.order_by(PlanningArtifactRevisionDB.created_at.desc())  # type: ignore[attr-defined]
        return list(self._session.exec(statement).all())

    def compare_and_set_status(
        self,
        *,
        revision_id: str,
        expected_status: str,
        next_status: str,
        expected_digest: str,
        expected_policy_hash: str,
        values: dict[str, Any] | None = None,
    ) -> bool:
        result = self._session.exec(
            sa_update(PlanningArtifactRevisionDB)
            .where(
                PlanningArtifactRevisionDB.id == str(revision_id or ""),
                PlanningArtifactRevisionDB.status == str(expected_status or ""),
                PlanningArtifactRevisionDB.content_digest == str(expected_digest or ""),
                PlanningArtifactRevisionDB.policy_hash == str(expected_policy_hash or ""),
            )
            .values(status=str(next_status or ""), **dict(values or {}))
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    def supersede_other_revisions(
        self,
        *,
        artifact_id: str,
        keep_revision_id: str,
        statuses: Sequence[str],
    ) -> int:
        result = self._session.exec(
            sa_update(PlanningArtifactRevisionDB)
            .where(
                PlanningArtifactRevisionDB.artifact_id == str(artifact_id or ""),
                PlanningArtifactRevisionDB.id != str(keep_revision_id or ""),
                PlanningArtifactRevisionDB.status.in_(tuple(statuses)),
            )
            .values(status="superseded")
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def mark_derived_tracks_stale(self, *, category_revision_id: str) -> int:
        result = self._session.exec(
            sa_update(PlanningArtifactRevisionDB)
            .where(
                PlanningArtifactRevisionDB.artifact_type == "planning_track",
                PlanningArtifactRevisionDB.parent_revision_id == str(category_revision_id or ""),
                PlanningArtifactRevisionDB.status.in_(("valid", "adopted")),
            )
            .values(status="stale")
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def add_lineage(self, rows: Sequence[PlanningLineageDB]) -> None:
        self._session.add_all(list(rows))
        self._session.flush()

    def list_lineage_for_track(self, track_revision_id: str) -> list[PlanningLineageDB]:
        return list(
            self._session.exec(
                select(PlanningLineageDB).where(PlanningLineageDB.track_revision_id == str(track_revision_id or ""))
            ).all()
        )

    def get_mapping(
        self,
        *,
        track_revision_id: str,
        plan_task_id: str,
    ) -> PlanningTaskMappingDB | None:
        return self._session.exec(
            select(PlanningTaskMappingDB).where(
                PlanningTaskMappingDB.track_revision_id == str(track_revision_id or ""),
                PlanningTaskMappingDB.plan_task_id == str(plan_task_id or ""),
            )
        ).one_or_none()

    def get_mapping_by_id(self, mapping_id: str) -> PlanningTaskMappingDB | None:
        return self._session.get(PlanningTaskMappingDB, str(mapping_id or ""))

    def add_mapping(self, mapping: PlanningTaskMappingDB) -> PlanningTaskMappingDB:
        self._session.add(mapping)
        self._session.flush()
        return mapping

    def list_mappings(self, track_revision_id: str) -> list[PlanningTaskMappingDB]:
        return list(
            self._session.exec(
                select(PlanningTaskMappingDB).where(
                    PlanningTaskMappingDB.track_revision_id == str(track_revision_id or "")
                )
            ).all()
        )

    def find_mappings_for_plan_task(
        self,
        *,
        goal_id: str,
        plan_task_id: str,
    ) -> list[PlanningTaskMappingDB]:
        return list(
            self._session.exec(
                select(PlanningTaskMappingDB).where(
                    PlanningTaskMappingDB.goal_id == str(goal_id or ""),
                    PlanningTaskMappingDB.plan_task_id == str(plan_task_id or ""),
                )
            ).all()
        )

    def get_dispatch_by_idempotency(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
    ) -> PlanningTaskDispatchDB | None:
        return self._session.exec(
            select(PlanningTaskDispatchDB).where(
                PlanningTaskDispatchDB.organization_id == str(organization_id or ""),
                PlanningTaskDispatchDB.idempotency_key == str(idempotency_key or ""),
            )
        ).one_or_none()

    def add_dispatch(self, dispatch: PlanningTaskDispatchDB) -> PlanningTaskDispatchDB:
        self._session.add(dispatch)
        self._session.flush()
        return dispatch

    def get_receipt_by_intent(
        self,
        *,
        approval_intent_key: str,
        operation: str,
    ) -> PlanningOperationReceiptDB | None:
        return self._session.exec(
            select(PlanningOperationReceiptDB).where(
                PlanningOperationReceiptDB.approval_intent_key == str(approval_intent_key or ""),
                PlanningOperationReceiptDB.operation == str(operation or ""),
            )
        ).one_or_none()

    def get_receipt(self, receipt_id: str) -> PlanningOperationReceiptDB | None:
        return self._session.get(PlanningOperationReceiptDB, str(receipt_id or ""))

    def add_receipt(self, receipt: PlanningOperationReceiptDB) -> PlanningOperationReceiptDB:
        self._session.add(receipt)
        self._session.flush()
        return receipt

    def get_proposal(
        self,
        proposal_id: str,
        *,
        for_update: bool = False,
    ) -> WorkerTaskProposalDB | None:
        statement = select(WorkerTaskProposalDB).where(WorkerTaskProposalDB.proposal_id == str(proposal_id or ""))
        if for_update and self._supports_row_lock():
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()

    def get_proposal_by_idempotency(
        self,
        *,
        organization_id: str,
        source_task_id: str,
        idempotency_key: str,
    ) -> WorkerTaskProposalDB | None:
        return self._session.exec(
            select(WorkerTaskProposalDB).where(
                WorkerTaskProposalDB.organization_id == str(organization_id or ""),
                WorkerTaskProposalDB.source_task_id == str(source_task_id or ""),
                WorkerTaskProposalDB.idempotency_key == str(idempotency_key or ""),
            )
        ).one_or_none()

    def add_proposal(self, proposal: WorkerTaskProposalDB) -> WorkerTaskProposalDB:
        self._session.add(proposal)
        self._session.flush()
        return proposal

    def count_proposals_for_source(self, *, organization_id: str, source_task_id: str) -> int:
        value = self._session.exec(
            select(func.count())
            .select_from(WorkerTaskProposalDB)
            .where(
                WorkerTaskProposalDB.organization_id == str(organization_id or ""),
                WorkerTaskProposalDB.source_task_id == str(source_task_id or ""),
            )
        ).one()
        return int(value or 0)

    def list_proposals(
        self,
        *,
        organization_id: str,
        source_goal_id: str | None = None,
        state: str | None = None,
    ) -> list[WorkerTaskProposalDB]:
        statement = select(WorkerTaskProposalDB).where(
            WorkerTaskProposalDB.organization_id == str(organization_id or "")
        )
        if source_goal_id:
            statement = statement.where(WorkerTaskProposalDB.source_goal_id == str(source_goal_id))
        if state:
            statement = statement.where(WorkerTaskProposalDB.state == str(state))
        statement = statement.order_by(WorkerTaskProposalDB.created_at.desc())  # type: ignore[attr-defined]
        return list(self._session.exec(statement).all())

    def get_amendment_input_by_idempotency(
        self,
        *,
        organization_id: str,
        source_task_id: str,
        input_kind: str,
        idempotency_key: str,
    ) -> PlanningAmendmentInputDB | None:
        return self._session.exec(
            select(PlanningAmendmentInputDB).where(
                PlanningAmendmentInputDB.organization_id == str(organization_id or ""),
                PlanningAmendmentInputDB.source_task_id == str(source_task_id or ""),
                PlanningAmendmentInputDB.input_kind == str(input_kind or ""),
                PlanningAmendmentInputDB.idempotency_key == str(idempotency_key or ""),
            )
        ).one_or_none()

    def add_amendment_input(
        self,
        amendment: PlanningAmendmentInputDB,
    ) -> PlanningAmendmentInputDB:
        self._session.add(amendment)
        self._session.flush()
        return amendment

    def list_amendment_inputs(
        self,
        *,
        organization_id: str,
        goal_id: str,
    ) -> list[PlanningAmendmentInputDB]:
        return list(
            self._session.exec(
                select(PlanningAmendmentInputDB)
                .where(
                    PlanningAmendmentInputDB.organization_id == str(organization_id or ""),
                    PlanningAmendmentInputDB.goal_id == str(goal_id or ""),
                )
                .order_by(PlanningAmendmentInputDB.created_at.desc())  # type: ignore[attr-defined]
            ).all()
        )

    def _supports_row_lock(self) -> bool:
        bind = self._session.get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "")) == "postgresql"


__all__ = ["PlanningArtifactRepository"]

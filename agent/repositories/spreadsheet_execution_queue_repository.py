"""Transactional repository for the Hub-owned spreadsheet execution queue."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.db_models.spreadsheet_studio import SpreadsheetExecutionJobDB
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json, require_id


class SqlSpreadsheetExecutionQueueRepository:
    durable = True
    production_component = True

    def __init__(self, *, db_engine) -> None:
        self._engine = db_engine

    def enqueue(self, assignment: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        value = dict(assignment)
        tenant_id = require_id(value.get("tenant_id"), "tenant_id")
        principal_id = require_id(value.get("principal_id"), "principal_id")
        proposal = dict(value.get("proposal") or {})
        proposal_id = require_id(proposal.get("proposal_id"), "proposal_id")
        document_id = require_id(proposal.get("document_id"), "document_id")
        proposal_digest = str(value.get("proposal_digest") or "")
        supplied_digest = str(value.get("assignment_digest") or "")
        unsigned = {key: item for key, item in value.items() if key != "assignment_digest"}
        if not supplied_digest or canonical_digest(unsigned) != supplied_digest:
            raise ValueError("spreadsheet_assignment_digest_invalid")
        record = SpreadsheetExecutionJobDB(
            tenant_id=tenant_id,
            job_id=f"spreadsheet-job-{uuid.uuid4()}",
            proposal_id=proposal_id,
            document_id=document_id,
            principal_id=principal_id,
            proposal_digest=proposal_digest,
            assignment_digest=supplied_digest,
            assignment_json=canonical_json(value),
            status="dispatch_pending",
        )
        try:
            with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
                session.add(record)
        except IntegrityError:
            with Session(bind=self._engine) as session:
                existing = session.execute(
                    select(SpreadsheetExecutionJobDB).where(
                        SpreadsheetExecutionJobDB.tenant_id == tenant_id,
                        SpreadsheetExecutionJobDB.proposal_id == proposal_id,
                    )
                ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.proposal_digest != proposal_digest or existing.assignment_digest != supplied_digest:
                raise SpreadsheetStoreConflict("spreadsheet_execution_replay_conflict")
            return self._projection(existing), False
        return self._projection(record), True

    def bind_dispatch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_job_id: str,
        slot_lease_id: str,
        worker_id: str,
        status: str,
        queue_position: int | None,
    ) -> dict[str, Any]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"queued", "leased"}:
            raise ValueError("spreadsheet_dispatch_status_invalid")
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            statement = select(SpreadsheetExecutionJobDB).where(
                SpreadsheetExecutionJobDB.tenant_id == require_id(tenant_id, "tenant_id"),
                SpreadsheetExecutionJobDB.job_id == require_id(job_id, "job_id"),
            )
            if str(self._engine.dialect.name).lower() == "postgresql":
                statement = statement.with_for_update()
            record = session.execute(statement).scalar_one_or_none()
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if record.status != "dispatch_pending":
                if (
                    record.worker_job_id == worker_job_id
                    and record.slot_lease_id == slot_lease_id
                    and record.status == normalized_status
                ):
                    return self._projection(record)
                raise SpreadsheetStoreConflict("spreadsheet_execution_dispatch_conflict")
            record.worker_job_id = require_id(worker_job_id, "worker_job_id")
            record.slot_lease_id = require_id(slot_lease_id, "slot_lease_id")
            record.worker_id = require_id(worker_id, "worker_id")
            record.status = normalized_status
            record.queue_position = queue_position
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
        return self._projection(record)

    def get(self, *, tenant_id: str, job_id: str) -> dict[str, Any]:
        with Session(bind=self._engine) as session:
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
        if record is None:
            raise KeyError("spreadsheet_execution_job_not_found")
        return self._projection(record)

    def fail_dispatch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        reason_code: str,
    ) -> dict[str, Any]:
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if record.status not in {"dispatch_pending", "failed"}:
                raise SpreadsheetStoreConflict("spreadsheet_execution_dispatch_conflict")
            record.status = "failed"
            record.result_json = canonical_json(
                {
                    "schema": "ananta.spreadsheet-execution-dispatch-failure.v1",
                    "reason_code": str(reason_code or "spreadsheet_dispatch_rejected"),
                }
            )
            record.result_digest = canonical_digest(json.loads(record.result_json))
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
        return self._projection(record)

    def claim(
        self,
        *,
        worker_id: str,
        callback_jti: str,
        artifact_handle_jti: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        worker = require_id(worker_id, "worker_id")
        callback = require_id(callback_jti, "callback_jti")
        artifact_jti = (
            require_id(artifact_handle_jti, "artifact_handle_jti") if artifact_handle_jti is not None else None
        )
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            candidate = session.execute(
                select(SpreadsheetExecutionJobDB)
                .where(
                    SpreadsheetExecutionJobDB.status == "leased",
                    SpreadsheetExecutionJobDB.worker_id == worker,
                    SpreadsheetExecutionJobDB.claimed_at.is_(None),
                )
                .order_by(SpreadsheetExecutionJobDB.created_at, SpreadsheetExecutionJobDB.job_id)
                .limit(1)
            ).scalar_one_or_none()
            if candidate is None:
                return None
            now = datetime.now(timezone.utc)
            changed = session.execute(
                update(SpreadsheetExecutionJobDB)
                .where(
                    SpreadsheetExecutionJobDB.tenant_id == candidate.tenant_id,
                    SpreadsheetExecutionJobDB.job_id == candidate.job_id,
                    SpreadsheetExecutionJobDB.status == "leased",
                    SpreadsheetExecutionJobDB.claimed_at.is_(None),
                )
                .values(
                    callback_jti=callback,
                    artifact_handle_jti=artifact_jti,
                    claimed_at=now,
                    updated_at=now,
                )
            ).rowcount
            if changed != 1:
                raise SpreadsheetStoreConflict("spreadsheet_execution_claim_conflict")
            session.expire(candidate)
            session.refresh(candidate)
            assignment = self._assignment(candidate)
        return self._projection(candidate), assignment

    def consume_artifact_handle(
        self,
        *,
        tenant_id: str,
        job_id: str,
        jti: str,
    ) -> dict[str, Any]:
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if (
                record.status != "leased"
                or record.claimed_at is None
                or record.artifact_handle_jti != require_id(jti, "artifact_handle_jti")
            ):
                raise SpreadsheetStoreConflict("spreadsheet_artifact_handle_binding_invalid")
            if record.artifact_consumed_at is not None:
                raise SpreadsheetStoreConflict("spreadsheet_artifact_handle_consumed")
            record.artifact_consumed_at = datetime.now(timezone.utc)
            record.updated_at = record.artifact_consumed_at
            session.add(record)
            assignment = self._assignment(record)
        return assignment

    def complete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        callback_jti: str,
        result: Mapping[str, Any],
        callback_payload_digest: str,
    ) -> dict[str, Any]:
        value = dict(result)
        result_digest = canonical_digest(value)
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if record.status == "completed":
                if record.callback_jti == callback_jti and record.callback_payload_digest == callback_payload_digest:
                    return {**self._projection(record), "replayed": True}
                raise SpreadsheetStoreConflict("spreadsheet_callback_replay_conflict")
            if (
                record.status != "leased"
                or record.claimed_at is None
                or record.callback_jti != require_id(callback_jti, "callback_jti")
            ):
                raise SpreadsheetStoreConflict("spreadsheet_callback_binding_invalid")
            record.status = "completed"
            record.result_json = canonical_json(value)
            record.result_digest = result_digest
            record.callback_payload_digest = callback_payload_digest
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
        return {**self._projection(record), "replayed": False}

    def get_assignment(self, *, tenant_id: str, job_id: str) -> dict[str, Any]:
        with Session(bind=self._engine) as session:
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
        if record is None:
            raise KeyError("spreadsheet_execution_job_not_found")
        return self._assignment(record)

    def fail_claim(self, *, tenant_id: str, job_id: str, reason_code: str) -> dict[str, Any]:
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if record.status != "leased" or record.claimed_at is None:
                raise SpreadsheetStoreConflict("spreadsheet_execution_claim_conflict")
            record.status = "failed"
            record.result_json = canonical_json(
                {
                    "schema": "ananta.spreadsheet-execution-claim-failure.v1",
                    "reason_code": str(reason_code or "spreadsheet_lease_inactive"),
                }
            )
            record.result_digest = canonical_digest(json.loads(record.result_json))
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
        return self._projection(record)

    def fail_execution(
        self,
        *,
        tenant_id: str,
        job_id: str,
        callback_jti: str,
        reason_code: str,
        callback_payload_digest: str,
    ) -> dict[str, Any]:
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            record = session.get(
                SpreadsheetExecutionJobDB,
                (require_id(tenant_id, "tenant_id"), require_id(job_id, "job_id")),
            )
            if record is None:
                raise KeyError("spreadsheet_execution_job_not_found")
            if record.status == "failed" and record.callback_payload_digest is not None:
                if record.callback_jti == callback_jti and record.callback_payload_digest == callback_payload_digest:
                    return {**self._projection(record), "replayed": True}
                raise SpreadsheetStoreConflict("spreadsheet_callback_replay_conflict")
            if (
                record.status != "leased"
                or record.claimed_at is None
                or record.callback_jti != require_id(callback_jti, "callback_jti")
            ):
                raise SpreadsheetStoreConflict("spreadsheet_callback_binding_invalid")
            failure = {
                "schema": "ananta.spreadsheet-execution-failure.v1",
                "reason_code": str(reason_code or "spreadsheet_worker_execution_failed"),
                "automatic_decision": True,
                "human_intervention_required": False,
            }
            record.status = "failed"
            record.result_json = canonical_json(failure)
            record.result_digest = canonical_digest(failure)
            record.callback_payload_digest = callback_payload_digest
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
        return {**self._projection(record), "replayed": False}

    def operations_summary(self, *, stale_before: datetime) -> dict[str, Any]:
        if stale_before.tzinfo is None:
            raise ValueError("spreadsheet_stale_before_invalid")
        active = ("dispatch_pending", "queued", "leased")
        with Session(bind=self._engine) as session:
            counts = dict(
                session.execute(
                    select(SpreadsheetExecutionJobDB.status, func.count()).group_by(
                        SpreadsheetExecutionJobDB.status
                    )
                ).all()
            )
            stale = list(
                session.execute(
                    select(SpreadsheetExecutionJobDB)
                    .where(
                        SpreadsheetExecutionJobDB.status.in_(active),
                        SpreadsheetExecutionJobDB.updated_at < stale_before,
                    )
                    .order_by(SpreadsheetExecutionJobDB.updated_at, SpreadsheetExecutionJobDB.job_id)
                    .limit(100)
                ).scalars()
            )
        return {
            "counts": {
                status: int(counts.get(status, 0))
                for status in (*active, "completed", "failed", "cancelled")
            },
            "stale_jobs": [self._projection(record) for record in stale],
        }

    def terminalize_stale(self, *, stale_before: datetime, limit: int) -> list[dict[str, Any]]:
        if stale_before.tzinfo is None or not 1 <= int(limit) <= 100:
            raise ValueError("spreadsheet_stale_recovery_input_invalid")
        active = ("dispatch_pending", "queued", "leased")
        recovered: list[dict[str, Any]] = []
        with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
            statement = (
                select(SpreadsheetExecutionJobDB)
                .where(
                    SpreadsheetExecutionJobDB.status.in_(active),
                    SpreadsheetExecutionJobDB.updated_at < stale_before,
                )
                .order_by(SpreadsheetExecutionJobDB.updated_at, SpreadsheetExecutionJobDB.job_id)
                .limit(int(limit))
            )
            if str(self._engine.dialect.name).lower() == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            records = list(session.execute(statement).scalars())
            for record in records:
                failure = {
                    "schema": "ananta.spreadsheet-execution-recovery.v1",
                    "reason_code": "spreadsheet_execution_stale_terminalized",
                    "automatic_decision": True,
                    "human_intervention_required": False,
                }
                record.status = "failed"
                record.result_json = canonical_json(failure)
                record.result_digest = canonical_digest(failure)
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                recovered.append(self._projection(record))
        return recovered

    @staticmethod
    def _projection(record: SpreadsheetExecutionJobDB) -> dict[str, Any]:
        SqlSpreadsheetExecutionQueueRepository._assignment(record)
        projection = {
            "schema": "ananta.spreadsheet-execution-job.v1",
            "job_id": record.job_id,
            "proposal_id": record.proposal_id,
            "document_id": record.document_id,
            "proposal_digest": record.proposal_digest,
            "assignment_digest": record.assignment_digest,
            "status": record.status,
            "worker_job_id": record.worker_job_id,
            "slot_lease_id": record.slot_lease_id,
            "worker_id": record.worker_id,
            "queue_position": record.queue_position,
            "automatic_decision": True,
            "human_intervention_required": False,
            "created_at": SqlSpreadsheetExecutionQueueRepository._timestamp(record.created_at),
            "updated_at": SqlSpreadsheetExecutionQueueRepository._timestamp(record.updated_at),
        }
        if record.claimed_at is not None:
            projection["claimed_at"] = SqlSpreadsheetExecutionQueueRepository._timestamp(record.claimed_at)
        if record.status == "completed" and record.result_json is not None:
            result = json.loads(record.result_json)
            if canonical_digest(result) != record.result_digest:
                raise RuntimeError("spreadsheet_execution_result_integrity_failed")
            projection["result"] = result
        return projection

    @staticmethod
    def _timestamp(value: datetime) -> float:
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.timestamp()

    @staticmethod
    def _assignment(record: SpreadsheetExecutionJobDB) -> dict[str, Any]:
        assignment = json.loads(record.assignment_json)
        unsigned = {key: item for key, item in assignment.items() if key != "assignment_digest"}
        if canonical_digest(unsigned) != record.assignment_digest:
            raise RuntimeError("spreadsheet_execution_assignment_integrity_failed")
        if assignment.get("assignment_digest") != record.assignment_digest:
            raise RuntimeError("spreadsheet_execution_assignment_projection_mismatch")
        return assignment


__all__ = ["SqlSpreadsheetExecutionQueueRepository"]

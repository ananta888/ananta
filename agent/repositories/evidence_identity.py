"""Transactional persistence for Hub-issued evidence identities."""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.ports.evidence_identity import (
    RunEvidenceIdentity,
    SourceEvidenceIdentity,
)


class EvidenceIdentityPersistenceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlEvidenceIdentityRepository:
    def __init__(self, database=engine) -> None:
        self._database = database

    def register_source(
        self, identity: SourceEvidenceIdentity
    ) -> SourceEvidenceIdentity:
        row = self._source_row(identity)
        with Session(self._database) as session:
            existing = session.get(
                HubSourceEvidenceIdentityDB,
                (identity.tenant_id, identity.project_id, identity.source_id),
            )
            if existing is not None:
                return self._same_source(existing, identity)
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(HubSourceEvidenceIdentityDB).where(
                        HubSourceEvidenceIdentityDB.binding_digest
                        == identity.binding_digest
                    )
                ).first()
                if existing is None:
                    raise
                return self._same_source(existing, identity)
            session.refresh(row)
            return self._source(row)

    def get_source(
        self, *, tenant_id: str, project_id: str, source_id: str
    ) -> SourceEvidenceIdentity | None:
        with Session(self._database) as session:
            row = session.exec(
                select(HubSourceEvidenceIdentityDB).where(
                    HubSourceEvidenceIdentityDB.source_id == source_id,
                    HubSourceEvidenceIdentityDB.tenant_id == tenant_id,
                    HubSourceEvidenceIdentityDB.project_id == project_id,
                )
            ).first()
            return self._source(row) if row is not None else None

    def reserve_run(self, identity: RunEvidenceIdentity) -> RunEvidenceIdentity:
        row = self._run_row(identity)
        with Session(self._database) as session:
            existing = session.get(
                HubRunEvidenceIdentityDB,
                (identity.tenant_id, identity.project_id, identity.run_id),
            )
            if existing is not None:
                return self._same_run(existing, identity)
            sources = session.exec(
                select(HubSourceEvidenceIdentityDB).where(
                    HubSourceEvidenceIdentityDB.tenant_id == identity.tenant_id,
                    HubSourceEvidenceIdentityDB.project_id == identity.project_id,
                    HubSourceEvidenceIdentityDB.source_id.in_(identity.source_ids),
                    HubSourceEvidenceIdentityDB.state == "admitted",
                )
            ).all()
            if {value.source_id for value in sources} != set(identity.source_ids):
                raise EvidenceIdentityPersistenceError(
                    "evidence_run_source_identity_unavailable"
                )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(HubRunEvidenceIdentityDB).where(
                        HubRunEvidenceIdentityDB.reservation_key_digest
                        == identity.reservation_key_digest
                    )
                ).first()
                if existing is None:
                    raise
                return self._same_run(existing, identity)
            session.refresh(row)
            return self._run(row)

    def get_run(
        self, *, tenant_id: str, project_id: str, run_id: str
    ) -> RunEvidenceIdentity | None:
        with Session(self._database) as session:
            row = session.exec(
                select(HubRunEvidenceIdentityDB).where(
                    HubRunEvidenceIdentityDB.run_id == run_id,
                    HubRunEvidenceIdentityDB.tenant_id == tenant_id,
                    HubRunEvidenceIdentityDB.project_id == project_id,
                )
            ).first()
            return self._run(row) if row is not None else None

    def complete_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        terminal_state: str,
        result_digest: str,
        updated_at_epoch: float,
    ) -> RunEvidenceIdentity:
        with Session(self._database) as session:
            row = session.exec(
                select(HubRunEvidenceIdentityDB)
                .where(
                    HubRunEvidenceIdentityDB.run_id == run_id,
                    HubRunEvidenceIdentityDB.tenant_id == tenant_id,
                    HubRunEvidenceIdentityDB.project_id == project_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise EvidenceIdentityPersistenceError(
                    "evidence_run_identity_not_found"
                )
            if (
                row.assignment_id != assignment_id
                or row.dispatch_lease_id != dispatch_lease_id
            ):
                raise EvidenceIdentityPersistenceError(
                    "evidence_run_assignment_binding_mismatch"
                )
            if row.state in {"succeeded", "failed", "cancelled"}:
                if row.state != terminal_state or row.result_digest != result_digest:
                    raise EvidenceIdentityPersistenceError(
                        "evidence_run_terminal_replay_conflict"
                    )
                return self._run(row)
            if row.state != "reserved":
                raise EvidenceIdentityPersistenceError(
                    "evidence_run_state_invalid"
                )
            row.state = terminal_state
            row.result_digest = result_digest
            row.updated_at_epoch = updated_at_epoch
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._run(row)

    @classmethod
    def _same_source(
        cls, row: HubSourceEvidenceIdentityDB, identity: SourceEvidenceIdentity
    ) -> SourceEvidenceIdentity:
        projected = cls._source(row)
        if (
            projected.source_id != identity.source_id
            or projected.binding_digest != identity.binding_digest
        ):
            raise EvidenceIdentityPersistenceError(
                "evidence_source_identity_immutable_conflict"
            )
        return projected

    @classmethod
    def _same_run(
        cls, row: HubRunEvidenceIdentityDB, identity: RunEvidenceIdentity
    ) -> RunEvidenceIdentity:
        projected = cls._run(row)
        if (
            projected.run_id != identity.run_id
            or projected.binding_digest != identity.binding_digest
            or projected.reservation_key_digest
            != identity.reservation_key_digest
        ):
            raise EvidenceIdentityPersistenceError(
                "evidence_run_identity_immutable_conflict"
            )
        return projected

    @staticmethod
    def _source_row(identity: SourceEvidenceIdentity) -> HubSourceEvidenceIdentityDB:
        return HubSourceEvidenceIdentityDB(**asdict(identity))

    @staticmethod
    def _run_row(identity: RunEvidenceIdentity) -> HubRunEvidenceIdentityDB:
        payload = {**asdict(identity), "source_ids": list(identity.source_ids)}
        return HubRunEvidenceIdentityDB(**payload)

    @staticmethod
    def _source(row: HubSourceEvidenceIdentityDB) -> SourceEvidenceIdentity:
        return SourceEvidenceIdentity(**row.model_dump())

    @staticmethod
    def _run(row: HubRunEvidenceIdentityDB) -> RunEvidenceIdentity:
        payload = row.model_dump()
        payload["source_ids"] = tuple(payload["source_ids"])
        return RunEvidenceIdentity(**payload)


__all__ = ["EvidenceIdentityPersistenceError", "SqlEvidenceIdentityRepository"]

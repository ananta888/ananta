"""Read-only projections of the Hub semantic-compute lease authority."""

from __future__ import annotations

from typing import Callable

from sqlmodel import Session, select

from agent.db_models import SemanticComputeLeaseDB
from agent.repositories.semantic_lease_models import SemanticLeaseRepositoryError
from agent.repositories.semantic_lease_records import required_lease


class SemanticLeaseQueries:
    """Side-effect free lease reads; never stages or commits a mutation."""

    def __init__(
        self,
        *,
        db_engine,
        clock: Callable[[], float],
        clock_skew_seconds: float,
    ) -> None:
        self._engine = db_engine
        self._clock = clock
        self._clock_skew = clock_skew_seconds

    def list_for_principal(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        session_id: str,
        epoch: int,
        contract_id: str | None = None,
        limit: int = 100,
    ) -> list[SemanticComputeLeaseDB]:
        if not 1 <= limit <= 200:
            raise SemanticLeaseRepositoryError("limit_invalid")
        with Session(self._engine) as db:
            statement = select(SemanticComputeLeaseDB).where(
                SemanticComputeLeaseDB.tenant_id == tenant_id,
                SemanticComputeLeaseDB.owner_subject == owner_subject,
                SemanticComputeLeaseDB.session_id == session_id,
                SemanticComputeLeaseDB.epoch == epoch,
            )
            if contract_id is not None:
                statement = statement.where(SemanticComputeLeaseDB.contract_id == contract_id)
            return list(
                db.exec(
                    statement.order_by(
                        SemanticComputeLeaseDB.issued_at.desc(),
                        SemanticComputeLeaseDB.id.desc(),
                    ).limit(limit)
                )
            )

    def get_scoped(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
    ) -> SemanticComputeLeaseDB:
        with Session(self._engine) as db:
            item = db.exec(
                select(SemanticComputeLeaseDB).where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                )
            ).first()
            if item is None:
                raise SemanticLeaseRepositoryError("lease_not_found")
            return item

    def active_assignment_counts(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        executor_ids: set[str],
    ) -> dict[str, int]:
        """Project current Hub leases for deterministic load-aware fairness."""

        normalized = {str(value).strip() for value in executor_ids if str(value).strip()}
        if len(normalized) > 128:
            raise SemanticLeaseRepositoryError("executor_limit_invalid")
        if not normalized:
            return {}
        now = self._clock()
        with Session(self._engine) as db:
            rows = db.exec(
                select(SemanticComputeLeaseDB.executor_id).where(
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.session_id == session_id,
                    SemanticComputeLeaseDB.epoch == epoch,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.expires_at > now + self._clock_skew,
                    SemanticComputeLeaseDB.deadline_at > now,
                    SemanticComputeLeaseDB.executor_id.in_(normalized),
                )
            )
            counts: dict[str, int] = {}
            for executor_id in rows:
                rendered = str(executor_id)
                counts[rendered] = counts.get(rendered, 0) + 1
            return counts

    def authorize_result(
        self,
        *,
        lease_id: str,
        contract_digest: str,
        fencing_token: int,
        session_id: str,
        epoch: int,
        task_type: str,
        audience: str,
        sequence: int | None = None,
    ) -> SemanticComputeLeaseDB:
        now = self._clock()
        with Session(self._engine) as db:
            lease = required_lease(db, lease_id)
            bindings = (
                lease.contract_digest == contract_digest,
                lease.fencing_token == fencing_token,
                lease.session_id == session_id,
                lease.epoch == epoch,
                lease.task_type == task_type,
                lease.audience == audience,
            )
            if not all(bindings):
                raise SemanticLeaseRepositoryError("lease_binding_mismatch")
            if lease.status != "active" or lease.expires_at <= now + self._clock_skew or lease.deadline_at <= now:
                raise SemanticLeaseRepositoryError("lease_not_authorized")
            if sequence is not None and not lease.sequence_start <= sequence <= lease.sequence_end:
                raise SemanticLeaseRepositoryError("lease_sequence_mismatch")
            return lease

    def get(self, lease_id: str) -> SemanticComputeLeaseDB:
        with Session(self._engine) as db:
            return required_lease(db, lease_id)


__all__ = ["SemanticLeaseQueries"]

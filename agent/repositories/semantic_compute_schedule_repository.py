"""Read-only compatibility projection for Hub compute schedule receipts.

The authoritative write lives in ``SemanticLeaseRepository.schedule_once`` so
the receipt, leases, fencing counters and audit outbox commit atomically.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping

from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SemanticComputeScheduleReceiptDB
from agent.repositories.semantic_contract_repository import SemanticPrincipal


class SemanticComputeScheduleRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SemanticComputeScheduleRepository:
    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def replay(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        idempotency_key: str,
        request_digest: str,
    ) -> Mapping[str, Any] | None:
        now = self._clock()
        digest = self._key_digest(idempotency_key)
        with Session(self._engine) as db:
            item = db.exec(
                select(SemanticComputeScheduleReceiptDB).where(
                    SemanticComputeScheduleReceiptDB.tenant_id == principal.tenant_id,
                    SemanticComputeScheduleReceiptDB.owner_subject == principal.subject,
                    SemanticComputeScheduleReceiptDB.contract_id == contract_id,
                    SemanticComputeScheduleReceiptDB.idempotency_key_digest == digest,
                )
            ).first()
            if item is None or item.expires_at <= now:
                return None
            if item.request_digest != request_digest:
                raise SemanticComputeScheduleRepositoryError("idempotency_conflict")
            return dict(item.result_payload or {})

    @staticmethod
    def _key_digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()


_repository: SemanticComputeScheduleRepository | None = None


def get_semantic_compute_schedule_repository() -> SemanticComputeScheduleRepository:
    global _repository
    if _repository is None:
        _repository = SemanticComputeScheduleRepository()
    return _repository


__all__ = [
    "SemanticComputeScheduleRepository",
    "SemanticComputeScheduleRepositoryError",
    "get_semantic_compute_schedule_repository",
]

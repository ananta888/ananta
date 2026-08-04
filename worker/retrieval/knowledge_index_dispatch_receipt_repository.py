"""Worker-local durable replay ledger for bound knowledge-index execution."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import KnowledgeIndexWorkerDispatchReceiptDB
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES,
)

DISPATCH_RECEIPT_SCHEMA = "ananta.knowledge_index_worker_dispatch_receipt.v1"
DISPATCH_STATE_CLAIMED = "claimed"
DISPATCH_STATE_COMPLETED = "completed"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_LOCK = threading.RLock()


class SqlKnowledgeIndexWorkerDispatchReceiptRepository:
    """Execute a Worker-scoped binding at most once and replay its result.

    The clock is read only after the Worker claim lock is held.  The database
    uniqueness constraints remain the cross-process authority; the local lock
    gives SQLite and same-process claimants the same deterministic boundary.
    """

    def __init__(
        self,
        *,
        db_engine: Any | None = None,
        session_factory: Callable[[], Session] | None = None,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        if db_engine is not None and session_factory is not None:
            raise ValueError("knowledge_index_worker_dispatch_ledger_config_invalid")
        if session_factory is None:
            if db_engine is None:
                from agent.database import engine

                db_engine = engine
            selected_engine = db_engine

            def default_session_factory() -> Session:
                return Session(selected_engine)

            session_factory = default_session_factory
        self._session_factory = session_factory
        self._clock_ms = clock_ms

    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        assignment_id: str,
        lease_id: str,
        marker_digest: str,
        manifest_binding_digest: str,
        lease_expires_epoch_ms: int,
        grant_expires_at_epoch_ms: int,
    ) -> Mapping[str, Any]:
        """Persist a live claim or return an exact completed replay."""

        normalized = self._validate_binding(
            worker_id=worker_id,
            job_id=job_id,
            assignment_id=assignment_id,
            lease_id=lease_id,
            marker_digest=marker_digest,
            manifest_binding_digest=manifest_binding_digest,
            lease_expires_epoch_ms=lease_expires_epoch_ms,
            grant_expires_at_epoch_ms=grant_expires_at_epoch_ms,
        )
        receipt_id = hashlib.sha256(
            "\0".join(
                (
                    normalized["worker_id"],
                    normalized["job_id"],
                    normalized["assignment_id"],
                    normalized["lease_id"],
                    normalized["marker_digest"],
                )
            ).encode("utf-8")
        ).hexdigest()

        with _CLAIM_LOCK, self._session_factory() as session:
            existing = self._find_receipt(
                session,
                worker_id=normalized["worker_id"],
                job_id=normalized["job_id"],
            )
            if existing is not None:
                return self._resolve_existing_claim(
                    existing,
                    expected_binding=normalized,
                )
            claimed_at_epoch_ms = self._now_epoch_ms()
            if claimed_at_epoch_ms >= normalized["lease_expires_epoch_ms"]:
                raise ValueError("knowledge_index_execution_lease_stale")
            if claimed_at_epoch_ms >= normalized["grant_expires_at_epoch_ms"]:
                raise ValueError("knowledge_index_source_access_grant_expired")
            row = KnowledgeIndexWorkerDispatchReceiptDB(
                receipt_id=receipt_id,
                **normalized,
                claimed_at_epoch_ms=claimed_at_epoch_ms,
            )
            session.add(row)
            try:
                session.commit()
                session.refresh(row)
            except IntegrityError as exc:
                session.rollback()
                existing = self._find_receipt(
                    session,
                    worker_id=normalized["worker_id"],
                    job_id=normalized["job_id"],
                )
                if existing is None:
                    raise ValueError(
                        "knowledge_index_worker_dispatch_claim_conflict"
                    ) from exc
                return self._resolve_existing_claim(
                    existing,
                    expected_binding=normalized,
                )
            return self._to_replay_receipt(row)

    def complete(
        self,
        *,
        worker_id: str,
        job_id: str,
        assignment_id: str,
        lease_id: str,
        marker_digest: str,
        manifest_binding_digest: str,
        result_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Atomically durably publish one terminal result for a claim."""

        binding = self._validate_completion_binding(
            worker_id=worker_id,
            job_id=job_id,
            assignment_id=assignment_id,
            lease_id=lease_id,
            marker_digest=marker_digest,
            manifest_binding_digest=manifest_binding_digest,
        )
        normalized_result, result_digest = self._normalize_result(
            result_payload
        )
        with _CLAIM_LOCK, self._session_factory() as session:
            row = self._find_receipt(
                session,
                worker_id=binding["worker_id"],
                job_id=binding["job_id"],
            )
            if row is None:
                raise ValueError(
                    "knowledge_index_worker_dispatch_receipt_missing"
                )
            self._assert_exact_binding(row, binding)
            if row.state == DISPATCH_STATE_COMPLETED:
                completed = self._to_replay_receipt(row)
                if (
                    completed["result_digest"] != result_digest
                    or completed["result_payload"] != normalized_result
                ):
                    raise ValueError(
                        "knowledge_index_worker_dispatch_result_conflict"
                    )
                return completed
            if (
                row.state != DISPATCH_STATE_CLAIMED
                or row.result_digest is not None
                or row.result_payload is not None
                or row.completed_at_epoch_ms is not None
            ):
                raise ValueError(
                    "knowledge_index_worker_dispatch_receipt_invalid"
                )
            completed_at_epoch_ms = self._now_epoch_ms()
            if completed_at_epoch_ms < row.claimed_at_epoch_ms:
                raise ValueError("knowledge_index_worker_clock_invalid")
            result = session.exec(
                update(KnowledgeIndexWorkerDispatchReceiptDB)
                .where(
                    KnowledgeIndexWorkerDispatchReceiptDB.receipt_id
                    == row.receipt_id,
                    KnowledgeIndexWorkerDispatchReceiptDB.state
                    == DISPATCH_STATE_CLAIMED,
                    KnowledgeIndexWorkerDispatchReceiptDB.result_digest.is_(
                        None
                    ),
                    KnowledgeIndexWorkerDispatchReceiptDB.completed_at_epoch_ms.is_(
                        None
                    ),
                )
                .values(
                    state=DISPATCH_STATE_COMPLETED,
                    result_digest=result_digest,
                    result_payload=normalized_result,
                    completed_at_epoch_ms=completed_at_epoch_ms,
                )
            )
            session.commit()
            if int(result.rowcount or 0) != 1:
                current = self._find_receipt(
                    session,
                    worker_id=binding["worker_id"],
                    job_id=binding["job_id"],
                )
                if current is None:
                    raise ValueError(
                        "knowledge_index_worker_dispatch_receipt_missing"
                    )
                completed = self._to_replay_receipt(current)
                if (
                    completed["state"] != DISPATCH_STATE_COMPLETED
                    or completed["result_digest"] != result_digest
                    or completed["result_payload"] != normalized_result
                ):
                    raise ValueError(
                        "knowledge_index_worker_dispatch_result_conflict"
                    )
                return completed
            completed_row = self._find_receipt(
                session,
                worker_id=binding["worker_id"],
                job_id=binding["job_id"],
            )
            if completed_row is None:
                raise ValueError(
                    "knowledge_index_worker_dispatch_receipt_missing"
                )
            return self._to_replay_receipt(completed_row)

    def get_receipt(
        self,
        *,
        worker_id: str,
        job_id: str,
    ) -> Mapping[str, Any] | None:
        """Read the permanent claim used by later Worker output checks."""

        normalized_worker_id = str(worker_id or "").strip()
        normalized_job_id = str(job_id or "").strip()
        if _WORKER_ID.fullmatch(normalized_worker_id) is None or _IDENTIFIER.fullmatch(normalized_job_id) is None:
            raise ValueError("knowledge_index_worker_dispatch_binding_invalid")
        with self._session_factory() as session:
            row = self._find_receipt(
                session,
                worker_id=normalized_worker_id,
                job_id=normalized_job_id,
            )
            if row is None:
                return None
            self._validate_persisted_state(row)
            # Artifact authorization deliberately receives the stable public
            # claim projection, not the potentially large result outbox.
            return self._to_authorization_receipt(row)

    def _now_epoch_ms(self) -> int:
        now = self._clock_ms()
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("knowledge_index_worker_clock_invalid")
        return now

    @staticmethod
    def _validate_binding(**values: Any) -> dict[str, Any]:
        normalized = {
            "worker_id": str(values.get("worker_id") or "").strip(),
            "job_id": str(values.get("job_id") or "").strip(),
            "assignment_id": str(values.get("assignment_id") or "").strip(),
            "lease_id": str(values.get("lease_id") or "").strip(),
            "marker_digest": str(values.get("marker_digest") or "").strip(),
            "manifest_binding_digest": str(values.get("manifest_binding_digest") or "").strip(),
            "lease_expires_epoch_ms": values.get("lease_expires_epoch_ms"),
            "grant_expires_at_epoch_ms": values.get("grant_expires_at_epoch_ms"),
        }
        if (
            _WORKER_ID.fullmatch(normalized["worker_id"]) is None
            or _IDENTIFIER.fullmatch(normalized["job_id"]) is None
            or _IDENTIFIER.fullmatch(normalized["assignment_id"]) is None
            or _IDENTIFIER.fullmatch(normalized["lease_id"]) is None
            or _DIGEST.fullmatch(normalized["marker_digest"]) is None
            or _DIGEST.fullmatch(normalized["manifest_binding_digest"]) is None
        ):
            raise ValueError("knowledge_index_worker_dispatch_binding_invalid")
        for field in (
            "lease_expires_epoch_ms",
            "grant_expires_at_epoch_ms",
        ):
            value = normalized[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("knowledge_index_worker_dispatch_binding_invalid")
        return normalized

    @classmethod
    def _validate_completion_binding(
        cls,
        **values: Any,
    ) -> dict[str, str]:
        normalized = {
            "worker_id": str(values.get("worker_id") or "").strip(),
            "job_id": str(values.get("job_id") or "").strip(),
            "assignment_id": str(
                values.get("assignment_id") or ""
            ).strip(),
            "lease_id": str(values.get("lease_id") or "").strip(),
            "marker_digest": str(
                values.get("marker_digest") or ""
            ).strip(),
            "manifest_binding_digest": str(
                values.get("manifest_binding_digest") or ""
            ).strip(),
        }
        if (
            _WORKER_ID.fullmatch(normalized["worker_id"]) is None
            or _IDENTIFIER.fullmatch(normalized["job_id"]) is None
            or _IDENTIFIER.fullmatch(normalized["assignment_id"])
            is None
            or _IDENTIFIER.fullmatch(normalized["lease_id"]) is None
            or _DIGEST.fullmatch(normalized["marker_digest"]) is None
            or _DIGEST.fullmatch(
                normalized["manifest_binding_digest"]
            )
            is None
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_binding_invalid"
            )
        return normalized

    @staticmethod
    def _normalize_result(
        result_payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(result_payload, Mapping):
            raise ValueError(
                "knowledge_index_worker_dispatch_result_invalid"
            )
        try:
            encoded = json.dumps(
                dict(result_payload),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError(
                "knowledge_index_worker_dispatch_result_invalid"
            ) from exc
        if len(encoded) > MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES:
            raise ValueError(
                "knowledge_index_worker_dispatch_result_too_large"
            )
        normalized = json.loads(encoded.decode("ascii"))
        if not isinstance(normalized, dict):
            raise ValueError(
                "knowledge_index_worker_dispatch_result_invalid"
            )
        return normalized, hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _find_receipt(
        session: Session,
        *,
        worker_id: str,
        job_id: str,
    ) -> KnowledgeIndexWorkerDispatchReceiptDB | None:
        return session.exec(
            select(KnowledgeIndexWorkerDispatchReceiptDB).where(
                KnowledgeIndexWorkerDispatchReceiptDB.worker_id
                == worker_id,
                KnowledgeIndexWorkerDispatchReceiptDB.job_id == job_id,
            )
        ).one_or_none()

    @staticmethod
    def _assert_exact_binding(
        row: KnowledgeIndexWorkerDispatchReceiptDB,
        expected: Mapping[str, Any],
    ) -> None:
        fields = (
            "worker_id",
            "job_id",
            "assignment_id",
            "lease_id",
            "marker_digest",
            "manifest_binding_digest",
        )
        if any(getattr(row, field) != expected[field] for field in fields):
            raise ValueError(
                "knowledge_index_worker_dispatch_binding_conflict"
            )
        for field in (
            "lease_expires_epoch_ms",
            "grant_expires_at_epoch_ms",
        ):
            if field in expected and getattr(row, field) != expected[field]:
                raise ValueError(
                    "knowledge_index_worker_dispatch_binding_conflict"
                )

    @classmethod
    def _resolve_existing_claim(
        cls,
        row: KnowledgeIndexWorkerDispatchReceiptDB,
        *,
        expected_binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        cls._assert_exact_binding(row, expected_binding)
        if row.state == DISPATCH_STATE_COMPLETED:
            return cls._to_replay_receipt(row)
        cls._validate_persisted_state(row)
        raise ValueError(
            "knowledge_index_worker_dispatch_result_pending"
        )

    @classmethod
    def _validate_persisted_state(
        cls,
        row: KnowledgeIndexWorkerDispatchReceiptDB,
    ) -> None:
        if row.state == DISPATCH_STATE_CLAIMED:
            if (
                row.result_digest is None
                and row.result_payload is None
                and row.completed_at_epoch_ms is None
            ):
                return
            raise ValueError(
                "knowledge_index_worker_dispatch_receipt_invalid"
            )
        if row.state != DISPATCH_STATE_COMPLETED:
            raise ValueError(
                "knowledge_index_worker_dispatch_receipt_invalid"
            )
        if (
            not isinstance(row.result_payload, Mapping)
            or _DIGEST.fullmatch(str(row.result_digest or "")) is None
            or isinstance(row.completed_at_epoch_ms, bool)
            or not isinstance(row.completed_at_epoch_ms, int)
            or row.completed_at_epoch_ms < row.claimed_at_epoch_ms
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_receipt_invalid"
            )
        normalized, digest = cls._normalize_result(row.result_payload)
        if digest != row.result_digest or normalized != row.result_payload:
            raise ValueError(
                "knowledge_index_worker_dispatch_receipt_invalid"
            )

    @staticmethod
    def _to_authorization_receipt(
        row: KnowledgeIndexWorkerDispatchReceiptDB,
    ) -> dict[str, Any]:
        return {
            "schema": DISPATCH_RECEIPT_SCHEMA,
            "job_id": row.job_id,
            "phase": "execute",
            "worker_id": row.worker_id,
            "assignment_id": row.assignment_id,
            "lease_id": row.lease_id,
            "marker_digest": row.marker_digest,
            "manifest_binding_digest": row.manifest_binding_digest,
            "claimed_at_epoch_ms": row.claimed_at_epoch_ms,
        }

    @classmethod
    def _to_replay_receipt(
        cls,
        row: KnowledgeIndexWorkerDispatchReceiptDB,
    ) -> dict[str, Any]:
        cls._validate_persisted_state(row)
        return {
            **cls._to_authorization_receipt(row),
            "state": row.state,
            "result_digest": row.result_digest,
            "result_payload": (
                dict(row.result_payload)
                if isinstance(row.result_payload, Mapping)
                else None
            ),
            "completed_at_epoch_ms": row.completed_at_epoch_ms,
        }


__all__ = [
    "DISPATCH_RECEIPT_SCHEMA",
    "DISPATCH_STATE_CLAIMED",
    "DISPATCH_STATE_COMPLETED",
    "SqlKnowledgeIndexWorkerDispatchReceiptRepository",
]

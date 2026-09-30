"""In-memory adapters for the speech adaptation admission ports.

Deterministic, lock-guarded implementations used by native/single-Hub
deployments and tests.  Production composition injects SQL-backed adapters.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Mapping

from agent.models.speech_adaptation_admission import (
    SpeechAdaptationDecisionConflict,
    SpeechAdmissionDecision,
    SpeechCapacityLease,
    SpeechPrincipal,
)
from ananta_contracts.speech_adaptation import SpeechAdaptationResult


class InMemorySpeechAdaptationDecisionStore:
    """Compatibility/test adapter; production composition injects SQL."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[SpeechPrincipal, str], SpeechAdmissionDecision] = {}
        self._by_job: dict[tuple[SpeechPrincipal, str], SpeechAdmissionDecision] = {}
        self._lock = threading.RLock()

    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> SpeechAdmissionDecision | None:
        with self._lock:
            return self._by_key.get((principal, idempotency_digest))

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: SpeechAdmissionDecision,
    ) -> tuple[SpeechAdmissionDecision, bool]:
        with self._lock:
            existing = self._by_key.get((principal, idempotency_digest))
            if existing is not None:
                if existing.request_digest != decision.request_digest:
                    raise SpeechAdaptationDecisionConflict("speech_idempotency_conflict")
                return existing, True
            self._by_key[(principal, idempotency_digest)] = decision
            self._by_job[(principal, decision.job_id)] = decision
            return decision, False

    def get(self, principal: SpeechPrincipal, job_id: str) -> SpeechAdmissionDecision | None:
        with self._lock:
            return self._by_job.get((principal, job_id))

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, Mapping[str, Any]] | None:
        with self._lock:
            decision = self._by_job.get((principal, job_id))
            if (
                decision is None
                or decision.status != "queued"
                or decision.job is not None
                or decision.admission_request is None
            ):
                return None
            for (owner, digest), candidate in self._by_key.items():
                if owner == principal and candidate.job_id == job_id:
                    return digest, dict(decision.admission_request)
            return None

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: SpeechAdmissionDecision,
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> SpeechAdmissionDecision:
        del result
        with self._lock:
            current = self._by_job.get((principal, decision.job_id))
            if current is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            if current.status == decision.status and current.reason_code == decision.reason_code:
                return current
            if current.status not in expected_statuses:
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            self._by_job[(principal, decision.job_id)] = decision
            for key, value in tuple(self._by_key.items()):
                if key[0] == principal and value.job_id == decision.job_id:
                    self._by_key[key] = decision
            return decision


class InMemorySpeechCapacityLeasePort:
    """Deterministic bounded lease port for native/single-Hub deployments and tests."""

    def __init__(self, capacity: int = 1, lease_seconds: int = 300) -> None:
        if not 1 <= capacity <= 128 or not 10 <= lease_seconds <= 3600:
            raise ValueError("speech capacity configuration is invalid")
        self._capacity = capacity
        self._lease_ms = lease_seconds * 1000
        self._lock = threading.RLock()
        self._leases: dict[str, SpeechCapacityLease] = {}
        self._epoch = 0

    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None:
        with self._lock:
            self._leases = {key: value for key, value in self._leases.items() if value.expires_at_ms > now_ms}
            existing = self._leases.get(job_id)
            if existing is not None:
                return existing
            if len(self._leases) >= self._capacity:
                return None
            self._epoch += 1
            expires = min(deadline_at_ms, now_ms + self._lease_ms)
            lease = SpeechCapacityLease(
                lease_id=f"speech-lease-{hashlib.sha256(f'{job_id}:{self._epoch}'.encode()).hexdigest()[:32]}",
                epoch=self._epoch,
                expires_at_ms=expires,
            )
            self._leases[job_id] = lease
            return lease

    def release(self, lease_id: str) -> None:
        with self._lock:
            self._leases = {key: value for key, value in self._leases.items() if value.lease_id != lease_id}


__all__ = [
    "InMemorySpeechAdaptationDecisionStore",
    "InMemorySpeechCapacityLeasePort",
]

"""Authenticated v2 runtime-observation ingestion and reducing projection."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.sfu_runtime_capability_evaluator import (
    SfuRuntimeCapabilityEvaluation,
    SfuRuntimeCapabilityEvaluator,
    SfuRuntimeObservationTrust,
)


class SfuRuntimeObservationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SfuRuntimeObservationAcceptance:
    status: str
    observation_id: str
    cursor_version: int


@dataclass(frozen=True, slots=True)
class SfuRuntimeObservationResult:
    status: str
    observation_id: str
    evaluation: SfuRuntimeCapabilityEvaluation


class SfuRuntimeObservationStorePort(Protocol):
    def accept(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        runtime_id: str | None,
        producer_mode: str,
        producer_id: str,
        producer_fencing_token: int,
        boot_id: str,
        sequence: int,
        measured_at_ms: int,
        fresh_until_ms: int,
        payload_digest: str,
    ) -> SfuRuntimeObservationAcceptance: ...


class SfuRuntimeCapabilityProjectionPort(Protocol):
    def reduce_or_stop(
        self,
        *,
        observation_id: str,
        scope: Mapping[str, object],
        producer_fencing_token: int,
        evaluation: SfuRuntimeCapabilityEvaluation,
    ) -> None: ...


class SfuRuntimeObservationService:
    """Stores replay state before projecting only reducing capability facts."""

    def __init__(
        self,
        *,
        evaluator: SfuRuntimeCapabilityEvaluator,
        store: SfuRuntimeObservationStorePort,
        projection: SfuRuntimeCapabilityProjectionPort,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._store = store
        self._projection = projection
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("fleet_health")
    def ingest(
        self,
        document: Mapping[str, object],
        trust: SfuRuntimeObservationTrust,
        *,
        now_ms: int,
    ) -> SfuRuntimeObservationResult:
        if not trust.transport_authenticated:
            raise SfuRuntimeObservationError(
                "sfu_runtime_observation_transport_untrusted", status_code=403
            )
        if (
            document.get("producer_mode") == "authenticated_runtime_extension"
            and not trust.signature_verified
        ):
            raise SfuRuntimeObservationError(
                "sfu_runtime_observation_signature_unverified", status_code=403
            )
        evaluation = self._evaluator.evaluate(document, trust, now_ms=now_ms)
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        if len(canonical) > 65_536:
            raise SfuRuntimeObservationError("sfu_runtime_observation_payload_oversize", status_code=413)
        payload_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        scope = document["scope"]
        try:
            acceptance = self._store.accept(
                tenant_id=str(scope["tenant_id"]),
                cluster_id=str(scope["cluster_id"]),
                runtime_id=None if scope.get("runtime_id") is None else str(scope["runtime_id"]),
                producer_mode=str(document["producer_mode"]),
                producer_id=str(document["producer_id"]),
                producer_fencing_token=int(document["producer_fencing_token"]),
                boot_id=str(document["boot_id"]),
                sequence=int(document["sequence"]),
                measured_at_ms=int(document["measured_at_ms"]),
                fresh_until_ms=evaluation.fresh_until_ms,
                payload_digest=payload_digest,
            )
            # Re-run on duplicates so a transient projection failure is repairable.
            self._projection.reduce_or_stop(
                observation_id=acceptance.observation_id,
                scope=scope,
                producer_fencing_token=int(document["producer_fencing_token"]),
                evaluation=evaluation,
            )
        except SfuRuntimeObservationError:
            raise
        except Exception as exc:
            reason = getattr(exc, "reason_code", "sfu_runtime_observation_store_unavailable")
            raise SfuRuntimeObservationError(str(reason), status_code=503) from exc
        return SfuRuntimeObservationResult(
            status=acceptance.status,
            observation_id=acceptance.observation_id,
            evaluation=evaluation,
        )


__all__ = [
    "SfuRuntimeCapabilityProjectionPort",
    "SfuRuntimeObservationAcceptance",
    "SfuRuntimeObservationError",
    "SfuRuntimeObservationResult",
    "SfuRuntimeObservationService",
    "SfuRuntimeObservationStorePort",
]

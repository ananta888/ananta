"""Bounded Hub projection of versioned broadcast policy to runtime targets."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Mapping

from agent.repositories.sfu_broadcast_flag_projection_repository import (
    SfuBroadcastFlagProjectionCommand,
    SfuBroadcastFlagProjectionRecord,
    SfuBroadcastFlagProjectionRepositoryPort,
    SfuRuntimeProjectionAdmissionState,
)
from agent.services.sfu_broadcast_runtime_control_port import (
    SfuBroadcastRuntimeControlPort,
    SfuRuntimeControlCommand,
)


@dataclass(frozen=True, slots=True)
class SfuBroadcastFlagPropagationPolicy:
    flag_propagation_max_seconds: float = 10.0
    batch_size_max: int = 50
    retry_max: int = 5
    ack_ttl_seconds: float = 30.0
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not 0.1 <= self.flag_propagation_max_seconds <= 120:
            raise ValueError("sfu_flag_propagation_deadline_invalid")
        if not 1 <= self.batch_size_max <= 500 or not 0 <= self.retry_max <= 20:
            raise ValueError("sfu_flag_propagation_bounds_invalid")


@dataclass(frozen=True, slots=True)
class SfuBroadcastProjectionTarget:
    runtime_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    minimum_fencing_token: int = 1


class SfuBroadcastFlagProjectionService:
    def __init__(
        self,
        repository: SfuBroadcastFlagProjectionRepositoryPort,
        runtime_control: SfuBroadcastRuntimeControlPort,
        policy: SfuBroadcastFlagPropagationPolicy | None = None,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._runtime_control = runtime_control
        self._policy = policy or SfuBroadcastFlagPropagationPolicy()
        self._clock = clock

    def project(
        self,
        *,
        tenant_id: str,
        target: SfuBroadcastProjectionTarget,
        flag_version: int,
        cohort_version: int,
        flags: Mapping[str, bool],
    ) -> SfuBroadcastFlagProjectionRecord:
        canonical = {key: value is True for key, value in sorted(flags.items())}
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        immediate_fence = canonical.get("immediate_security_fence") is True
        now = float(self._clock())
        return self._repository.stage(
            SfuBroadcastFlagProjectionCommand(
                tenant_id=tenant_id,
                target_runtime_id=target.runtime_id,
                cluster_id=target.cluster_id,
                region=target.region,
                runtime_control_mode=target.runtime_control_mode,
                flag_version=flag_version,
                cohort_version=cohort_version,
                config_digest=digest,
                config=canonical,
                nonce=secrets.token_urlsafe(24),
                priority=100 if immediate_fence else 10,
                retry_max=self._policy.retry_max,
                ttl_seconds=self._policy.ack_ttl_seconds,
                deadline_at=now + self._policy.flag_propagation_max_seconds,
                minimum_fencing_token=target.minimum_fencing_token,
            ),
            now=now,
        )

    def propagate_once(self) -> dict[str, int]:
        now = float(self._clock())
        due = self._repository.due(limit=self._policy.batch_size_max, now=now)
        accepted = rejected = 0
        for pending in due:
            marked = self._repository.mark_attempt(
                pending.id,
                expected_version=pending.version,
                retry_delay_seconds=self._policy.retry_backoff_seconds * (2 ** min(pending.attempt, 6)),
                now=now,
            )
            result = self._runtime_control.execute(
                SfuRuntimeControlCommand(
                    command_id=marked.id,
                    command_type="immediate_security_fence" if marked.priority >= 100 else "project_flags",
                    target_runtime_id=marked.target_runtime_id,
                    tenant_id=marked.tenant_id,
                    flag_version=marked.flag_version,
                    cohort_version=marked.cohort_version,
                    config_digest=marked.config_digest,
                    nonce=marked.nonce,
                    fencing_token=marked.fencing_token,
                    issued_at=now,
                    deadline_at=marked.deadline_at,
                    payload=marked.config,
                )
            )
            self._repository.record_result(
                marked.id,
                expected_version=marked.version,
                result=result,
                now=float(self._clock()),
            )
            if result.accepted and result.authenticated:
                accepted += 1
            else:
                rejected += 1
        return {"processed": len(due), "accepted": accepted, "rejected": rejected}

    def admission_state(
        self,
        *,
        tenant_id: str,
        target_runtime_id: str,
        flag_version: int,
        cohort_version: int,
    ) -> SfuRuntimeProjectionAdmissionState:
        return self._repository.admission_state(
            tenant_id=tenant_id,
            target_runtime_id=target_runtime_id,
            flag_version=flag_version,
            cohort_version=cohort_version,
            now=float(self._clock()),
        )


__all__ = [
    "SfuBroadcastFlagProjectionService",
    "SfuBroadcastFlagPropagationPolicy",
    "SfuBroadcastProjectionTarget",
]

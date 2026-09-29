from __future__ import annotations

from dataclasses import replace

from agent.repositories.sfu_broadcast_flag_projection_repository import SfuBroadcastFlagProjectionRecord
from agent.services.sfu_broadcast_flag_projection_service import (
    SfuBroadcastFlagProjectionService,
    SfuBroadcastProjectionTarget,
)
from agent.services.sfu_broadcast_runtime_control_port import SfuRuntimeControlResult


class MemoryProjectionRepository:
    def __init__(self):
        self.row = None
        self.result = None

    def stage(self, command, *, now):
        self.row = SfuBroadcastFlagProjectionRecord(
            id="projection-1", fencing_token=command.minimum_fencing_token,
            attempt=0, next_attempt_at=now, status="pending", reason_code=None, version=1,
            **{key: getattr(command, key) for key in (
                "tenant_id", "target_runtime_id", "cluster_id", "region", "runtime_control_mode",
                "flag_version", "cohort_version", "config_digest", "config", "nonce", "priority",
                "retry_max", "ttl_seconds", "deadline_at",
            )},
        )
        return self.row

    def due(self, *, limit, now):
        return (self.row,) if self.row else ()

    def mark_attempt(self, projection_id, *, expected_version, retry_delay_seconds, now):
        self.row = replace(self.row, attempt=1, status="dispatching", version=2)
        return self.row

    def record_result(self, projection_id, *, expected_version, result, now):
        self.result = result
        self.row = replace(self.row, status="acknowledged" if result.accepted else "rejected", version=3)
        return self.row

    def admission_state(self, **kwargs):
        raise NotImplementedError


class AcceptingRuntime:
    def execute(self, command):
        return SfuRuntimeControlResult(
            accepted=True, authenticated=True, reason_code="accepted",
            target_runtime_id=command.target_runtime_id, flag_version=command.flag_version,
            cohort_version=command.cohort_version, config_digest=command.config_digest,
            nonce=command.nonce, fencing_token=command.fencing_token,
            acknowledgement_digest="a" * 64,
        )


def test_security_fence_is_prioritized_and_ack_is_bound_to_fence():
    repository = MemoryProjectionRepository()
    service = SfuBroadcastFlagProjectionService(repository, AcceptingRuntime(), clock=lambda: 100.0)
    staged = service.project(
        tenant_id="tenant", target=SfuBroadcastProjectionTarget("runtime", "cluster", "region", "authenticated_runtime_extension", 9),
        flag_version=4, cohort_version=2, flags={"immediate_security_fence": True},
    )
    assert staged.priority == 100
    assert staged.fencing_token == 9
    assert service.propagate_once() == {"processed": 1, "accepted": 1, "rejected": 0}
    assert repository.result.fencing_token == 9

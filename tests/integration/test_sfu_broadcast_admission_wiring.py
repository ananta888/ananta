from __future__ import annotations

from dataclasses import replace

from agent.repositories.sfu_broadcast_admission_operation_repository import SfuBroadcastAdmissionOperationRecord
from agent.services.sfu_broadcast_admission_saga import (
    SfuBroadcastAdmissionPlan,
    SfuBroadcastAdmissionSaga,
    SfuBroadcastPreparedResource,
)


class Journal:
    def __init__(self):
        self.row = SfuBroadcastAdmissionOperationRecord("op", "tenant", "room", "join", "open", "started", (), {}, {}, {}, None, 200.0, 1)

    def begin(self, command, *, now): return self.row
    def advance(self, operation_id, *, expected_version, step, external_request_id, bindings, now):
        self.row = replace(self.row, current_step=step, applied_steps=(*self.row.applied_steps, step), external_request_ids={**self.row.external_request_ids, step: external_request_id} if external_request_id else self.row.external_request_ids, bindings={**self.row.bindings, **bindings}, version=self.row.version + 1)
        return self.row
    def finish(self, operation_id, *, expected_version, status, reason_code, result_digest, compensation, now):
        self.row = replace(self.row, status=status, reason_code=reason_code, compensation=compensation, version=self.row.version + 1)
        return self.row
    def open(self, *, limit, now): return (self.row,) if self.row.status == "open" else ()


class Ready:
    def require_ready(self, plan): assert plan.runtime_instance_id is None


class Resource:
    def __init__(self, name): self.name, self.compensated = name, []
    def prepare(self, operation_id, plan): return SfuBroadcastPreparedResource(f"{self.name}-id", f"request-{self.name}", {})
    def compensate(self, operation_id, resource_id): self.compensated.append(resource_id)


def test_native_admission_prepares_all_resources_before_completion():
    journal = Journal()
    ports = [Resource(name) for name in ("capacity", "identity", "route")]
    saga = SfuBroadcastAdmissionSaga(journal, Ready(), *ports, clock=lambda: 100.0)
    plan = SfuBroadcastAdmissionPlan(
        "tenant", "room", "actor", "join", "key-12345678", 0, 200.0, 3, 2,
        "cluster", "region", "livekit_control_api", None, 7, 11, 4, 5, 6, {"request": "content-free"},
    )
    prepared = saga.prepare(plan)
    assert prepared.applied_steps == ("placement", "capacity", "identity", "route")
    assert saga.complete(prepared, {"access_token": "not-persisted", "room_id": "room"}).status == "completed"
    assert "access_token" not in journal.row.bindings

from __future__ import annotations

import pytest

from agent.bootstrap.sfu_broadcast_maintenance import (
    initialize_sfu_broadcast_maintenance_jobs,
)
from agent.services.sfu_broadcast_background_job_port import (
    SfuBroadcastBackgroundJobLease,
)
from agent.services.sfu_broadcast_maintenance_jobs import (
    SfuBlindIndexReindexJob,
    SfuCommandOutboxDeliveryJob,
    SfuDigestDestructionPendingJob,
    SfuMaintenanceBatchResult,
    SfuTtlPurgeJob,
)
from agent.services.sfu_broadcast_reconciler_scheduler import SfuBroadcastJobContext


class _RecordingPort:
    def __init__(self) -> None:
        self.calls = []

    def _record(self, request):
        self.calls.append(request)
        return SfuMaintenanceBatchResult(2, "next")

    deliver_pending = _record
    destroy_pending = _record
    reindex_blind_indexes = _record
    purge_expired = _record


def _context(*, current=True):
    lease = SfuBroadcastBackgroundJobLease(
        "job-1", "maintenance", "global", "hub-1", 7, 3,
        120.0, "previous", 5, 2000,
    )
    return SfuBroadcastJobContext(lease, lambda: current)


@pytest.mark.parametrize(
    "job_type",
    (
        SfuCommandOutboxDeliveryJob,
        SfuDigestDestructionPendingJob,
        SfuBlindIndexReindexJob,
        SfuTtlPurgeJob,
    ),
)
def test_maintenance_job_forwards_durable_fence_and_bounds(job_type):
    port = _RecordingPort()
    job = job_type(port=port, clock=lambda: 100.0)

    assert job.run(_context()) == "next"
    request = port.calls[0]
    assert (request.owner_id, request.fencing_token) == ("hub-1", 7)
    assert request.resume_cursor == "previous"
    assert request.batch_size_max == 5
    assert request.runtime_deadline_ms == 2000


def test_maintenance_job_stops_before_port_after_takeover():
    port = _RecordingPort()
    job = SfuTtlPurgeJob(port=port)

    with pytest.raises(RuntimeError, match="sfu_background_job_lease_lost"):
        job.run(_context(current=False))

    assert port.calls == []


def test_maintenance_composition_is_explicitly_not_ready_without_production_ports():
    extensions = {}

    statuses = initialize_sfu_broadcast_maintenance_jobs(extensions)

    assert set(statuses) == {
        "command_outbox_delivery",
        "destruction_pending",
        "blind_index_reindex",
        "ttl_purge",
    }
    assert all(status.ready is False for status in statuses.values())
    assert not any(key.endswith("_job") for key in extensions)

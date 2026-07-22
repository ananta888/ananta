"""Fail-closed composition for Hub-owned SFU maintenance jobs."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, MutableMapping

from agent.services.sfu_broadcast_maintenance_jobs import (
    SfuBlindIndexReindexJob,
    SfuCommandOutboxDeliveryJob,
    SfuDigestDestructionPendingJob,
    SfuTtlPurgeJob,
)


@dataclass(frozen=True, slots=True)
class SfuMaintenanceWiringStatus:
    ready: bool
    reason_code: str


_JOB_BINDINGS = (
    (
        "command_outbox_delivery",
        "sfu_broadcast_command_outbox_delivery_port",
        "deliver_pending",
        "sfu_broadcast_command_outbox_delivery_job",
        SfuCommandOutboxDeliveryJob,
    ),
    (
        "destruction_pending",
        "sfu_member_digest_destruction_pending_port",
        "destroy_pending",
        "sfu_member_digest_destruction_pending_job",
        SfuDigestDestructionPendingJob,
    ),
    (
        "blind_index_reindex",
        "sfu_hub_blind_index_reindex_port",
        "reindex_blind_indexes",
        "sfu_hub_blind_index_reindex_job",
        SfuBlindIndexReindexJob,
    ),
    (
        "ttl_purge",
        "sfu_broadcast_ttl_purge_port",
        "purge_expired",
        "sfu_broadcast_ttl_purge_job",
        SfuTtlPurgeJob,
    ),
)


def initialize_sfu_broadcast_maintenance_jobs(
    extensions: MutableMapping[str, object],
) -> Mapping[str, SfuMaintenanceWiringStatus]:
    """Publish jobs only when their complete durable production port is present."""

    statuses: dict[str, SfuMaintenanceWiringStatus] = {}
    for name, port_key, operation, job_key, job_type in _JOB_BINDINGS:
        port = extensions.get(port_key)
        if not _is_production_port(port, operation):
            extensions.pop(job_key, None)
            statuses[name] = SfuMaintenanceWiringStatus(
                False, f"sfu_{name}_production_port_unavailable",
            )
            continue
        extensions[job_key] = job_type(port=port)
        statuses[name] = SfuMaintenanceWiringStatus(True, "accepted")
    published = MappingProxyType(statuses)
    extensions["sfu_broadcast_maintenance_wiring_status"] = published
    return published


def _is_production_port(candidate: object, operation: str) -> bool:
    if candidate is None or not callable(getattr(candidate, operation, None)):
        return False
    candidate_type = type(candidate)
    lowered_name = candidate_type.__name__.lower()
    module = candidate_type.__module__.lower()
    return (
        not any(marker in lowered_name for marker in ("fake", "mock", "stub", "memory"))
        and not module.startswith(("test", "tests"))
        and ".testing" not in module
    )


__all__ = [
    "SfuMaintenanceWiringStatus",
    "initialize_sfu_broadcast_maintenance_jobs",
]

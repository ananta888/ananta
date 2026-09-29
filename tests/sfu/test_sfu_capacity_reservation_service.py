from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from agent.db_models import (
    SfuCapacityLedgerDB,
    SfuCapacityReservationDB,
    SfuCapacityReservationMutationDB,
)
from agent.repositories.sfu_capacity_reservation_repository import (
    SfuCapacityReservationError,
    SfuResourceVector,
    SqlSfuCapacityReservationRepository,
)
from agent.services.sfu_capacity_reservation_service import (
    SfuCapacityLimitSet,
    SfuCapacityReservationPolicy,
    SfuCapacityReservationRequest,
    SfuCapacityReservationService,
)


NOW = 1000.0


def vector(receivers=1):
    return SfuResourceVector(
        cpu_millicores=100,
        memory_bytes=1024,
        fd_count=2,
        ingress_bps=1000,
        egress_bps=2000,
        receivers=receivers,
        tracks=2,
        turn_bps=0,
    )


def policy(cluster_receivers=25, tenant_receivers=5):
    cluster = replace(vector(cluster_receivers), receivers=cluster_receivers)
    tenant = replace(vector(tenant_receivers), receivers=tenant_receivers)
    cluster = SfuResourceVector(
        **{name: max(getattr(cluster, name), 1_000_000) for name in cluster.payload()}
        | {"receivers": cluster_receivers}
    )
    tenant = SfuResourceVector(
        **{name: max(getattr(tenant, name), 1_000_000) for name in tenant.payload()}
        | {"receivers": tenant_receivers}
    )
    return SfuCapacityReservationPolicy(
        enabled=True,
        profile_limits={"single-region": SfuCapacityLimitSet(cluster, tenant)},
    )


def request(room="room-1", tenant="tenant-1", command="cmd-1", **changes):
    value = SfuCapacityReservationRequest(
        command_id=command,
        operation="create",
        tenant_id=tenant,
        room_id=room,
        cluster_id="cluster-1",
        region="eu-central",
        runtime_control_mode="livekit_control_api",
        placement_owner="livekit_native",
        observed_node_id=None,
        runtime_instance_id=None,
        infrastructure_profile_id="infra-1",
        slo_profile_id="single-region",
        resources=vector(),
        lease_ttl_seconds=30,
        directory_version=1,
        expected_version=0,
        observation_expires_at=NOW + 10,
        target_admission_ready=True,
        compatible=True,
    )
    return replace(value, **changes)


@pytest.fixture
def service(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'capacity.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuCapacityLedgerDB.__table__,
            SfuCapacityReservationDB.__table__,
            SfuCapacityReservationMutationDB.__table__,
        ],
    )
    repository = SqlSfuCapacityReservationRepository(
        db_engine=engine, clock=lambda: NOW
    )
    return SfuCapacityReservationService(
        repository, policy(), clock=lambda: NOW
    ), repository


def test_create_renew_resize_release_are_idempotent_and_fenced(service):
    capacity, repository = service
    created = capacity.change(request())
    replay = capacity.change(request())
    assert replay.replayed is True
    assert replay.record == created.record

    renewed_request = request(
        command="cmd-2",
        operation="renew",
        expected_version=created.record.version,
    )
    renewed = capacity.change(renewed_request)
    assert renewed.record.fencing_token > created.record.fencing_token

    resized = capacity.change(
        request(
            command="cmd-3",
            operation="resize",
            expected_version=renewed.record.version,
            resources=vector(receivers=2),
            directory_version=2,
        )
    )
    assert resized.record.resources.receivers == 2

    released = capacity.change(
        request(
            command="cmd-4",
            operation="release",
            expected_version=resized.record.version,
            resources=SfuResourceVector(),
            observation_expires_at=0,
            target_admission_ready=False,
            directory_version=2,
        )
    )
    assert released.record.status == "released"
    assert repository.reconcile_expired(now=NOW + 100) == 0


def test_stale_growth_native_node_selection_and_negative_inputs_fail_closed(service):
    capacity, _ = service
    with pytest.raises(SfuCapacityReservationError, match="sfu_capacity_observation_stale"):
        capacity.change(request(observation_expires_at=NOW))
    with pytest.raises(
        SfuCapacityReservationError, match="sfu_capacity_native_node_selection_forbidden"
    ):
        capacity.change(request(runtime_instance_id="invented-node"))
    with pytest.raises(ValueError, match="sfu_capacity_resource_invalid"):
        SfuResourceVector(receivers=-1)


def test_one_hundred_parallel_attempts_respect_hard_limit_and_tenant_fairness(service):
    capacity, _ = service

    def reserve(index):
        try:
            return capacity.change(
                request(
                    room=f"room-{index}",
                    tenant=f"tenant-{index % 10}",
                    command=f"parallel-{index}",
                )
            ).record
        except SfuCapacityReservationError:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = [item for item in pool.map(reserve, range(100)) if item is not None]
    assert len(accepted) <= 25
    by_tenant = {}
    for item in accepted:
        by_tenant[item.tenant_id] = by_tenant.get(item.tenant_id, 0) + 1
    assert all(value <= 5 for value in by_tenant.values())


def test_capacity_mutation_receipts_are_ttl_bounded(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'capacity-ttl.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuCapacityLedgerDB.__table__,
            SfuCapacityReservationDB.__table__,
            SfuCapacityReservationMutationDB.__table__,
        ],
    )
    repository = SqlSfuCapacityReservationRepository(
        db_engine=engine,
        clock=lambda: NOW,
        mutation_retention_seconds=60,
    )
    capacity = SfuCapacityReservationService(repository, policy(), clock=lambda: NOW)
    capacity.change(request())
    assert repository.purge_expired_mutations(now=NOW + 59) == 0
    assert repository.purge_expired_mutations(now=NOW + 60) == 1

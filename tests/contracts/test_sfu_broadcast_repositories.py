from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models import SfuBroadcastAudienceDB, SfuFanoutRouteDB, SfuReceiverGroupDB
from agent.repositories.sfu_broadcast_repository import (
    InMemorySfuBroadcastAudienceRepository,
    InMemorySfuBroadcastRepositoryStore,
    InMemorySfuFanoutRouteRepository,
    InMemorySfuReceiverGroupRepository,
    SfuBroadcastRepositoryError,
    SqlSfuBroadcastAudienceRepository,
    SqlSfuFanoutRouteRepository,
    SqlSfuReceiverGroupRepository,
)
from agent.services.sfu_broadcast_repository_ports import (
    SfuBroadcastAudience,
    SfuBroadcastAudienceRepositoryPort,
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuFanoutRouteRepositoryPort,
    SfuProjectionMutation,
    SfuReceiverGroup,
    SfuReceiverGroupRepositoryPort,
)


DIGEST = "a" * 64
SCOPE = SfuBroadcastRoomScope("tenant-a", "room-a")


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@dataclass
class RepositoryHarness:
    audience: SfuBroadcastAudienceRepositoryPort
    group: SfuReceiverGroupRepositoryPort
    route: SfuFanoutRouteRepositoryPort
    restart: callable
    clock: Clock


@pytest.fixture(params=("memory", "sql"))
def repositories(request, tmp_path) -> RepositoryHarness:
    clock = Clock()
    if request.param == "memory":
        store = InMemorySfuBroadcastRepositoryStore()

        def build():
            return (
                InMemorySfuBroadcastAudienceRepository(store=store, page_size_max=2, clock=clock),
                InMemorySfuReceiverGroupRepository(store=store, page_size_max=2, clock=clock),
                InMemorySfuFanoutRouteRepository(store=store, page_size_max=2, clock=clock),
            )
    else:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'sfu-broadcast-repositories.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        SQLModel.metadata.create_all(
            engine,
            tables=[
                SfuBroadcastAudienceDB.__table__,
                SfuReceiverGroupDB.__table__,
                SfuFanoutRouteDB.__table__,
            ],
        )

        def build():
            return (
                SqlSfuBroadcastAudienceRepository(db_engine=engine, page_size_max=2, clock=clock),
                SqlSfuReceiverGroupRepository(db_engine=engine, page_size_max=2, clock=clock),
                SqlSfuFanoutRouteRepository(db_engine=engine, page_size_max=2, clock=clock),
            )

    audience, group, route = build()
    return RepositoryHarness(
        audience,
        group,
        route,
        restart=lambda: RepositoryHarness(*build(), restart=None, clock=clock),
        clock=clock,
    )


def _common(identifier: str, *, tenant: str = "tenant-a", room: str = "room-a") -> dict:
    return {
        "id": identifier,
        "tenant_id": tenant,
        "session_id": room,
        "room_state_id": f"state-{tenant}-{room}",
        "room_state_revision": 3,
        "status": "active",
        "ttl_seconds": 100,
        "retention_seconds": 100,
        "retention_status": "live",
        "expires_at": 1_100.0,
        "retain_until": 1_200.0,
        "tombstoned_at": None,
        "tombstone_reason": None,
        "fencing_token": 3,
        "version": 1,
        "audit_actor_ref": "hub:test",
        "audit_reason": "contract",
        "request_digest": DIGEST,
        "idempotency_key_digest": "b" * 64,
        "created_at": 900.0,
        "updated_at": 900.0,
        "audited_at": 900.0,
    }


def _audience(
    identifier: str = "audience-a",
    *,
    tenant: str = "tenant-a",
    room: str = "room-a",
    suffix: str = "a",
) -> SfuBroadcastAudience:
    return SfuBroadcastAudience(
        **_common(identifier, tenant=tenant, room=room),
        audience_ref=f"audience-{suffix}",
        publication_ref=f"publication-{suffix}",
        audience_digest="c" * 64,
        policy_digest="d" * 64,
        membership_digest="e" * 64,
        policy_epoch=3,
        membership_epoch=3,
        key_epoch=3,
    )


def _group(
    identifier: str = "group-b",
    *,
    tenant: str = "tenant-a",
    room: str = "room-a",
    suffix: str = "b",
) -> SfuReceiverGroup:
    return SfuReceiverGroup(
        **_common(identifier, tenant=tenant, room=room),
        receiver_group_ref=f"group-{suffix}",
        subscription_ref=f"subscription-{suffix}",
        group_digest="f" * 64,
        membership_digest="1" * 64,
        key_digest="2" * 64,
        membership_epoch=3,
        key_epoch=3,
        topology_epoch=3,
    )


def _route(
    identifier: str = "route-a-b",
    *,
    tenant: str = "tenant-a",
    room: str = "room-a",
    suffix: str = "a-b",
    audience_id: str = "audience-a",
    group_id: str = "group-b",
    publication: str = "publication-a",
    subscription: str = "subscription-b",
) -> SfuFanoutRoute:
    return SfuFanoutRoute(
        **_common(identifier, tenant=tenant, room=room),
        route_ref=f"route-{suffix}",
        audience_projection_id=audience_id,
        receiver_group_projection_id=group_id,
        publication_ref=publication,
        subscription_ref=subscription,
        route_digest="3" * 64,
        policy_digest="4" * 64,
        membership_digest="5" * 64,
        key_digest="6" * 64,
        policy_epoch=3,
        membership_epoch=3,
        key_epoch=3,
        route_epoch=3,
        topology_epoch=3,
    )


def _save(repository, value, *, version=0, key="create"):
    return repository.save(
        SfuProjectionMutation(value, expected_version=version, idempotency_key=key)
    )


def test_audience_contract_retry_conflict_stale_expiry_and_restart(repositories) -> None:
    repository = repositories.audience
    value = _audience()
    created = _save(repository, value)
    assert created.status == "saved" and created.value.version == 1

    replay = _save(repository, value)
    assert replay.status == "saved" and replay.replayed and replay.value == created.value
    idempotency_conflict = _save(repository, replace(value, request_digest="9" * 64))
    assert idempotency_conflict.status == "conflict"

    updated_value = replace(value, policy_epoch=4, request_digest="8" * 64)
    updated = _save(repository, updated_value, version=1, key="update")
    assert updated.status == "saved" and updated.value.version == 2
    conflict = _save(repository, replace(updated_value, request_digest="7" * 64), version=1, key="loser")
    assert conflict.status == "conflict"
    stale = _save(
        repository,
        replace(updated_value, policy_epoch=2, request_digest="6" * 64),
        version=2,
        key="stale",
    )
    assert stale.status == "stale_epoch"
    assert _save(repository, _audience("missing", suffix="z"), version=4).status == "not_found"

    restarted = repositories.restart().audience
    assert restarted.get(SCOPE, value.id).version == 2
    repositories.clock.value = 1_101.0
    expired = restarted.save(
        SfuProjectionMutation(
            replace(updated.value, request_digest="5" * 64),
            expected_version=2,
            idempotency_key="after-expiry",
        )
    )
    assert expired.status == "expired"
    marked = restarted.expire(SCOPE, value.id, expected_version=2, idempotency_key="expire")
    assert marked.status == "saved" and marked.value.status == "expired"


def test_group_and_route_ports_are_focused_and_substitutable(repositories) -> None:
    assert isinstance(repositories.audience, SfuBroadcastAudienceRepositoryPort)
    assert isinstance(repositories.group, SfuReceiverGroupRepositoryPort)
    assert isinstance(repositories.route, SfuFanoutRouteRepositoryPort)
    assert not hasattr(repositories.audience, "register_node")
    assert not hasattr(repositories.group, "mint_token")
    assert not hasattr(repositories.route, "record_metrics")

    audience = _save(repositories.audience, _audience())
    group = _save(repositories.group, _group())
    assert audience.committed and group.committed
    route = _save(repositories.route, _route())
    assert route.status == "saved"

    stale_route = _save(
        repositories.route,
        replace(_route(), route_epoch=2, request_digest="7" * 64),
        version=1,
        key="route-stale",
    )
    assert stale_route.status == "stale_epoch"
    orphan = _save(
        repositories.route,
        _route(
            "route-orphan",
            suffix="orphan",
            audience_id="missing-audience",
            publication="publication-orphan",
        ),
    )
    assert orphan.status == "not_found"


def test_all_pages_are_sorted_bounded_room_scoped_and_restart_safe(repositories) -> None:
    for suffix in ("c", "a", "b"):
        assert _save(
            repositories.audience,
            _audience(f"audience-{suffix}", suffix=suffix),
            key=f"audience-{suffix}",
        ).committed
        assert _save(
            repositories.group,
            _group(f"group-{suffix}", suffix=suffix),
            key=f"group-{suffix}",
        ).committed
    assert _save(
        repositories.audience,
        _audience("audience-other", tenant="tenant-other", suffix="other"),
        key="other-tenant",
    ).committed

    restarted = repositories.restart()
    for repository, attribute in (
        (restarted.audience, "audience_ref"),
        (restarted.group, "receiver_group_ref"),
    ):
        first = repository.page(SCOPE, page_size=2)
        second = repository.page(SCOPE, page_size=2, cursor=first.next_cursor)
        values = (*first.items, *second.items)
        assert [getattr(value, attribute) for value in values] == sorted(
            getattr(value, attribute) for value in values
        )
        assert len(values) == 3
        assert all(value.tenant_id == "tenant-a" and value.session_id == "room-a" for value in values)
        with pytest.raises(SfuBroadcastRepositoryError, match="projection_page_size_invalid"):
            repository.page(SCOPE, page_size=3)

    assert restarted.audience.get(
        SfuBroadcastRoomScope("tenant-other", "room-a"),
        "audience-a",
    ) is None


def test_expiry_reconciliation_and_retention_use_existing_ports(repositories) -> None:
    stale = replace(
        _audience(),
        room_state_revision=2,
        expires_at=950.0,
        retain_until=975.0,
        status="expired",
        retention_status="retained",
    )
    assert _save(repositories.audience, stale).committed

    reconciliation = repositories.audience.page_reconciliation(
        SCOPE,
        current_room_state_revision=3,
        page_size=2,
    )
    retention = repositories.audience.page_retention_due(
        SCOPE,
        now=1_000.0,
        page_size=2,
    )
    assert [value.id for value in reconciliation.items] == [stale.id]
    assert [value.id for value in retention.items] == [stale.id]
    assert isinstance(repositories.audience, SfuBroadcastAudienceRepositoryPort)

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from agent.repositories.sfu_broadcast_repository import (
    InMemorySfuAtomicGroupProjectionRepository,
    InMemorySfuBroadcastAudienceRepository,
    InMemorySfuBroadcastRepositoryStore,
)
from agent.services.sfu_broadcast_repository_ports import (
    SfuBroadcastAudience,
    SfuProjectionMutation,
)
from agent.services.sfu_group_projection_service import (
    SfuGroupProjectionCommand,
    SfuGroupProjectionService,
)
from agent.services.sfu_receiver_group_projector import ProjectedReceiverGroup


NOW = 1_000.0


def audience():
    return SfuBroadcastAudience(
        "audience-a", "tenant-a", "room-a", "room-state-a", 3, "active", 100,
        100, "live", 1_100.0, 1_200.0, None, None, 1, 1, "hub:test", "test",
        "a" * 64, "0" * 64, NOW, NOW, NOW, "audience-ref", "publication-a",
        "b" * 64, "c" * 64, "d" * 64, 3, 4, 5,
    )


def group(suffix="a"):
    return ProjectedReceiverGroup(
        f"group-{suffix}", f"subscription-{suffix}", "publication-a", "team", "medium",
        (f"receiver-{suffix}",), "e" * 64, "f" * 64, "1" * 64, 4, 5, 2,
    )


def command(suffix="a", *, fence=1, key="create", version=0):
    return SfuGroupProjectionCommand(
        "projection-a", "audience-a", "tenant-a", "room-a", "room-state-a", 3,
        group(suffix), 3, version, fence, 50, 50, key, "hub:test", "race", NOW,
    )


def test_two_repository_instances_have_exactly_one_join_race_winner() -> None:
    store = InMemorySfuBroadcastRepositoryStore()
    parent = InMemorySfuBroadcastAudienceRepository(store=store, clock=lambda: NOW)
    assert parent.save(SfuProjectionMutation(audience(), 0, "parent"), now=NOW).committed
    repositories = (
        InMemorySfuAtomicGroupProjectionRepository(store=store, clock=lambda: NOW),
        InMemorySfuAtomicGroupProjectionRepository(store=store, clock=lambda: NOW),
    )
    services = tuple(SfuGroupProjectionService(repository=item) for item in repositories)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda pair: pair[0].project(pair[1]), zip(services, (command("a", fence=1, key="hub-a"), command("b", fence=2, key="hub-b")))))
    assert sum(outcome.committed for outcome in outcomes) == 1
    assert {outcome.status for outcome in outcomes} == {"saved", "conflict"}


def test_retry_is_same_version_and_stale_epoch_or_fence_cannot_overwrite() -> None:
    store = InMemorySfuBroadcastRepositoryStore()
    parent_repo = InMemorySfuBroadcastAudienceRepository(store=store, clock=lambda: NOW)
    saved_parent = parent_repo.save(SfuProjectionMutation(audience(), 0, "parent"), now=NOW)
    repository = InMemorySfuAtomicGroupProjectionRepository(store=store, clock=lambda: NOW)
    service = SfuGroupProjectionService(repository=repository)
    created = service.project(command())
    replay = service.project(command())
    assert created.committed and replay.replayed
    assert replay.value.version == created.value.version == 1
    stale_fence = service.project(command("b", fence=1, key="stale-fence", version=1))
    assert stale_fence.status == "stale_epoch"
    parent_repo.save(
        SfuProjectionMutation(replace(saved_parent.value, policy_epoch=4, request_digest="9" * 64), 1, "policy-change"),
        now=NOW,
    )
    stale_policy = service.project(command("b", fence=2, key="stale-policy", version=1))
    assert stale_policy.status == "stale_epoch"

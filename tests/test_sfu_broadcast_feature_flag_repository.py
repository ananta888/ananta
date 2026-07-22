from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models import SfuBroadcastFeatureFlagDB, SfuBroadcastFeatureFlagMutationDB
from agent.repositories.sfu_broadcast_feature_flag_repository import (
    InMemorySfuBroadcastFeatureFlagRepository,
    InMemorySfuBroadcastFeatureFlagStore,
    SfuBroadcastFeatureFlagMutation,
    SfuBroadcastFeatureFlagRepositoryError,
    SfuBroadcastFeatureFlagScope,
    SqlSfuBroadcastFeatureFlagRepository,
)


def _mutation(
    scope: SfuBroadcastFeatureFlagScope,
    *,
    flag: str = "semantic_media_broadcast",
    enabled: bool = True,
    stage: str = "cohort",
    key: str = "mutation-1",
    reason: str = "approved rollout",
) -> SfuBroadcastFeatureFlagMutation:
    return SfuBroadcastFeatureFlagMutation(
        scope=scope,
        flag=flag,
        enabled=enabled,
        rollout_stage=stage,
        actor="hub-policy",
        reason=reason,
        idempotency_key=key,
    )


@pytest.fixture(params=("memory", "sql"))
def repository_factory(request, tmp_path):
    if request.param == "memory":
        store = InMemorySfuBroadcastFeatureFlagStore()
        return lambda: InMemorySfuBroadcastFeatureFlagRepository(store=store, clock=lambda: 1_000.0)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'broadcast-flags.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuBroadcastFeatureFlagDB.__table__,
            SfuBroadcastFeatureFlagMutationDB.__table__,
        ],
    )
    return lambda: SqlSfuBroadcastFeatureFlagRepository(db_engine=engine, clock=lambda: 1_000.0)


def test_repository_contract_create_cas_replay_restart_and_cross_tenant(repository_factory) -> None:
    scope = SfuBroadcastFeatureFlagScope("tenant-a", "eu-central", "rooms-01")
    repository = repository_factory()
    create = _mutation(scope)
    created = repository.create(create, expected_version=0)
    assert created.status == "created"
    assert created.state is not None and created.state.version == 1
    assert created.state.idempotency_key_digest != create.idempotency_key

    replayed = repository.create(create, expected_version=0)
    assert replayed.status == "replayed" and replayed.state == created.state
    with pytest.raises(SfuBroadcastFeatureFlagRepositoryError, match="feature_flag_idempotency_conflict"):
        repository.create(_mutation(scope, enabled=False), expected_version=0)

    updated = repository.compare_and_swap(
        _mutation(scope, enabled=False, stage="paused", key="mutation-2"),
        expected_version=1,
    )
    assert updated.status == "updated"
    assert updated.state is not None and updated.state.version == 2
    stale = repository.compare_and_swap(
        _mutation(scope, stage="tenant", key="stale-writer"),
        expected_version=1,
    )
    assert stale.status == "conflict"

    restarted = repository_factory()
    snapshot = restarted.snapshot(scope)
    assert snapshot.available and not snapshot.enabled("semantic_media_broadcast")
    assert snapshot.flags["semantic_media_broadcast"].version == 2

    other_scope = SfuBroadcastFeatureFlagScope("tenant-b", "eu-central", "rooms-01")
    other = restarted.create(_mutation(other_scope), expected_version=0)
    assert other.status == "created"
    assert restarted.snapshot(other_scope).enabled("semantic_media_broadcast")
    assert not restarted.snapshot(scope).enabled("semantic_media_broadcast")


def test_repository_contract_security_latches_are_monotone(repository_factory, monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_IMMEDIATE_SECURITY_FENCE", "false")
    repository = repository_factory()
    scope = SfuBroadcastFeatureFlagScope("tenant-fenced", "eu-central", "rooms-fenced")
    for index, flag in enumerate(("immediate_security_fence", "stop_admission"), start=1):
        created = repository.create(
            _mutation(scope, flag=flag, key=f"fence-create-{index}"),
            expected_version=0,
        )
        assert created.status == "created"
        reset = repository.compare_and_swap(
            _mutation(scope, flag=flag, enabled=False, stage="off", key=f"fence-reset-{index}"),
            expected_version=1,
        )
        assert reset.status == "conflict"
        assert reset.reason_code == "feature_flag_security_latch_monotone"
        assert repository.snapshot(scope).enabled(flag)


def test_repository_contract_pagination_is_stable_and_tenant_scoped(repository_factory) -> None:
    repository = repository_factory()
    scope = SfuBroadcastFeatureFlagScope("tenant-page", "eu-central", "rooms-page")
    for index, flag in enumerate(("alpha_flag", "beta_flag", "gamma_flag"), start=1):
        assert repository.create(
            _mutation(scope, flag=flag, key=f"page-{index}"),
            expected_version=0,
        ).committed
    repository.create(
        _mutation(SfuBroadcastFeatureFlagScope("tenant-other"), flag="hidden_flag", key="other-page"),
        expected_version=0,
    )

    first = repository.page("tenant-page", limit=2)
    second = repository.page("tenant-page", limit=2, cursor=first.next_cursor)
    assert first.available and first.next_cursor is not None
    assert [item.flag for item in (*first.items, *second.items)] == [
        "alpha_flag",
        "beta_flag",
        "gamma_flag",
    ]
    assert second.next_cursor is None
    assert all(item.scope.tenant_id == "tenant-page" for item in (*first.items, *second.items))


def test_repository_contract_concurrent_writer_has_single_winner(repository_factory) -> None:
    scope = SfuBroadcastFeatureFlagScope("tenant-race", "eu-central", "rooms-race")
    assert repository_factory().create(_mutation(scope, enabled=False), expected_version=0).committed

    def update(key: str, stage: str):
        return repository_factory().compare_and_swap(
            _mutation(scope, enabled=True, stage=stage, key=key),
            expected_version=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda args: update(*args), (("race-a", "cohort-a"), ("race-b", "cohort-b"))))
    assert sorted(result.status for result in outcomes) == ["conflict", "updated"]
    state = repository_factory().snapshot(scope).flags["semantic_media_broadcast"]
    assert state.version == 2 and state.enabled


def test_unavailable_stores_fail_closed_even_when_static_environment_is_true(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_SEMANTIC_MEDIA_BROADCAST_ENABLED", "true")
    scope = SfuBroadcastFeatureFlagScope("tenant-closed")

    store = InMemorySfuBroadcastFeatureFlagStore()
    memory = InMemorySfuBroadcastFeatureFlagRepository(store=store)
    memory.set_available(False)
    assert not memory.snapshot(scope).available
    assert not memory.snapshot(scope).enabled("semantic_media_broadcast")

    missing_parent = tmp_path / "missing" / "flags.db"
    sql = SqlSfuBroadcastFeatureFlagRepository(
        db_engine=create_engine(f"sqlite:///{missing_parent}")
    )
    snapshot = sql.snapshot(scope)
    assert not snapshot.available
    assert not snapshot.enabled("semantic_media_broadcast")

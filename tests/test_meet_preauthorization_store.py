"""Real private SQL revisions, burned allowances and no content-bearing audit."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select

from agent.repositories.meet_preauthorizations import SqlMeetPreauthorizations, dispatches, events, policies
from agent.services.meet_contract import MeetError
from tests.test_meet_preauthorization_policy import assignment, document

pytestmark = pytest.mark.timeout(20)
OPERATOR = "local-uid:1000"


@pytest.fixture
def store(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "policy.sqlite"), connect_args={"timeout": 3})
    result = SqlMeetPreauthorizations(engine, clock=lambda: 100)
    result.initialize()
    yield result
    engine.dispose()


def test_provision_reserve_restart_and_revoke_without_dispatch_or_room_contents_in_audit(store):
    receipt = store.provision(document(), 0, OPERATOR)
    assert receipt["revision"] == 1 and receipt["status"] == "active"
    assert receipt["dispatch_started"] is receipt["trust_activated"] is False
    binding = store.reserve(assignment())
    restarted = SqlMeetPreauthorizations(store.engine, clock=lambda: 101)
    restarted.require_current(assignment(), binding)
    revoked = restarted.revoke(document()["policy_id"], 1, OPERATOR)
    assert revoked["revision"] == 2 and revoked["status"] == "revoked"
    with pytest.raises(MeetError, match="binding_denied"):
        store.require_current(assignment(), binding)
    with store.engine.connect() as connection:
        audit = [dict(row) for row in connection.execute(select(events)).mappings()]
        assert len(audit) == 2
        assert "room-" not in str(audit) and document()["origin"] not in str(audit)
        assert all(row["operator"] == OPERATOR and len(row["document_digest"]) == 64 for row in audit)


def test_policy_revision_cas_cannot_reset_budget_on_stale_retry_or_revive_old_dispatch(store):
    store.provision(document(), 0, OPERATOR)
    binding = store.reserve(assignment())
    for action in (
        lambda: store.provision(document(), 0, OPERATOR),
        lambda: store.revoke("synthetic-policy", 2, OPERATOR),
    ):
        with pytest.raises(MeetError, match="conflict"):
            action()
    assert store.provision(document() | {"expires_at": 1100}, 1, OPERATOR)["revision"] == 2
    with pytest.raises(MeetError, match="binding_denied"):
        store.require_current(assignment(), binding)
    with pytest.raises(MeetError, match="dispatch_replayed"):
        store.reserve(assignment())
    fresh = assignment() | {"task_id": "fresh-task", "lease_id": "fresh-lease", "runtime_id": "fresh-runtime"}
    current = store.reserve(fresh)
    assert current["revision"] == 2
    store.require_current(fresh, current)


def test_failed_or_uncertain_consumer_cannot_reclaim_reserved_dispatch_budget(store):
    store.provision(document() | {"max_dispatches": 1}, 0, OPERATOR)
    store.reserve(assignment())  # Simulate failure before Task ingestion.
    with pytest.raises(MeetError, match="exhausted"):
        store.reserve(assignment() | {"task_id": "other"})
    with store.engine.connect() as connection:
        assert connection.execute(select(policies.c.used_dispatches)).scalar_one() == 1
        assert len(connection.execute(select(dispatches)).all()) == 1


@pytest.mark.parametrize("field", ["task_id", "lease_id", "runtime_id", "owner_subject", "room_id", "parent_task_id"])
def test_pointer_cannot_be_transplanted_to_foreign_task_or_scope(store, field):
    store.provision(document(), 0, OPERATOR)
    binding = store.reserve(assignment())
    with pytest.raises(MeetError):
        store.require_current(assignment() | {field: "other"}, binding)


@pytest.mark.parametrize("clock", [99, 400, 1000, float("nan"), True])
def test_current_and_reserved_authority_recheck_clock(store, clock):
    store.provision(document(), 0, OPERATOR)
    binding = store.reserve(assignment())
    store.clock = lambda: clock
    with pytest.raises(MeetError):
        store.require_current(assignment(), binding)
    with pytest.raises(MeetError):
        store.reserve(assignment() | {"task_id": "other"})


def test_concurrent_hubs_cannot_overbook_single_dispatch_allowance(store):
    store.provision(document() | {"max_dispatches": 1}, 0, OPERATOR)
    barrier = Barrier(2)

    def reserve(index):
        replica = SqlMeetPreauthorizations(store.engine, clock=lambda: 100)
        barrier.wait(timeout=3)
        try:
            return replica.reserve(assignment() | {"task_id": f"task-{index}"})
        except MeetError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert "meet_preauthorization_inactive_or_exhausted" in results


def test_invalid_operator_identity_rolls_back_policy_and_audit(store):
    with pytest.raises(MeetError, match="operator_invalid"):
        store.provision(document(), 0, "caller-asserted-admin")
    with store.engine.connect() as connection:
        assert connection.execute(select(policies)).first() is None
        assert connection.execute(select(events)).first() is None


def test_waiting_provision_cannot_commit_a_policy_that_expired_during_transaction(store):
    moments = iter([100, 1000])
    store.clock = lambda: next(moments)
    with pytest.raises(MeetError, match="expired"):
        store.provision(document(), 0, OPERATOR)
    with store.engine.connect() as connection:
        assert connection.execute(select(policies)).first() is None
        assert connection.execute(select(events)).first() is None

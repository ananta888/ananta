"""Headless deadline cleanup, cursor fairness and current-authority races."""

from copy import deepcopy

import pytest

from agent.services.meet_dialog_deadlines import MeetDialogDeadlines, original_deadline


def candidate(task_id="a", deadline=100, *, audio=False):
    context = {"meet_dialog": {"lease_id": "dispatch", "runtime_id": "runtime", "deadline": deadline}}
    if audio:
        context = {
            "meet_audio": {"task_id": task_id, "lease_id": "audio-dispatch", "deadline": deadline},
            "parent_dispatch": "dispatch",
            "runtime_id": "runtime",
        }
    return {
        "task_id": task_id,
        "task_kind": "meet_audio_receive" if audio else "meet_dialog_session",
        "tenant_id": "synthetic-tenant",
        "project_id": "synthetic-project",
        "parent_task_id": "parent" if audio else None,
        "context": context,
    }


class Store:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.settled = []
        self.before_settle = lambda: None

    def page(self, after, limit):
        return deepcopy([r for r in self.rows if after is None or r["task_id"] > after][:limit])

    def settle(self, row, still_expired):
        self.before_settle()
        if not still_expired() or row not in self.rows:
            return False
        self.rows.remove(row)
        self.settled.append(row)
        return True


def test_keyset_deletion_does_not_skip_children_or_later_rows_and_resets():
    store = Store([candidate("a"), candidate("b", audio=True), candidate("c", 200), candidate("d")])
    service = MeetDialogDeadlines(store, clock=lambda: 100)
    assert service.run_once(limit=2)["settled"] == 2
    assert service.run_once(limit=2) == {"scanned": 2, "settled": 1, "live": 1, "invalid": 0, "conflict": 0}
    assert service.run_once(limit=2)["scanned"] == 0
    assert service.cursor is None
    store.rows.insert(0, candidate("0"))
    assert service.run_once(limit=2)["settled"] == 1
    assert [r["task_id"] for r in store.settled] == ["a", "b", "d", "0"]


@pytest.mark.parametrize(
    "field,value",
    [("task_kind", []), ("task_kind", "other"), ("tenant_id", None), ("project_id", ""), ("context", None)],
)
def test_malformed_candidate_never_blocks_later_valid_task(field, value):
    bad = candidate() | {field: value}
    store = Store([bad, candidate("b")])
    result = MeetDialogDeadlines(store, clock=lambda: 100).run_once()
    assert result["invalid"] == 1 and result["settled"] == 1


@pytest.mark.parametrize("key", ["", "a" * 192])
def test_invalid_persisted_identifier_is_only_a_cursor_not_settlement_authority(key):
    service = MeetDialogDeadlines(Store([candidate(key), candidate("b")]), clock=lambda: 100)
    assert service.run_once(limit=1)["invalid"] == 1
    assert service.run_once(limit=1)["settled"] == 1


@pytest.mark.parametrize("deadline", [True, None, 0, -1, 2**53, 100.0, "100"])
def test_deadline_has_no_coercion_or_default(deadline):
    with pytest.raises(ValueError, match="binding_invalid"):
        original_deadline(candidate(deadline=deadline))


@pytest.mark.parametrize("field", ["parent_dispatch", "runtime_id"])
def test_child_requires_parent_dispatch_identity(field):
    row = candidate(audio=True)
    del row["context"][field]
    with pytest.raises(ValueError, match="binding_invalid"):
        original_deadline(row)


@pytest.mark.parametrize("race", ["clock", "shutdown", "replacement"])
def test_expiry_is_rechecked_inside_settlement(race):
    state = {"now": 100, "stopped": False}
    store = Store([candidate()])

    def change():
        if race == "clock":
            state["now"] = 99
        elif race == "shutdown":
            state["stopped"] = True
        else:
            store.rows[0]["context"]["meet_dialog"]["lease_id"] = "replacement"

    store.before_settle = change
    result = MeetDialogDeadlines(store, clock=lambda: state["now"]).run_once(stopped=lambda: state["stopped"])
    assert result["conflict"] == 1 and not store.settled


@pytest.mark.parametrize("now", [True, 0, float("nan"), float("inf"), "100", 2**53, 10**400])
def test_invalid_clock_fails_before_store_access(now):
    class NoRead:
        def page(self, *_):
            pytest.fail("invalid clock must not read the store")

    with pytest.raises(ValueError, match="clock_invalid"):
        MeetDialogDeadlines(NoRead(), clock=lambda: now).run_once()


@pytest.mark.parametrize("limit", [True, 0, 101, "25"])
def test_page_budget_is_bounded(limit):
    with pytest.raises(ValueError, match="limit_invalid"):
        MeetDialogDeadlines(Store([])).run_once(limit=limit)


def test_stop_before_page_and_empty_restart_are_noops():
    service = MeetDialogDeadlines(Store([candidate()]), clock=lambda: 100)
    assert service.run_once(stopped=lambda: True)["scanned"] == 0
    assert service.cursor is None


def test_broken_store_order_fails_bounded():
    with pytest.raises(ValueError, match="cursor_invalid"):
        MeetDialogDeadlines(Store([candidate("b"), candidate("a")]), clock=lambda: 100).run_once()

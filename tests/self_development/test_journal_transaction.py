"""JournalTransaction: optimistic merge with bounded lock holds."""

from __future__ import annotations

from pathlib import Path

from agent.services.self_development.state import Journal, JournalTransaction


def _journal(tmp_path: Path) -> Journal:
    journal = Journal(tmp_path / "control")
    journal.write(
        {
            "items": {
                "k1": {"key": "k1", "todo_id": "T1", "status": "ready"},
                "k2": {"key": "k2", "todo_id": "T2", "status": "ready"},
            },
            "session": {"dispatched": 3},
        }
    )
    return journal


def test_begin_snapshots_and_write_merges_touched_items(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    state["items"]["k1"]["status"] = "running"
    state["session"]["dispatched"] = 4
    assert txn.write(owned_key="k1") is True
    live = journal.read()
    assert live["items"]["k1"]["status"] == "running"
    assert live["items"]["k2"] == {"key": "k2", "todo_id": "T2", "status": "ready"}
    assert live["session"]["dispatched"] == 4


def test_owned_item_conflict_marks_superseded_and_discards(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    # Concurrent admin change to the owned item before our flush.
    concurrent = journal.read()
    concurrent["items"]["k1"]["status"] = "cancelled"
    journal.write(concurrent)
    state["items"]["k1"]["status"] = "running"
    state["items"]["k1"]["result"] = "ours"
    assert txn.write(owned_key="k1") is False
    live = journal.read()
    assert live["items"]["k1"]["status"] == "cancelled"
    assert "result" not in live["items"]["k1"]


def test_foreign_touched_item_keeps_live_version_on_conflict(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    # We release a dependency (touch k2) while someone else edits k2 too.
    concurrent = journal.read()
    concurrent["items"]["k2"]["status"] = "failed"
    journal.write(concurrent)
    state["items"]["k2"]["status"] = "ready"
    state["items"]["k2"]["reason"] = None
    assert txn.write() is True
    live = journal.read()
    assert live["items"]["k2"]["status"] == "failed"


def test_untouched_items_never_overwrite_concurrent_changes(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    concurrent = journal.read()
    concurrent["items"]["k2"]["status"] = "running"
    journal.write(concurrent)
    state["items"]["k1"]["status"] = "running"
    assert txn.write(owned_key="k1") is True
    live = journal.read()
    assert live["items"]["k2"]["status"] == "running"


def test_baseline_resets_after_write_for_incremental_diffs(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    state["items"]["k1"]["status"] = "running"
    assert txn.write(owned_key="k1") is True
    # Second flush must not re-apply k1 unless touched again.
    concurrent = journal.read()
    concurrent["items"]["k1"]["status"] = "failed"
    journal.write(concurrent)
    assert txn.write(owned_key="k1") is True
    assert journal.read()["items"]["k1"]["status"] == "failed"
    # Touching k1 again after the concurrent edit reports it superseded.
    state["items"]["k1"]["status"] = "review_passed"
    assert txn.write(owned_key="k1") is False
    assert journal.read()["items"]["k1"]["status"] == "failed"


def test_discard_applies_nothing(tmp_path):
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    state["items"]["k1"]["status"] = "running"
    txn.discard()
    assert journal.read()["items"]["k1"]["status"] == "ready"


def test_tuple_values_from_asdict_compare_equal_after_roundtrip(tmp_path):
    """Asdict tuples must not read as conflicts against the journal's lists."""
    journal = _journal(tmp_path)
    txn = JournalTransaction(journal)
    state = txn.begin()
    state["items"]["k1"]["review"] = {"criteria_passed": (True, False), "findings": ()}
    assert txn.write(owned_key="k1") is True
    state["items"]["k1"]["status"] = "published"
    assert txn.write(owned_key="k1") is True
    live = journal.read()
    assert live["items"]["k1"]["status"] == "published"
    assert live["items"]["k1"]["review"]["criteria_passed"] == [True, False]

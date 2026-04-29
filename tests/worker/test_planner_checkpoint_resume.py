from __future__ import annotations

from worker.planning.checkpoint_store import PlannerCheckpointStore


def test_checkpoint_resume_restores_last_consistent_state(tmp_path) -> None:
    store = PlannerCheckpointStore(path=tmp_path / "checkpoint.json")
    payload = {
        "schema": "worker_planner_checkpoint.v1",
        "trace_id": "tr-1",
        "policy_snapshot_ref": "policy:1",
        "completed_steps": ["s1"],
        "pending_steps": ["s2"],
        "budget_counters": {"tokens_used": 120},
    }
    store.save(payload=payload)
    restored = store.load()
    assert restored is not None
    assert restored["completed_steps"] == ["s1"]
    assert restored["policy_snapshot_ref"] == "policy:1"


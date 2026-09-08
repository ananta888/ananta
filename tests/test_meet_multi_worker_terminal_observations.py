"""Terminal fixture extraction preserves missing-report and immutable-task checks."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_multi_worker_terminal_observations import terminal_observations


@pytest.mark.parametrize("crashed", [False, True])
@pytest.mark.parametrize("mutation", [None, "invented", "classification", "elapsed", "status", "task"])
def test_terminal_receipt_checker_keeps_all_existing_fences(crashed, mutation):
    statuses = ["failed" if crashed else "cancelled", "failed"]
    started = [{"task_id": "first"}, {"task_id": "second"}]
    rows = [{"status": status, "revision": 1} for status in statuses]
    observations = [
        {
            "observation_status": "recorded",
            "classification": "unverified_worker_observation",
            "hub_task_status": status,
            "observation": {"measurements": {"elapsed_ms": 10}},
        }
        for status in statuses
    ]
    if crashed:
        observations[0].update(observation_status="missing", observation=None)
    if mutation == "invented" and crashed:
        observations[0].update(observation_status="recorded", observation={"measurements": {"elapsed_ms": 10}})
    elif mutation == "classification":
        observations[1]["classification"] = "production"
    elif mutation == "elapsed":
        observations[1]["observation"]["measurements"]["elapsed_ms"] = 0
    elif mutation == "status":
        observations[1]["hub_task_status"] = "completed"
    calls = []

    def task(task_id):
        calls.append(task_id)
        row = deepcopy(rows[task_id == "second"])
        if mutation == "task" and len(calls) > 2:
            row["revision"] = 2
        return SimpleNamespace(model_dump=lambda: row)

    app = SimpleNamespace(app_context=nullcontext)
    tasks = SimpleNamespace(get_by_id=task)
    diagnostics = SimpleNamespace(inspect=lambda principal, project, task_id: observations[task_id == "second"])
    if mutation is None or mutation == "invented" and not crashed:
        assert (
            terminal_observations(app, tasks, diagnostics, Mock(), started, statuses, crashed=crashed) == observations
        )
    else:
        with pytest.raises(AssertionError):
            terminal_observations(app, tasks, diagnostics, Mock(), started, statuses, crashed=crashed)

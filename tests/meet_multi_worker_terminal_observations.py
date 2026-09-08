"""Bounded terminal observation verification, separate from container orchestration."""

import time


def terminal_observations(app, tasks, diagnostics, principal, started, expected_statuses, *, crashed=False):
    until = time.monotonic() + 8
    terminal_tasks = []
    # Receiver departure precedes the ordinary finish callback by design.
    # Compare immutable snapshots only after the actual terminal transition.
    while time.monotonic() < until:
        with app.app_context():
            terminal_tasks = [tasks.get_by_id(row["task_id"]).model_dump() for row in started]
        if [row["status"] for row in terminal_tasks] == expected_statuses:
            break
        time.sleep(0.05)
    assert [row["status"] for row in terminal_tasks] == expected_statuses, "bounded terminal Task finish missing"
    observations = []
    while time.monotonic() < until:
        with app.app_context():
            observations = [diagnostics.inspect(principal, "synthetic", row["task_id"]) for row in started]
        if all(row["observation_status"] == "recorded" for row in observations[1 if crashed else 0 :]):
            break
        time.sleep(0.05)
    recorded = observations[1 if crashed else 0 :]
    assert all(row["observation_status"] == "recorded" for row in recorded), "bounded terminal reports missing"
    if crashed:
        assert observations[0]["observation_status"] == "missing" and observations[0]["observation"] is None
    assert all(row["classification"] == "unverified_worker_observation" for row in observations)
    assert [row["hub_task_status"] for row in observations] == expected_statuses
    assert all(row["observation"]["measurements"]["elapsed_ms"] > 0 for row in recorded)
    with app.app_context():
        assert [tasks.get_by_id(row["task_id"]).model_dump() for row in started] == terminal_tasks
    return observations

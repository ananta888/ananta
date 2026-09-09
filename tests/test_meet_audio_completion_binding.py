"""Audio task ownership and final policy reread under shared Hub mutation locks."""

from contextlib import contextmanager

import pytest

from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_audio import runtime


def test_current_audio_assignment_requires_exact_parent_worker():
    f, _receipt, service, payload = runtime()
    job = service.start(payload)["job"]
    child = f.tasks.get_by_id(job["task_id"])
    f.task.assigned_agent_url = child.assigned_agent_url = "http://synthetic-worker:8094"
    service.current(("task", "dispatch", "runtime"), job)
    for changed in (None, "http://other-worker:8094"):
        child.assigned_agent_url = changed
        with pytest.raises(MeetError, match="task_inactive"):
            service.current(("task", "dispatch", "runtime"), job)


@pytest.mark.parametrize("available", [True, False])
def test_audio_result_cannot_complete_after_prelock_policy_change(monkeypatch, available):
    from agent.common.task_mutation_lock import get_task_mutation_lock_port

    f, _receipt, service, payload = runtime()
    job = service.start(payload)["job"]

    @contextmanager
    def acquire(task_ids):
        assert task_ids == {"task", job["task_id"]}
        if available:
            f.context["controls"]["audio"]["enabled"] = False
        yield available

    monkeypatch.setattr(get_task_mutation_lock_port(), "mutation_locks", acquire)
    with pytest.raises(MeetError):
        service.complete(
            payload
            | {
                "audio_task_id": job["task_id"],
                "audio_lease_id": job["lease_id"],
                "end_sample": 160000,
                "language": "de",
                "text": "synthetic",
            }
        )
    assert [call.args[2] for call in f.tasks.finish_audio.call_args_list] == ["failed"]

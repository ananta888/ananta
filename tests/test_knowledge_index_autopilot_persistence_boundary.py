from __future__ import annotations

from types import SimpleNamespace

from agent.routes.tasks import utils as task_utils
from agent.services import autopilot_support_service as support_module
from agent.services.autopilot_support_service import AutopilotSupportService


class _TaskRow(SimpleNamespace):
    def model_dump(self) -> dict:
        return dict(vars(self))


def _bound_task() -> dict:
    return {
        "id": "job-a",
        "status": "assigned",
        "history": [],
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "job_id": "job-a",
            }
        },
    }


def test_autopilot_status_writer_leaves_bound_v2_task_to_hub_cas(
    monkeypatch,
) -> None:
    writes: list[dict] = []
    monkeypatch.setattr(
        task_utils,
        "get_local_task_status",
        lambda _task_id: _bound_task(),
    )
    monkeypatch.setattr(
        task_utils,
        "update_local_task_status",
        lambda *args, **kwargs: writes.append(
            {"args": args, "kwargs": kwargs}
        ),
    )

    result = task_utils._update_local_task_status(
        "job-a",
        "failed",
        error="generic_autopilot_failure",
        force=True,
    )

    assert result is None
    assert writes == []


def test_autopilot_trace_writer_does_not_touch_bound_v2_task(
    monkeypatch,
) -> None:
    task = _TaskRow(**_bound_task())
    writes: list[dict] = []
    monkeypatch.setattr(
        support_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            task_repo=SimpleNamespace(get_by_id=lambda _task_id: task)
        ),
    )
    monkeypatch.setattr(
        support_module,
        "update_local_task_status",
        lambda *args, **kwargs: writes.append(
            {"args": args, "kwargs": kwargs}
        ),
    )

    AutopilotSupportService().append_trace_event(
        "job-a",
        "autopilot_dispatch_started",
    )

    assert writes == []


def test_autopilot_status_writer_still_updates_ordinary_tasks(
    monkeypatch,
) -> None:
    writes: list[dict] = []
    monkeypatch.setattr(
        task_utils,
        "get_local_task_status",
        lambda _task_id: {"id": "ordinary-task"},
    )
    monkeypatch.setattr(
        task_utils,
        "update_local_task_status",
        lambda *args, **kwargs: writes.append(
            {"args": args, "kwargs": kwargs}
        ),
    )

    task_utils._update_local_task_status(
        "ordinary-task",
        "in_progress",
    )

    assert writes == [
        {
            "args": ("ordinary-task", "in_progress"),
            "kwargs": {
                "event_type": None,
                "event_actor": "system",
                "event_details": None,
            },
        }
    ]

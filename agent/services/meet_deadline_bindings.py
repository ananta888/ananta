"""Immutable per-kind persisted-deadline readers; never execution authorization."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable


def deadline_identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 191


def _scalar_deadline(binding):
    if (
        not isinstance(binding, dict)
        or not deadline_identifier(binding.get("lease_id"))
        or type(binding.get("deadline")) is not int
        or not 0 < binding["deadline"] < 2**53
    ):
        raise ValueError("meet_deadline_binding_invalid")
    return binding["deadline"]


def _dialog(candidate, binding):
    deadline = _scalar_deadline(binding)
    if not deadline_identifier(binding.get("runtime_id")):
        raise ValueError("meet_deadline_binding_invalid")
    return deadline


def _audio(candidate, binding):
    deadline = _scalar_deadline(binding)
    context = candidate["context"]
    if (
        not deadline_identifier(candidate["parent_task_id"])
        or binding.get("task_id") != candidate["task_id"]
        or not deadline_identifier(context.get("parent_dispatch"))
        or not deadline_identifier(context.get("runtime_id"))
    ):
        raise ValueError("meet_deadline_binding_invalid")
    return deadline


def _browser(candidate, binding):
    from ananta_contracts.meet_browser_workspace import validate_browser_job

    job, context = validate_browser_job(binding), candidate["context"]
    if set(context) != {"meet_browser_job", "parent_dispatch", "runtime_id"} or (
        job["task_id"],
        job["parent_task_id"],
        job["tenant_id"],
        job["project_id"],
        job["parent_lease_id"],
        job["runtime_id"],
    ) != (
        candidate["task_id"],
        candidate["parent_task_id"],
        candidate["tenant_id"],
        candidate["project_id"],
        context["parent_dispatch"],
        context["runtime_id"],
    ):
        raise ValueError("meet_deadline_binding_invalid")
    return job["deadline_ms"] / 1000


def _visual(candidate, binding):
    from ananta_contracts.meet_visual_receive import validate_visual_job

    context = candidate["context"]
    if (
        not isinstance(binding, dict)
        or set(context) != {"meet_visual_job", "parent_dispatch", "runtime_id"}
        or candidate["parent_task_id"] == candidate["task_id"]
        or not all(
            deadline_identifier(value)
            for value in (
                candidate["parent_task_id"],
                context["parent_dispatch"],
                context["runtime_id"],
            )
        )
    ):
        raise ValueError("meet_deadline_binding_invalid")
    # Captured issue time checks structural duration bounds on an expired job;
    # only the scanner's real clock decides settlement, never continued execution.
    job = validate_visual_job(binding, binding.get("issued_at", 0))
    if job["task_id"] != candidate["task_id"]:
        raise ValueError("meet_deadline_binding_invalid")
    return job["deadline"]


@dataclass(frozen=True)
class DeadlineBinding:
    context_key: str
    event_type: str
    read: Callable[[dict, object], int | float]


DEADLINE_BINDINGS = MappingProxyType(
    {
        "meet_dialog_session": DeadlineBinding("meet_dialog", "meet_dialog_deadline_expired", _dialog),
        "meet_audio_receive": DeadlineBinding("meet_audio", "meet_audio_deadline_expired", _audio),
        "meet_browser_workspace": DeadlineBinding("meet_browser_job", "meet_browser_deadline_expired", _browser),
        "meet_visual_receive": DeadlineBinding("meet_visual_job", "meet_visual_deadline_expired", _visual),
    }
)

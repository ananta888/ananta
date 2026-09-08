"""Immutable observation binding; no Task identity or authorization mutation."""

import hashlib
import json
import math

from ananta_contracts.meet_dialog import validate_controls
from ananta_contracts.meet_dialog_diagnostics import observation_digest, validate_observation

TASK_FIELDS = (
    "id",
    "task_kind",
    "status",
    "tenant_id",
    "project_id",
    "parent_task_id",
    "assigned_agent_url",
    "organization_id",
    "unit_id",
    "team_id",
    "role_slot_id",
    "worker_execution_context",
)
TERMINAL = frozenset({"completed", "failed", "cancelled"})


def timestamp_ms(now):
    if type(now) not in {int, float} or not math.isfinite(now) or not 0 < now < (2**53) / 1000:
        raise ValueError("meet_dialog_diagnostics_clock_invalid")
    return int(now * 1000)


def task_binding(snapshot):
    if (
        type(snapshot) is not dict
        or set(snapshot) != set(TASK_FIELDS)
        or snapshot["task_kind"] != "meet_dialog_session"
        or snapshot["status"] not in TERMINAL | {"in_progress"}
    ):
        raise ValueError("meet_dialog_diagnostics_binding_invalid")
    context = snapshot["worker_execution_context"]
    if type(context) is not dict or type(context.get("meet_dialog")) is not dict:
        raise ValueError("meet_dialog_diagnostics_binding_invalid")
    dialog = context["meet_dialog"]
    validate_controls(dialog.get("controls"))
    if type(dialog.get("deadline")) is not int or dialog["deadline"] <= 0:
        raise ValueError("meet_dialog_diagnostics_binding_invalid")
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def record_for(snapshot, observation, now_ms):
    if snapshot["status"] not in TERMINAL:
        raise ValueError("meet_dialog_diagnostics_not_terminal")
    return {
        "schema": "ananta.meet-dialog-diagnostics-record.v1",
        "binding_digest": task_binding(snapshot),
        "observation_digest": observation_digest(observation),
        "observation": validate_observation(observation),
        "recorded_at_ms": now_ms,
        "hub_task_status": snapshot["status"],
        "hub_control_revision_at_receipt": snapshot["worker_execution_context"]["meet_dialog"]["controls"]["revision"],
    }


def validate_record(record, snapshot):
    if type(record) is not dict or set(record) != {
        "schema",
        "binding_digest",
        "observation_digest",
        "observation",
        "recorded_at_ms",
        "hub_task_status",
        "hub_control_revision_at_receipt",
    }:
        raise ValueError("meet_dialog_diagnostics_record_invalid")
    if (
        type(record["recorded_at_ms"]) is not int
        or not 0 < record["recorded_at_ms"] < 2**53
        or type(record["hub_control_revision_at_receipt"]) is not int
        or record != record_for(snapshot, record["observation"], record["recorded_at_ms"])
    ):
        raise ValueError("meet_dialog_diagnostics_record_invalid")
    return record_for(snapshot, record["observation"], record["recorded_at_ms"])

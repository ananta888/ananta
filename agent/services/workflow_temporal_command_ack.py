"""Fail-closed validation of Temporal workflow command acknowledgements."""

from __future__ import annotations

from typing import Any

from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from ananta_contracts.temporal_workflow import COMMAND_RESULT_SCHEMA
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA

_RESULT_KEYS = frozenset(
    {"schema", "command_id", "accepted", "revision", "status", "reason_code"}
)
_STATUSES = frozenset(
    {"created", "running", "paused", "waiting_approval", "completed", "failed", "cancelled"}
)


def validate_temporal_command_ack(
    raw: dict[str, Any],
    *,
    command: SignedWorkflowCommand,
) -> tuple[int, str]:
    if frozenset(raw) != _RESULT_KEYS:
        raise ValueError("workflow_control_command_ack_shape_invalid")
    if raw.get("schema") != COMMAND_RESULT_SCHEMA:
        raise ValueError("workflow_control_command_ack_schema_invalid")
    if raw.get("command_id") != command.command_id:
        raise ValueError("workflow_control_command_ack_identity_mismatch")
    if raw.get("accepted") is not True:
        raise ValueError("workflow_control_command_ack_rejected")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= command.expected_revision:
        raise ValueError("workflow_control_command_ack_revision_invalid")
    status = bounded_command_ack_text(
        raw.get("status"),
        field_name="status",
        maximum=64,
    ).lower()
    if status not in _STATUSES:
        raise ValueError("workflow_control_command_ack_status_invalid")
    bounded_command_ack_text(
        raw.get("reason_code"),
        field_name="reason_code",
        maximum=512,
        allow_empty=True,
    )
    return revision, status


def assert_acknowledged_observation(
    status: dict[str, Any],
    *,
    acknowledged_revision: int,
    acknowledged_status: str,
) -> None:
    source = status.get("source_observation")
    if not isinstance(source, dict) or source.get("schema") != TEMPORAL_STATUS_SCHEMA:
        raise ValueError("workflow_control_command_observation_schema_invalid")
    observed_revision = source.get("revision")
    if (
        isinstance(observed_revision, bool)
        or not isinstance(observed_revision, int)
        or observed_revision < acknowledged_revision
    ):
        raise ValueError("workflow_control_command_observation_revision_stale")
    if observed_revision == acknowledged_revision and source.get("status") != acknowledged_status:
        raise ValueError("workflow_control_command_observation_status_conflict")


def bounded_command_ack_text(
    raw: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(raw, str) or raw != raw.strip() or len(raw) > maximum:
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    if not raw and not allow_empty:
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    if any(not character.isprintable() or character in {"\x00", "\x7f"} for character in raw):
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    return raw

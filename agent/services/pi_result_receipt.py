"""Closed technical admission receipts, never source/run or release evidence."""

from agent.common.pi_task_result_binding import PI_RESULT_RECEIPT
from ananta_contracts.native_context_bundle import native_context_digest

_FIELDS = {
    "schema", "classification", "hub_task_id", "tenant_id", "project_id",
    "command_digest", "result_digest", "authority",
}
_COUNTERS = {"assignment_revision", "ownership_revision", "grant_revision"}
_IDENTITIES = {"assignment_id", "worker_id", "worker_url"}


def require_pi_result_receipt(*, task, command, candidate) -> dict:
    receipt = (task.verification_status or {}).get(PI_RESULT_RECEIPT)
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS:
        raise ValueError("pi_native_result_receipt_required")
    authority = receipt["authority"]
    if (
        receipt["schema"] != "ananta.pi-native-result-receipt.v1"
        or receipt["classification"] != "technical_observation"
        or receipt["hub_task_id"] != task.id or receipt["tenant_id"] != task.tenant_id
        or receipt["project_id"] != task.project_id
        or receipt["command_digest"] != native_context_digest(command.to_dict())
        or receipt["result_digest"] != candidate.digest
        or not isinstance(authority, dict) or set(authority) != _COUNTERS | _IDENTITIES
        or any(type(authority[key]) is not int or authority[key] < 1 for key in _COUNTERS)
        or any(not _identity(authority[key]) for key in _IDENTITIES)
        or authority["worker_url"] != task.assigned_agent_url
    ):
        raise ValueError("pi_native_result_receipt_invalid")
    return receipt


def _identity(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and not any(ord(char) < 33 for char in value)

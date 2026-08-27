"""Hub-owned collector for independently labelled tool-learning records."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol

from agent.services.local_tool_training_redaction import LocalToolTrainingRedactionPolicy
from ananta_contracts.local_tool_training import (
    IndependentToolOutcome,
    ToolDecision,
    ToolInteractionTrainingRecord,
)

_STATUS_LABELS = {
    "completed": "success",
    "schema_rejected": "schema_error",
    "tool_rejected": "unknown_tool",
    "arguments_rejected": "invalid_arguments",
    "timed_out": "timeout",
    "failed": "execution_error",
}


class LocalToolTrainingRepositoryPort(Protocol):
    def get_outcome(self, interaction_id: str) -> IndependentToolOutcome | None: ...

    def append_record(self, record: ToolInteractionTrainingRecord) -> None: ...


class LocalToolInteractionCollector:
    """Normalizes facts only; it cannot train, execute, route, or promote."""

    POLICY_VERSION = "local-tool-interaction-collector-v1"

    def __init__(
        self,
        *,
        repository: LocalToolTrainingRepositoryPort,
        redaction: LocalToolTrainingRedactionPolicy | None = None,
        audit_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        self._repository = repository
        self._redaction = redaction or LocalToolTrainingRedactionPolicy()
        self._audit = audit_sink or (lambda _action, _facts: None)

    @property
    def policy_digest(self) -> str:
        return hashlib.sha256(self.POLICY_VERSION.encode()).hexdigest()

    def collect(
        self,
        *,
        interaction_id: str,
        observed_at: str,
        request_class: str,
        expected_schema: Mapping[str, Any],
        candidate_tool: str | None,
        candidate_arguments: Mapping[str, Any],
    ) -> ToolInteractionTrainingRecord:
        trusted_outcome = self._repository.get_outcome(str(interaction_id).strip().lower())
        if trusted_outcome is None:
            raise ValueError("tool_training_outcome_not_independent")
        outcome_source = trusted_outcome.outcome_source
        execution_status = trusted_outcome.execution_status
        if execution_status not in _STATUS_LABELS:
            raise ValueError("tool_training_execution_status_invalid")
        schema = _canonical_json(self._redaction.sanitize_arguments(expected_schema))
        candidate = ToolDecision(
            tool_name=candidate_tool,
            arguments=self._redaction.sanitize_arguments(candidate_arguments),
        )
        outcome = ToolDecision(
            tool_name=trusted_outcome.decision.tool_name,
            arguments=self._redaction.sanitize_arguments(trusted_outcome.decision.arguments),
        )
        similarity = _canonical_json(
            {
                "request_class": str(request_class).strip().lower(),
                "expected_schema_sha256": hashlib.sha256(schema).hexdigest(),
                "outcome_tool": outcome.tool_name,
                "outcome_argument_keys": sorted(outcome.arguments),
            }
        )
        record = ToolInteractionTrainingRecord(
            interaction_id=interaction_id,
            observed_at=_normalize_time(observed_at),
            request_class=request_class,
            expected_schema_sha256=hashlib.sha256(schema).hexdigest(),
            candidate=candidate,
            independent_outcome=outcome,
            outcome_source=outcome_source,
            outcome_label=_STATUS_LABELS[execution_status],
            execution_status=execution_status,
            similarity_group_sha256=hashlib.sha256(similarity).hexdigest(),
            collector_policy_sha256=self.policy_digest,
            redaction_policy_sha256=self._redaction.digest,
            outcome_evidence_sha256=trusted_outcome.evidence_sha256,
        )
        self._repository.append_record(record)
        self._audit(
            "local_tool_training_record_collected",
            {
                "interaction_id": record.interaction_id,
                "request_class": record.request_class,
                "outcome_label": record.outcome_label,
                "outcome_source": record.outcome_source,
                "similarity_group_sha256": record.similarity_group_sha256,
                "redaction_policy_sha256": record.redaction_policy_sha256,
            },
        )
        return record


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("tool_training_value_not_canonical") from exc


def _normalize_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("tool_training_observed_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("tool_training_observed_at_invalid")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["LocalToolInteractionCollector", "LocalToolTrainingRepositoryPort"]

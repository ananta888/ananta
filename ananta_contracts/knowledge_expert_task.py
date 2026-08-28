"""Closed task envelopes for Hub-owned knowledge-expert orchestration."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TASK_TYPES = frozenset(
    {
        "compile_dataset",
        "train_expert",
        "evaluate_expert",
        "publish_bank",
        "revoke_expert",
        "garbage_collect",
    }
)


@dataclass(frozen=True, slots=True)
class KnowledgeExpertTask:
    schema: str
    task_id: str
    attempt_id: str
    task_type: str
    worker_audience: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    input_digest: str
    policy_digest: str
    deadline: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KnowledgeExpertTask":
        fields = {
            "schema",
            "task_id",
            "attempt_id",
            "task_type",
            "worker_audience",
            "tenant_id",
            "workspace_id",
            "repository_id",
            "input_digest",
            "policy_digest",
            "deadline",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("knowledge_expert_task_shape_invalid")
        if raw["schema"] != "ananta.knowledge-expert-task.v1":
            raise ValueError("knowledge_expert_task_schema_invalid")
        task_type = str(raw["task_type"])
        if task_type not in _TASK_TYPES:
            raise ValueError("knowledge_expert_task_type_invalid")
        text_fields = (
            "task_id",
            "attempt_id",
            "worker_audience",
            "tenant_id",
            "workspace_id",
            "repository_id",
        )
        if any(not str(raw[field]).strip() or len(str(raw[field])) > 192 for field in text_fields):
            raise ValueError("knowledge_expert_task_binding_invalid")
        for field in ("input_digest", "policy_digest"):
            if not _DIGEST.fullmatch(str(raw[field])):
                raise ValueError("knowledge_expert_task_digest_invalid")
        _parse_deadline(str(raw["deadline"]))
        return cls(**{field: str(raw[field]) for field in fields})

    def assert_executable(
        self,
        *,
        worker_audience: str,
        current_attempt_id: str,
        now: datetime | None = None,
    ) -> None:
        if worker_audience != self.worker_audience:
            raise ValueError("knowledge_expert_task_audience_mismatch")
        if current_attempt_id != self.attempt_id:
            raise ValueError("knowledge_expert_task_stale_attempt")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current >= _parse_deadline(self.deadline):
            raise ValueError("knowledge_expert_task_expired")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _parse_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("knowledge_expert_task_deadline_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("knowledge_expert_task_deadline_invalid")
    return parsed.astimezone(timezone.utc)


__all__ = ["KnowledgeExpertTask"]

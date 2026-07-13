"""Container-neutral contracts for LangGraph's Hub-owned checkpoint gateway.

Only JSON-safe values cross this boundary.  The Hub deliberately does not
import LangGraph and the Worker deliberately does not import Hub services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA = "ananta.langgraph_checkpoint_command.v1"
LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA = "ananta.langgraph_checkpoint_response.v1"
LANGGRAPH_CHECKPOINT_SNAPSHOT_SCHEMA = "ananta.langgraph_checkpoint_snapshot.v1"
LANGGRAPH_CHECKPOINT_OPERATIONS = frozenset({"get", "list", "put", "put_writes"})
LANGGRAPH_CHECKPOINT_RUNTIME_ID = "langgraph"
LANGGRAPH_CHECKPOINT_RUNTIME_VERSION = "1"
MAX_LANGGRAPH_CHECKPOINT_HISTORY = 100


class LangGraphCheckpointContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class LangGraphCheckpointBinding:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    task_id: str
    plan_hash: str
    policy_version: str
    fencing_token: int
    authorization_envelope: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: object) -> "LangGraphCheckpointBinding":
        if not isinstance(raw, Mapping):
            raise LangGraphCheckpointContractError("langgraph_checkpoint_binding_invalid")
        try:
            binding = cls(
                tenant_id=str(raw.get("tenant_id") or "").strip(),
                workflow_id=str(raw.get("workflow_id") or "").strip(),
                run_id=str(raw.get("run_id") or "").strip(),
                step_id=str(raw.get("step_id") or "").strip(),
                task_id=str(raw.get("task_id") or "").strip(),
                plan_hash=str(raw.get("plan_hash") or "").strip(),
                policy_version=str(raw.get("policy_version") or "").strip(),
                fencing_token=int(raw.get("fencing_token") or 0),
                authorization_envelope=dict(raw.get("authorization_envelope") or {}),
            )
        except (TypeError, ValueError) as exc:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_binding_invalid") from exc
        binding.validate()
        return binding

    def validate(self) -> None:
        values = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.task_id,
            self.plan_hash,
            self.policy_version,
        )
        if any(not value or len(value) > 256 for value in values):
            raise LangGraphCheckpointContractError("langgraph_checkpoint_binding_invalid")
        if self.fencing_token < 1 or not self.authorization_envelope:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_authority_invalid")
        if self.task_id != self.step_id:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_task_scope_mismatch")
        _assert_json_value(dict(self.authorization_envelope))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "task_id": self.task_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "fencing_token": self.fencing_token,
            "authorization_envelope": dict(self.authorization_envelope),
        }


@dataclass(frozen=True)
class LangGraphCheckpointSnapshot:
    checkpoint: Mapping[str, Any]
    metadata: Mapping[str, Any]
    pending_writes: tuple[tuple[str, str, Any], ...]
    config: Mapping[str, Any]
    parent_config: Mapping[str, Any] | None
    revision: int
    head_revision: int
    signed_checkpoint_ref: str
    schema: str = LANGGRAPH_CHECKPOINT_SNAPSHOT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "LangGraphCheckpointSnapshot":
        if not isinstance(raw, Mapping) or raw.get("schema") != LANGGRAPH_CHECKPOINT_SNAPSHOT_SCHEMA:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_snapshot_invalid")
        if raw.get("parent_config") is not None and not isinstance(raw.get("parent_config"), Mapping):
            raise LangGraphCheckpointContractError("langgraph_checkpoint_snapshot_invalid")
        try:
            snapshot = cls(
                checkpoint=assert_json_mapping(
                    raw.get("checkpoint"),
                    reason_code="langgraph_checkpoint_snapshot_invalid",
                ),
                metadata=assert_json_mapping(
                    raw.get("metadata") or {},
                    reason_code="langgraph_checkpoint_snapshot_invalid",
                ),
                pending_writes=normalize_pending_writes(raw.get("pending_writes") or []),
                config=assert_json_mapping(
                    raw.get("config"),
                    reason_code="langgraph_checkpoint_snapshot_invalid",
                ),
                parent_config=(
                    assert_json_mapping(
                        raw.get("parent_config"),
                        reason_code="langgraph_checkpoint_snapshot_invalid",
                    )
                    if raw.get("parent_config") is not None
                    else None
                ),
                revision=int(raw.get("revision") or 0),
                head_revision=int(raw.get("head_revision") or raw.get("revision") or 0),
                signed_checkpoint_ref=str(raw.get("signed_checkpoint_ref") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_snapshot_invalid") from exc
        if (
            not snapshot.checkpoint
            or snapshot.revision < 1
            or snapshot.head_revision < snapshot.revision
            or not snapshot.signed_checkpoint_ref
            or len(snapshot.signed_checkpoint_ref) > 256
        ):
            raise LangGraphCheckpointContractError("langgraph_checkpoint_snapshot_invalid")
        _assert_json_value(snapshot.to_dict())
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "checkpoint": dict(self.checkpoint),
            "metadata": dict(self.metadata),
            "pending_writes": [list(item) for item in self.pending_writes],
            "config": dict(self.config),
            "parent_config": dict(self.parent_config) if self.parent_config is not None else None,
            "revision": self.revision,
            "head_revision": self.head_revision,
            "signed_checkpoint_ref": self.signed_checkpoint_ref,
        }


def assert_langgraph_config_binding(config: object, *, task_id: str) -> dict[str, Any]:
    """Validate a LangGraph ``RunnableConfig`` without importing LangGraph."""

    if not isinstance(config, Mapping):
        raise LangGraphCheckpointContractError("langgraph_checkpoint_config_invalid")
    normalized = dict(config)
    configurable = normalized.get("configurable")
    if not isinstance(configurable, Mapping):
        raise LangGraphCheckpointContractError("langgraph_checkpoint_config_invalid")
    thread_id = str(configurable.get("thread_id") or "").strip()
    namespace = str(configurable.get("checkpoint_ns") or "")
    checkpoint_id = str(configurable.get("checkpoint_id") or "")
    if thread_id != str(task_id) or len(namespace) > 256 or len(checkpoint_id) > 256:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_config_binding_mismatch")
    if namespace:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_namespace_unsupported")
    _assert_json_value(normalized)
    return normalized


def normalize_pending_writes(raw: object) -> tuple[tuple[str, str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) > 10_000:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_writes_invalid")
    normalized: list[tuple[str, str, Any]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_writes_invalid")
        task_id, channel, value = str(item[0]).strip(), str(item[1]).strip(), item[2]
        if not task_id or not channel or len(task_id) > 256 or len(channel) > 256:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_writes_invalid")
        _assert_json_value(value)
        normalized.append((task_id, channel, value))
    return tuple(normalized)


def assert_json_mapping(raw: object, *, reason_code: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LangGraphCheckpointContractError(reason_code)
    value = dict(raw)
    _assert_json_value(value)
    return value


def _assert_json_value(value: object, *, depth: int = 0) -> None:
    if depth > 64:
        raise LangGraphCheckpointContractError("langgraph_checkpoint_json_depth_exceeded")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_json_invalid")
        return
    if isinstance(value, Mapping):
        if len(value) > 100_000:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_json_too_large")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 1_024:
                raise LangGraphCheckpointContractError("langgraph_checkpoint_json_invalid")
            _assert_json_value(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 100_000:
            raise LangGraphCheckpointContractError("langgraph_checkpoint_json_too_large")
        for item in value:
            _assert_json_value(item, depth=depth + 1)
        return
    raise LangGraphCheckpointContractError("langgraph_checkpoint_json_invalid")


__all__ = [
    "LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA",
    "LANGGRAPH_CHECKPOINT_OPERATIONS",
    "LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA",
    "LANGGRAPH_CHECKPOINT_RUNTIME_ID",
    "LANGGRAPH_CHECKPOINT_RUNTIME_VERSION",
    "LANGGRAPH_CHECKPOINT_SNAPSHOT_SCHEMA",
    "MAX_LANGGRAPH_CHECKPOINT_HISTORY",
    "LangGraphCheckpointBinding",
    "LangGraphCheckpointContractError",
    "LangGraphCheckpointSnapshot",
    "assert_json_mapping",
    "assert_langgraph_config_binding",
    "normalize_pending_writes",
]

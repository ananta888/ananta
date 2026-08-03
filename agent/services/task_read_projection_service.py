"""Closed projections for generic Task read surfaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

_SUMMARY_SCALAR_FIELDS = (
    "id",
    "title",
    "description",
    "status",
    "priority",
    "created_at",
    "updated_at",
    "archived_at",
    "tenant_id",
    "project_id",
    "organization_id",
    "unit_id",
    "team_id",
    "role_slot_id",
    "assigned_agent_url",
    "assigned_role_id",
    "manual_override_until",
    "goal_id",
    "goal_trace_id",
    "plan_id",
    "plan_node_id",
    "task_kind",
    "current_worker_job_id",
    "status_reason_code",
    "parent_task_id",
    "source_task_id",
    "derivation_reason",
    "derivation_depth",
    "kanban_position",
    "kanban_revision",
)
_SUMMARY_LIST_FIELDS = (
    "depends_on",
    "required_capabilities",
    "tags",
)
_VERIFICATION_FIELDS = frozenset(
    {
        "status",
        "state",
        "result",
        "reason_code",
        "verified",
        "passed",
        "verified_at",
        "updated_at",
        "completed_at",
    }
)
_SOURCE_CATALOG_METADATA_FIELDS = frozenset(
    {
        "catalog_hash",
        "catalog_id",
        "catalog_state",
        "rejected_count",
        "retrieval_context_hash",
        "retrieval_manifest_hash",
        "retrieval_trace_id",
        "schema",
        "source_catalog_hash",
        "source_catalog_id",
        "source_count",
    }
)
_SOURCE_CATALOG_PUBLICATION_METADATA_FIELDS = frozenset(
    {
        "active_generation",
        "admission_digest",
        "admission_receipt_id",
        "binding_digest",
        "index_manifest_digest",
        "index_run_id",
        "index_source_scope",
        "knowledge_index_id",
        "organization_id",
        "policy_snapshot_digest",
        "query_count",
        "query_limit",
        "revision_digest",
        "schema",
        "source_count",
        "source_manifest_digest",
        "source_revision_id",
    }
)
_HISTORY_EVENT_FIELDS = (
    "schema",
    "channel",
    "event_type",
    "timestamp",
    "actor",
    "task_id",
    "goal_id",
    "trace_id",
    "plan_id",
    "team_id",
    "task_status",
)
_HISTORY_DETAIL_FIELDS = frozenset(
    {
        "action",
        "agent_url",
        "allowed",
        "assignment_id",
        "attempt",
        "blocked_reasons",
        "blocked_tools",
        "catalog_hash",
        "catalog_id",
        "channel",
        "decision",
        "event_fingerprint",
        "exit_code",
        "fields",
        "from_status",
        "organization_id",
        "parent_task_id",
        "policy_name",
        "policy_precheck",
        "policy_version",
        "publication_binding_digest",
        "quality_gate_failed",
        "reason",
        "reason_code",
        "reasons",
        "rule_ids",
        "security_level",
        "source",
        "status",
        "status_requested",
        "task_count",
        "to_status",
        "trigger_event_fingerprint",
        "trigger_policy_precheck",
        "worker_job_id",
    }
)
_INSTRUCTION_SUMMARY_FIELDS = frozenset(
    {
        "attachment_id",
        "attachment_kind",
        "owner_username",
        "overlay_id",
        "profile_id",
        "selected_overlay",
        "selected_profile",
    }
)
_INSTRUCTION_ENTITY_FIELDS = frozenset(
    {
        "attachment_id",
        "attachment_kind",
        "expires_at",
        "id",
        "is_active",
        "is_default",
        "name",
        "owner_username",
        "scope",
    }
)
_WORKER_CONTEXT_SCALAR_FIELDS = (
    "execution_mode",
    "profile_source",
    "task_kind",
    "worker_profile",
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return {}


def _safe_scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))]


class TaskReadProjectionService:
    """Expose only fields required by generic Task UI/read consumers."""

    def summary(self, value: Any) -> dict[str, Any]:
        task = _mapping(value)
        projected = {
            field: _safe_scalar(task.get(field))
            for field in _SUMMARY_SCALAR_FIELDS
            if field in task
        }
        projected.update(
            {
                field: _safe_string_list(task.get(field))
                for field in _SUMMARY_LIST_FIELDS
                if field in task
            }
        )
        if "verification_status" in task:
            projected["verification_status"] = self.verification_summary(
                task.get("verification_status")
            )
        return projected

    def detail(
        self,
        value: Any,
        *,
        instruction_layers: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = _mapping(value)
        projected = self.summary(task)
        projected["last_exit_code"] = _safe_scalar(task.get("last_exit_code"))
        projected["context_bundle_id"] = _safe_scalar(task.get("context_bundle_id"))
        projected["worker_execution_context"] = self.worker_context_summary(
            task.get("worker_execution_context")
        )
        projected["verification_status"] = self.verification_summary(
            task.get("verification_status")
        )
        projected["history"] = [
            event
            for event in (
                self.history_event(item)
                for item in list(task.get("history") or [])
            )
            if event is not None
        ]
        if instruction_layers is not None:
            projected["instruction_layers"] = self.instruction_summary(
                instruction_layers
            )
        return projected

    @staticmethod
    def worker_context_summary(value: Any) -> dict[str, Any]:
        payload = _mapping(value)
        projected = {
            field: scalar
            for field in _WORKER_CONTEXT_SCALAR_FIELDS
            if field in payload
            and (scalar := _safe_scalar(payload.get(field))) is not None
        }
        if "allowed_tools" in payload:
            projected["allowed_tools"] = _safe_string_list(
                payload.get("allowed_tools")
            )
        return projected

    @staticmethod
    def verification_summary(value: Any) -> dict[str, Any]:
        payload = _mapping(value)
        return {
            key: scalar
            for key, raw in payload.items()
            if key in _VERIFICATION_FIELDS
            and (scalar := _safe_scalar(raw)) is not None
        }

    def verification_detail(self, value: Any) -> dict[str, Any]:
        """Expose status plus content-free Source Catalog metadata."""

        payload = _mapping(value)
        projected = self.verification_summary(payload)
        source_catalog = self._scalar_metadata(
            payload.get("source_catalog"),
            allowed_fields=_SOURCE_CATALOG_METADATA_FIELDS,
        )
        raw_catalog = _mapping(payload.get("source_catalog"))
        if "source_count" not in source_catalog and isinstance(
            raw_catalog.get("sources"),
            list,
        ):
            source_catalog["source_count"] = len(raw_catalog["sources"])
        if source_catalog:
            projected["source_catalog"] = source_catalog

        publication = self._scalar_metadata(
            payload.get("source_catalog_publication"),
            allowed_fields=_SOURCE_CATALOG_PUBLICATION_METADATA_FIELDS,
        )
        if publication:
            projected["source_catalog_publication"] = publication
        return projected

    def history_event(self, value: Any) -> dict[str, Any] | None:
        event = _mapping(value)
        event_type = str(event.get("event_type") or "").strip()
        if not event_type:
            return None
        projected = {
            field: scalar
            for field in _HISTORY_EVENT_FIELDS
            if field in event
            and (scalar := _safe_scalar(event.get(field))) is not None
        }
        projected["event_type"] = event_type
        details = self._history_details(event.get("details"))
        if details:
            projected["details"] = details
        return projected

    def tree(
        self,
        value: Any,
        *,
        can_read: Callable[[Mapping[str, Any]], bool],
    ) -> dict[str, Any] | None:
        node = _mapping(value)
        task = _mapping(node.get("task"))
        if not task or not can_read(task):
            return None
        children = [
            child
            for child in (
                self.tree(item, can_read=can_read)
                for item in list(node.get("children") or [])
            )
            if child is not None
        ]
        projected: dict[str, Any] = {
            "task": self.summary(task),
            "depth": int(node.get("depth") or 0),
            "children": children,
            "children_count": len(children),
        }
        if node.get("truncated") is True:
            projected["truncated"] = True
        return projected

    def instruction_summary(self, value: Any) -> dict[str, Any]:
        payload = _mapping(value)
        projected: dict[str, Any] = {}
        for field in _INSTRUCTION_SUMMARY_FIELDS:
            if field not in payload:
                continue
            raw = payload[field]
            if field in {"selected_profile", "selected_overlay"}:
                entity = _mapping(raw)
                projected[field] = {
                    key: scalar
                    for key, item in entity.items()
                    if key in _INSTRUCTION_ENTITY_FIELDS
                    and (scalar := _safe_scalar(item)) is not None
                } or None
            else:
                projected[field] = _safe_scalar(raw)
        return projected

    def _history_details(self, value: Any) -> dict[str, Any]:
        payload = _mapping(value)
        projected: dict[str, Any] = {}
        for key, raw in payload.items():
            if key not in _HISTORY_DETAIL_FIELDS:
                continue
            scalar = _safe_scalar(raw)
            if scalar is not None:
                projected[key] = scalar
                continue
            if isinstance(raw, (list, tuple, set, frozenset)):
                projected[key] = _safe_string_list(raw)
                continue
            if isinstance(raw, Mapping):
                nested = self._history_details(raw)
                if nested:
                    projected[key] = nested
        return projected

    @staticmethod
    def _scalar_metadata(
        value: Any,
        *,
        allowed_fields: frozenset[str],
    ) -> dict[str, Any]:
        payload = _mapping(value)
        return {
            key: scalar
            for key, raw in payload.items()
            if key in allowed_fields
            and (scalar := _safe_scalar(raw)) is not None
        }


_SERVICE = TaskReadProjectionService()


def get_task_read_projection_service() -> TaskReadProjectionService:
    return _SERVICE


__all__ = [
    "TaskReadProjectionService",
    "get_task_read_projection_service",
]

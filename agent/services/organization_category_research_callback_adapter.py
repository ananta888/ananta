"""Adapt the existing Worker subtask callback to Category result admission."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any


_CATEGORY_CALLBACK_PATH = (
    "/api/worker-results/tasks/{source_task_id}/assignments/"
    "{assignment_id}/planning/category"
)


class OrganizationCategoryResearchCallbackError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class OrganizationCategoryResearchCallbackAdapter:
    """Route an admitted generic callback into the closed Category pipeline."""

    def __init__(
        self,
        *,
        task_reader: Callable[[str], Any] | None = None,
        result_acceptor: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._task_reader = task_reader or self._default_task_reader
        self._result_acceptor = result_acceptor or self._default_result_acceptor

    @staticmethod
    def _default_task_reader(task_id: str) -> Any:
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    @staticmethod
    def _default_result_acceptor(**kwargs: Any) -> dict[str, Any]:
        from agent.services.organization_planning_composition import (
            get_organization_planning_composition,
        )

        return get_organization_planning_composition().accept_category_research_result(
            **kwargs
        )

    @staticmethod
    def _is_category_task(task: Any) -> bool:
        worker_context = _mapping(_value(task, "worker_execution_context"))
        callback = _mapping(worker_context.get("planning_result_callback"))
        return (
            str(_value(task, "task_kind") or "") == "planning_research"
            and callback
            == {
                "schema": "organization_planning_result_callback.v1",
                "method": "POST",
                "path_template": _CATEGORY_CALLBACK_PATH,
                "authorization": "worker_result_capability",
            }
        )

    def accept_if_applicable(
        self,
        *,
        source_task_id: str,
        payload: Mapping[str, Any],
        capability_claims: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        task_id = str(source_task_id or "").strip()
        task = self._task_reader(task_id)
        if task is None or not self._is_category_task(task):
            return None

        status = str(payload.get("status") or "").strip().lower()
        exit_code = payload.get("last_exit_code")
        raw_output = str(payload.get("last_output") or "")
        if status != "completed" or exit_code != 0 or not raw_output:
            return None

        assignment_id = str(
            payload.get("id") or payload.get("assignment_id") or ""
        ).strip()
        if (
            str(capability_claims.get("source_task_id") or "") != task_id
            or str(capability_claims.get("assignment_id") or "")
            != assignment_id
        ):
            raise OrganizationCategoryResearchCallbackError(
                "category_research_callback_capability_mismatch"
            )

        digest = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        worker_context = _mapping(_value(task, "worker_execution_context"))
        research_binding = _mapping(
            worker_context.get("planning_research_binding")
        )
        artifact_hashes = _mapping(research_binding.get("artifact_hashes"))
        idempotency_payload = {
            "source_task_id": task_id,
            "assignment_id": assignment_id,
            "dispatch_lease_id": str(
                capability_claims.get("dispatch_lease_id") or ""
            ),
            "raw_output_digest": digest,
        }
        idempotency_key = "category-result-" + hashlib.sha256(
            json.dumps(
                idempotency_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        try:
            return self._result_acceptor(
                source_task_id=task_id,
                assignment_id=assignment_id,
                capability_claims=dict(capability_claims),
                raw_output=raw_output,
                raw_output_digest=digest,
                idempotency_key=idempotency_key,
                runtime_artifact_hashes={
                    str(key): str(value)
                    for key, value in artifact_hashes.items()
                },
            )
        except (TypeError, ValueError) as exc:
            reason_code = str(
                getattr(exc, "reason_code", "")
                or str(exc)
                or "category_research_result_invalid"
            )
            raise OrganizationCategoryResearchCallbackError(
                reason_code
            ) from exc


__all__ = [
    "OrganizationCategoryResearchCallbackAdapter",
    "OrganizationCategoryResearchCallbackError",
]

"""Least-privilege projection of one Hub-owned Recovery child task."""

from __future__ import annotations

import copy
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.recovery_plan_contract import (
    calculate_recovery_task_payload_digest,
)
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.task_mutation_lock_service import (
    get_task_mutation_lock_port,
)

RECOVERY_TASK_MANIFEST_SCHEMA = "ananta.recovery-task-manifest.v1"

# Explicit allowlist: execution payload only.  Assignment, callbacks, history,
# results, review state, and other Hub control-plane fields never cross this
# boundary.
_RECOVERY_CHILD_PAYLOAD_FIELDS = (
    "id",
    "title",
    "description",
    "priority",
    "goal_id",
    "goal_trace_id",
    "plan_id",
    "plan_node_id",
    "parent_task_id",
    "source_task_id",
    "team_id",
    "derivation_reason",
    "derivation_depth",
    "task_kind",
    "retrieval_intent",
    "required_context_scope",
    "preferred_bundle_mode",
    "required_capabilities",
    "context_bundle_id",
    "worker_execution_context",
    "worker_execution_contract",
    "expected_artifacts",
    "verification_spec",
    "depends_on",
)


@dataclass
class RecoveryTaskManifestDenied(RuntimeError):
    reason_code: str
    status_code: int

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.reason_code)


class RecoveryTaskManifestService:
    """Authorize and project a Recovery child for its assigned Worker."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] = get_repository_registry,
        lock_provider: Callable[[], Any] = get_task_mutation_lock_port,
    ) -> None:
        self._repository_provider = repository_provider
        self._lock_provider = lock_provider

    def manifest_for_worker(
        self,
        *,
        task_id: str,
        worker_url: str,
    ) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        normalized_worker_url = _normalized_worker_url(worker_url)
        if not normalized_task_id or not normalized_worker_url:
            raise RecoveryTaskManifestDenied(
                "recovery_task_manifest_identity_invalid",
                403,
            )

        with self._lock_provider().mutation_lock(
            normalized_task_id
        ) as acquired:
            if not acquired:
                raise RecoveryTaskManifestDenied(
                    "recovery_task_manifest_lock_unavailable",
                    503,
                )
            task = self._repository_provider().task_repo.get_by_id(
                normalized_task_id
            )
            if task is None or recovery_task_role(task) != "child":
                # Do not reveal generic/source task data through this endpoint.
                raise RecoveryTaskManifestDenied(
                    "recovery_task_manifest_not_found",
                    404,
                )
            assigned_worker_url = _normalized_worker_url(
                _value(task, "assigned_agent_url")
            )
            if (
                not assigned_worker_url
                or assigned_worker_url != normalized_worker_url
            ):
                raise RecoveryTaskManifestDenied(
                    "recovery_task_manifest_assignment_denied",
                    403,
                )

            release = _recovery_release(task)
            expected_digest = str(
                release.get("task_payload_digest") or ""
            ).strip()
            actual_digest = calculate_recovery_task_payload_digest(
                task
            )
            if (
                not expected_digest
                or not hmac.compare_digest(
                    expected_digest,
                    actual_digest,
                )
            ):
                raise RecoveryTaskManifestDenied(
                    "recovery_task_manifest_payload_digest_mismatch",
                    409,
                )

            task_payload = {
                field: copy.deepcopy(_value(task, field))
                for field in _RECOVERY_CHILD_PAYLOAD_FIELDS
            }
            task_payload["status_reason_details"] = {
                "model_recovery_release": copy.deepcopy(release)
            }
            run_context = _mapping(
                _mapping(
                    _value(task, "status_reason_details")
                ).get("recovery_tool_run_context")
            )
            if run_context:
                from ananta_contracts.recovery_run_evidence import (
                    RecoveryRunEvidenceContractError,
                    validate_recovery_tool_run_context,
                )

                try:
                    projected_run_context = (
                        validate_recovery_tool_run_context(
                            run_context,
                            task_id=normalized_task_id,
                        )
                    )
                except RecoveryRunEvidenceContractError as exc:
                    raise RecoveryTaskManifestDenied(
                        str(exc),
                        409,
                    ) from exc
                task_payload["status_reason_details"][
                    "recovery_tool_run_context"
                ] = copy.deepcopy(projected_run_context)
            return {
                "schema": RECOVERY_TASK_MANIFEST_SCHEMA,
                "task": task_payload,
            }


def _value(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _recovery_release(task: Any) -> dict[str, Any]:
    for field in ("status_reason_details", "verification_status"):
        release = _mapping(
            _mapping(_value(task, field)).get(
                "model_recovery_release"
            )
        )
        if release:
            return release
    return {}


def _normalized_worker_url(value: Any) -> str:
    # Authentication already canonicalizes the claimed URL.  A conservative
    # comparison here intentionally denies non-identical assignments.
    return str(value or "").strip().rstrip("/")


_SERVICE = RecoveryTaskManifestService()


def get_recovery_task_manifest_service() -> RecoveryTaskManifestService:
    return _SERVICE


__all__ = [
    "RECOVERY_TASK_MANIFEST_SCHEMA",
    "RecoveryTaskManifestDenied",
    "RecoveryTaskManifestService",
    "get_recovery_task_manifest_service",
]

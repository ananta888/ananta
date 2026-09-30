"""Submission of Hub-planned, revision-bound source index jobs.

``HubBoundSourceIndexSubmissionAdapter`` accepts only a complete governance
plan that is still bound to the admitted revision and translates the public
connector type into the runtime index source scope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from agent.db_models.source_control import SourceConnectionDB, SourceRevisionDB
from agent.services.source_control_adapter_common import (
    SourceControlProductionAdapterError,
    public_projection,
)


_BOUND_INDEX_PLAN_FIELDS = frozenset(
    {
        "hub_task_id",
        "source_revision_id",
        "source_revision_digest",
        "admission_digest",
        "policy_snapshot_id",
        "policy_snapshot_digest",
        "destination_id",
        "destination_digest",
        "source_access_grant_id",
        "source_access_grant_digest",
        "files",
        "resource_budget",
        "assignment",
        "destination_selection",
        "source_scope",
        "source_id",
        "records",
    }
)
_BOUND_INDEX_REMOTE_PLAN_FIELDS = frozenset(
    {"source_payload_digest", "source_payload_connection_id"}
)

_REPOSITORY_CONNECTOR_SCOPES = frozenset(
    {
        "registered_workspace",
        "local_directory",
        "git",
        "github",
        "generic_git",
        "github_repository",
    }
)


def _runtime_index_source_scope(connector_type: object) -> str:
    normalized = str(connector_type or "").strip().lower()
    if normalized in _REPOSITORY_CONNECTOR_SCOPES:
        return "repo_path"
    if normalized in {"artifact", "repo_path", "wiki"}:
        return normalized
    raise SourceControlProductionAdapterError(
        "source_index_connector_scope_unsupported",
        status_code=400,
    )


class HubBoundSourceIndexSubmissionAdapter:
    """Submit only complete Hub-planned, revision-bound index jobs."""

    def __init__(self, *, planner: object, job_service: object) -> None:
        self._planner = planner
        self._jobs = job_service

    def submit(
        self,
        *,
        connection: SourceConnectionDB,
        revision: SourceRevisionDB,
        descriptor: Mapping[str, object],
        actor_id: str,
        idempotency_key: str,
        profile_name: str,
    ) -> Mapping[str, object]:
        plan_method = getattr(
            self._planner, "plan_bound_source_revision", None
        )
        submit_method = getattr(
            self._jobs, "submit_bound_source_revision_job", None
        )
        if not callable(plan_method) or not callable(submit_method):
            raise SourceControlProductionAdapterError(
                "source_index_governance_backend_unconfigured",
                status_code=503,
            )
        raw_plan = plan_method(
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            actor_id=actor_id,
            connection_id=connection.connection_id,
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            descriptor=dict(descriptor),
            idempotency_key=idempotency_key,
        )
        if not isinstance(raw_plan, Mapping):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_invalid", status_code=502
            )
        plan = dict(raw_plan)
        plan_fields = set(plan)
        remote_plan_fields = plan_fields & _BOUND_INDEX_REMOTE_PLAN_FIELDS
        if (
            plan_fields
            not in (
                _BOUND_INDEX_PLAN_FIELDS,
                _BOUND_INDEX_PLAN_FIELDS
                | _BOUND_INDEX_REMOTE_PLAN_FIELDS,
            )
            or remote_plan_fields
            not in (set(), set(_BOUND_INDEX_REMOTE_PLAN_FIELDS))
        ):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_invalid", status_code=502
            )
        if remote_plan_fields:
            if (
                re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(plan["source_payload_digest"]),
                )
                is None
                or plan["source_payload_connection_id"]
                != connection.connection_id
            ):
                raise SourceControlProductionAdapterError(
                    "source_index_governance_plan_stale", status_code=409
                )
        if (
            plan["source_revision_id"] != revision.source_revision_id
            or plan["source_revision_digest"] != revision.revision_digest
            or plan["source_scope"] != connection.connector_type
            or plan["source_id"] != str(descriptor.get("source_id") or "")
        ):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_stale", status_code=409
            )
        execution_plan = {
            **{
                key: value
                for key, value in plan.items()
                if key not in _BOUND_INDEX_REMOTE_PLAN_FIELDS
            },
            "source_scope": _runtime_index_source_scope(
                plan["source_scope"]
            ),
        }
        result = submit_method(
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            owner_id=connection.owner_id,
            created_by=actor_id,
            idempotency_key=idempotency_key,
            profile_name=profile_name,
            source_operation="index",
            source_transformation="redacted",
            source_purpose="knowledge-index",
            source_policy_version=str(plan["policy_snapshot_id"]),
            **execution_plan,
        )
        public = public_projection(result)
        if not isinstance(public, Mapping):
            raise SourceControlProductionAdapterError(
                "source_index_submission_result_invalid", status_code=502
            )
        return dict(public)


__all__ = ["HubBoundSourceIndexSubmissionAdapter"]

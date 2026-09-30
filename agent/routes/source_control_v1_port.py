"""Runtime port consumed by the Source Control v1 routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)


class SourceControlV1Port(Protocol):
    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None: ...

    def validate_connection(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def create_connection(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def validate_content_admission(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def create_content_admission(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def list_source_control_catalog(
        self,
        *,
        principal: object,
        catalog: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def list_grant_presets(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def list_grants(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def create_grant(
        self,
        *,
        principal: object,
        project_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def revoke_grant(
        self,
        *,
        principal: object,
        project_id: str,
        grant_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def prepare_index_access_options(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
    ) -> Mapping[str, object]: ...

    def prepare_index_access(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def list_connections(
        self,
        *,
        principal: object,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def get_connection(
        self, *, principal: object, connection_id: str
    ) -> tuple[Mapping[str, object], str]: ...

    def run_history(
        self,
        *,
        principal: object,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def compare_indices(
        self,
        *,
        principal: object,
        left_index_id: str,
        right_index_id: str,
    ) -> Mapping[str, object]: ...

    def mutate(
        self,
        *,
        principal: object,
        operation: str,
        resource_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def dispatch_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def graph(
        self,
        *,
        principal: object,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def query(
        self,
        *,
        principal: object,
        connection_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def artifact_status(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
    ) -> Mapping[str, object]: ...

    def artifact_download(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream: ...

    def codehug_mutation(
        self,
        *,
        principal: object,
        mutation_intent_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def bulk_plan(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def bulk_execute(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def poll_events(
        self,
        *,
        principal: object,
        after_sequence: int,
        limit: int,
    ) -> Mapping[str, object]: ...

    def access_preview(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def access_matrix(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def context_policy_list(
        self, *, principal: object, cursor: str | None, limit: int
    ) -> Mapping[str, object]: ...

    def context_policy_versions(
        self,
        *,
        principal: object,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def context_policy_detail(
        self, *, principal: object, policy_id: str, version: int
    ) -> tuple[Mapping[str, object], str]: ...

    def context_policy_active(
        self, *, principal: object, policy_id: str
    ) -> tuple[Mapping[str, object], str]: ...

    def context_policy_draft(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def context_policy_lint(
        self, *, principal: object, policy_id: str, version: int
    ) -> Mapping[str, object]: ...

    def context_policy_preview(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def context_policy_transition(
        self,
        *,
        principal: object,
        operation: str,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def context_policy_rollback(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...


__all__ = ["SourceControlV1Port"]

"""Operation dispatch, read operations, artifact download and CodeHug mutations of the Source Control API runtime.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)
from agent.services.source_control_operation_store import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_projection_service import (
    SourceControlProjectionService,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    operation_key,
    runtime_principal,
    wire,
    wire_digest,
)


class SourceControlOperationGateway:
    """Route scoped source operations to the configured Hub operation ports."""

    def __init__(
        self,
        *,
        projection: SourceControlProjectionService,
        idempotency: SQLSourceControlOperationStore,
        operations: object | None,
        catalogs: object | None,
        artifact_downloads: object | None,
        codehug_mutations: object | None,
    ) -> None:
        self._projection = projection
        self._idempotency = idempotency
        self._operations = operations
        self._catalogs = catalogs
        self._artifact_downloads = artifact_downloads
        self._codehug_mutations = codehug_mutations

    def dispatch_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        if operation == "run":
            self._catalog_service().require_index_profile(
                project_id=actor.project_id,
                profile_id=str(payload.get("index_profile_id") or ""),
            )
        projection = self._projection.get(
            principal=actor, connection_id=connection_id
        )
        self._projection.assert_if_match(projection, if_match)
        execute = getattr(self._operations, operation, None)
        if not callable(execute):
            execute = getattr(self._operations, "execute", None)
        if not callable(execute):
            raise SourceControlApiRuntimeError(
                "source_control_operation_unavailable", status_code=503
            )
        request_digest = wire_digest(
            {
                "operation": operation,
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
                "connection_id": connection_id,
                "if_match": if_match,
                "payload": payload,
            }
        )
        key = operation_key(
            "operation", actor.tenant_id, idempotency_key
        )
        claim = self._idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        kwargs = {
            "tenant_id": actor.tenant_id,
            "project_id": actor.project_id,
            "actor_id": actor.subject_id,
            "connection_id": connection_id,
            "payload": dict(payload),
            "idempotency_key": idempotency_key,
        }
        if not claim.claim_token:
            raise SourceControlApiRuntimeError(
                "idempotency_claim_token_missing", status_code=500
            )
        try:
            if getattr(self._operations, operation, None) is execute:
                raw = execute(**kwargs)
            else:
                raw = execute(operation=operation, **kwargs)
        except Exception:
            try:
                self._idempotency.release(
                    idempotency_key=key,
                    plan_digest=request_digest,
                    claim_token=claim.claim_token,
                )
            except Exception:
                pass
            raise
        result = {
            "operation": operation,
            "connection_id": connection_id,
            "receipt": wire(raw),
        }
        self._idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            claim_token=claim.claim_token,
            result=result,
        )
        return result

    def graph(
        self,
        *,
        principal: object,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="graph",
            connection_id=connection_id,
            parameters=parameters,
        )

    def query(
        self,
        *,
        principal: object,
        connection_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="query",
            connection_id=connection_id,
            parameters=payload,
        )

    def artifact_status(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
    ) -> Mapping[str, object]:
        return self._read_operation(
            principal=principal,
            operation="artifact_status",
            connection_id=connection_id,
            parameters={"artifact_id": artifact_id},
        )

    def artifact_download(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream:
        if self._artifact_downloads is None:
            raise SourceControlApiRuntimeError(
                "artifact_download_unavailable", status_code=503
            )
        self._projection.get(
            principal=runtime_principal(principal), connection_id=connection_id
        )
        open_stream = getattr(self._artifact_downloads, "open", None)
        if not callable(open_stream):
            raise SourceControlApiRuntimeError(
                "artifact_download_unavailable", status_code=503
            )
        stream = open_stream(
            principal=principal,
            connection_id=connection_id,
            artifact_id=artifact_id,
            range_header=range_header,
        )
        if not isinstance(stream, SourceControlArtifactStream):
            raise SourceControlApiRuntimeError(
                "artifact_download_result_invalid", status_code=502
            )
        return stream

    def codehug_mutation(
        self,
        *,
        principal: object,
        mutation_intent_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        if self._codehug_mutations is None:
            raise SourceControlApiRuntimeError(
                "codehug_mutation_unavailable", status_code=503
            )
        actor = runtime_principal(principal)
        request_digest = wire_digest(
            {
                "operation": "codehug_mutation",
                "scope": [
                    actor.tenant_id,
                    actor.project_id,
                    actor.subject_id,
                ],
                "mutation_intent_id": mutation_intent_id,
            }
        )
        key = operation_key(
            "codehug", actor.tenant_id, idempotency_key
        )
        claim = self._idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        execute = getattr(self._codehug_mutations, "execute", None)
        if not callable(execute):
            raise SourceControlApiRuntimeError(
                "codehug_mutation_unavailable", status_code=503
            )
        result = dict(
            execute(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                mutation_intent_id=mutation_intent_id,
            )
        )
        self._idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def _read_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        self._projection.get(
            principal=actor, connection_id=connection_id
        )
        method = getattr(self._operations, operation, None)
        if not callable(method):
            raise SourceControlApiRuntimeError(
                "source_control_operation_unavailable", status_code=503
            )
        value = wire(
            method(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                connection_id=connection_id,
                parameters=dict(parameters),
            )
        )
        if not isinstance(value, dict):
            raise SourceControlApiRuntimeError(
                "source_control_operation_result_invalid",
                status_code=502,
            )
        value.setdefault("text_alternative", "")
        value.setdefault(
            "artifact_status",
            {"state": "not_applicable", "reason_code": None},
        )
        return value

    def _catalog_service(self):
        if self._catalogs is None:
            raise SourceControlApiRuntimeError(
                "source_control_catalog_unavailable", status_code=503
            )
        return self._catalogs


__all__ = ["SourceControlOperationGateway"]

"""Authenticated Hub REST facade for model-intelligence jobs and artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from agent.auth import check_user_auth, get_request_auth_context
from agent.config import settings
from agent.services.model_analysis_job_service import (
    ModelAnalysisJobRecord,
    ModelAnalysisJobService,
    ModelAnalysisJobServiceError,
    build_model_analysis_job_service,
)
from agent.services.model_intelligence_artifact_store import (
    FileSystemModelIntelligenceArtifactStore,
    ModelIntelligenceArtifactRef,
    ModelIntelligenceArtifactStoreError,
    ModelIntelligenceArtifactStorePort,
)
from agent.services.model_intelligence_graph_service import (
    ModelGraphArtifact,
    ModelGraphEdge,
    ModelGraphError,
    ModelGraphNode,
    ModelGraphQueryService,
)
from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef

model_intelligence_bp = Blueprint(
    "model_intelligence",
    __name__,
    url_prefix="/api/model-intelligence",
)

_MAX_BODY_BYTES = 64 * 1024
_CREATE_SCHEMA = "ananta.model-intelligence.create-job.v1"
_CREATE_FIELDS = frozenset(
    {
        "schema",
        "extensions",
        "hub_task_id",
        "model_id",
        "analysis_kind",
        "profile_id",
        "request_sha256",
        "requested_artifact_kinds",
        "max_runtime_seconds",
        "max_output_bytes",
    }
)
_READ_CAPABILITY = "model_intelligence.read"
_WRITE_CAPABILITY = "model_intelligence.write"
_REPORT_KINDS = {
    "json": frozenset({"analysis.report", "model.report", "report.json"}),
    "html": frozenset({"model.report-html", "report.html"}),
}


class ModelIntelligenceRouteError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(reason_code)


@model_intelligence_bp.get("/capabilities")
@check_user_auth
def capabilities():
    try:
        _require_capability(write=False)
        return jsonify(
            {
                "ok": True,
                "data": {
                    "schema": "ananta.model-intelligence.api-capabilities.v1",
                    "capabilities": [
                        "artifact.read",
                        "graph.traverse",
                        "job.cancel",
                        "job.create",
                        "job.get",
                        "job.list",
                        "report.read",
                    ],
                    "limits": {
                        "max_graph_depth": 8,
                        "max_graph_nodes": 5000,
                        "max_graph_page_size": 100,
                        "max_graph_timeout_ms": 1000,
                        "max_job_page_size": 100,
                        "max_request_bytes": _MAX_BODY_BYTES,
                    },
                    "schemas": {
                        "create_request": _CREATE_SCHEMA,
                        "job": "ananta.model-intelligence.analysis-job.v1",
                        "job_status": "ananta.model-intelligence.job-status.v1",
                    },
                },
            }
        )
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.post("/jobs")
@check_user_auth
def create_job():
    try:
        _require_capability(write=True)
        tenant_id = _tenant_id()
        idempotency_key = _idempotency_key()
        body = _json_body()
        if set(body) - _CREATE_FIELDS or body.get("schema") != _CREATE_SCHEMA:
            raise ModelIntelligenceRouteError(
                "model_analysis_create_request_invalid"
            )
        job_id = _derived_job_id(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        try:
            job = AnalysisJob.model_validate(
                {
                    **body,
                    "schema": "ananta.model-intelligence.analysis-job.v1",
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                }
            )
        except ValidationError as exc:
            raise ModelIntelligenceRouteError(
                "model_analysis_create_request_invalid"
            ) from exc
        record = _job_service().submit(
            job,
            idempotency_key=idempotency_key,
        )
        response = jsonify({"ok": True, "data": {"job": _job_view(record)}})
        response.status_code = 202
        response.headers["ETag"] = _job_etag(record)
        response.headers["Location"] = (
            f"/api/model-intelligence/jobs/{record.job.job_id}"
        )
        return response
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get("/jobs")
@check_user_auth
def list_jobs():
    try:
        _require_capability(write=False)
        if set(request.args) - {"page_size", "cursor"}:
            raise ModelIntelligenceRouteError(
                "model_analysis_query_invalid"
            )
        page_size = _query_int(
            "page_size",
            default=50,
            minimum=1,
            maximum=100,
        )
        page = _job_service().list_page(
            tenant_id=_tenant_id(),
            page_size=page_size,
            cursor=request.args.get("cursor"),
        )
        return jsonify(
            {
                "ok": True,
                "data": {
                    "jobs": [_job_view(record) for record in page.items],
                    "next_cursor": page.next_cursor,
                },
            }
        )
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get("/jobs/<job_id>")
@check_user_auth
def get_job(job_id: str):
    try:
        _require_capability(write=False)
        record = _job_service().get(
            tenant_id=_tenant_id(),
            job_id=job_id,
        )
        etag = _job_etag(record)
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag})
        response = jsonify({"ok": True, "data": {"job": _job_view(record)}})
        response.headers["ETag"] = etag
        return response
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.post("/jobs/<job_id>/cancel")
@check_user_auth
def cancel_job(job_id: str):
    try:
        _require_capability(write=True)
        _idempotency_key()
        if _json_body(allow_empty=True):
            raise ModelIntelligenceRouteError(
                "model_analysis_cancel_body_invalid"
            )
        record = _job_service().get(
            tenant_id=_tenant_id(),
            job_id=job_id,
        )
        _require_if_match(record)
        cancelled, signal = _job_service().request_cancel(
            tenant_id=record.job.tenant_id,
            job_id=record.job.job_id,
            expected_version=record.version,
        )
        response = jsonify(
            {
                "ok": True,
                "data": {
                    "job": _job_view(cancelled),
                    "cancellation": (
                        signal.to_wire() if signal is not None else None
                    ),
                },
            }
        )
        response.headers["ETag"] = _job_etag(cancelled)
        return response
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get(
    "/jobs/<job_id>/artifacts/<artifact_id>"
)
@check_user_auth
def get_artifact(job_id: str, artifact_id: str):
    try:
        _require_capability(write=False)
        record, artifact = _job_artifact(job_id, artifact_id)
        metadata = _artifact_store().get_metadata(
            record.job.tenant_id,
            _store_ref(record.job.tenant_id, artifact),
        )
        etag = f'"sha256-{artifact.sha256}"'
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag})
        response = jsonify(
            {
                "ok": True,
                "data": {
                    "artifact": artifact.to_wire(),
                    "storage": metadata.to_dict(),
                },
            }
        )
        response.headers["ETag"] = etag
        return response
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get(
    "/jobs/<job_id>/artifacts/<artifact_id>/content"
)
@check_user_auth
def get_artifact_content(job_id: str, artifact_id: str):
    try:
        _require_capability(write=False)
        record, artifact = _job_artifact(job_id, artifact_id)
        etag = f'"sha256-{artifact.sha256}"'
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag})
        content = _artifact_store().get_bytes(
            record.job.tenant_id,
            _store_ref(record.job.tenant_id, artifact),
        )
        return Response(
            content,
            status=200,
            content_type=artifact.media_type,
            headers={
                "Cache-Control": "private, no-cache",
                "ETag": etag,
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get("/jobs/<job_id>/report")
@check_user_auth
def get_report(job_id: str):
    try:
        _require_capability(write=False)
        if set(request.args) - {"format"}:
            raise ModelIntelligenceRouteError(
                "model_analysis_query_invalid"
            )
        report_format = str(request.args.get("format") or "json").lower()
        if report_format not in _REPORT_KINDS:
            raise ModelIntelligenceRouteError(
                "model_analysis_report_format_invalid"
            )
        record = _job_service().get(
            tenant_id=_tenant_id(),
            job_id=job_id,
        )
        artifact = _find_artifact(
            record,
            kinds=_REPORT_KINDS[report_format],
        )
        etag = f'"sha256-{artifact.sha256}"'
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag})
        content = _artifact_store().get_bytes(
            record.job.tenant_id,
            _store_ref(record.job.tenant_id, artifact),
        )
        return Response(
            content,
            status=200,
            content_type=(
                "application/json"
                if report_format == "json"
                else "text/html"
            ),
            headers={
                "Cache-Control": "private, no-cache",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                "ETag": etag,
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        return _error_response(exc)


@model_intelligence_bp.get("/jobs/<job_id>/graph")
@check_user_auth
def get_graph(job_id: str):
    try:
        _require_capability(write=False)
        allowed = {
            "start_node_id",
            "max_depth",
            "max_nodes",
            "page_size",
            "cursor",
            "timeout_ms",
        }
        if set(request.args) - allowed:
            raise ModelIntelligenceRouteError(
                "model_analysis_query_invalid"
            )
        start_node_id = str(
            request.args.get("start_node_id") or ""
        ).strip()
        if not start_node_id:
            raise ModelIntelligenceRouteError(
                "model_graph_start_node_required"
            )
        record = _job_service().get(
            tenant_id=_tenant_id(),
            job_id=job_id,
        )
        artifact = _find_artifact(record, kinds=frozenset({"model.graph"}))
        content = _artifact_store().get_bytes(
            record.job.tenant_id,
            _store_ref(record.job.tenant_id, artifact),
        )
        graph = _graph_artifact(content, model_id=record.job.model_id)
        result = ModelGraphQueryService(graph).traverse(
            start_node_id=start_node_id,
            max_depth=_query_int(
                "max_depth", default=1, minimum=0, maximum=8
            ),
            max_nodes=_query_int(
                "max_nodes", default=100, minimum=1, maximum=5000
            ),
            page_size=_query_int(
                "page_size", default=50, minimum=1, maximum=100
            ),
            cursor=request.args.get("cursor"),
            timeout_ms=_query_int(
                "timeout_ms", default=200, minimum=1, maximum=1000
            ),
        )
        etag = _graph_etag(artifact, request.args)
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag})
        response = jsonify(
            {
                "ok": True,
                "data": {
                    "graph": {
                        "job_id": record.job.job_id,
                        "model_id": graph.model_id,
                        **result.to_dict(),
                    }
                },
            }
        )
        response.headers["ETag"] = etag
        return response
    except Exception as exc:
        return _error_response(exc)


def _job_service() -> ModelAnalysisJobService:
    configured = current_app.extensions.get(
        "model_analysis_job_service"
    )
    if configured is not None:
        return configured
    service = build_model_analysis_job_service()
    current_app.extensions["model_analysis_job_service"] = service
    return service


def _artifact_store() -> ModelIntelligenceArtifactStorePort:
    configured = current_app.extensions.get(
        "model_intelligence_artifact_store"
    )
    if configured is not None:
        return configured
    root = current_app.config.get("MODEL_INTELLIGENCE_ARTIFACT_ROOT")
    store = FileSystemModelIntelligenceArtifactStore(
        root=Path(root) if root else Path(settings.data_dir) / "model-intelligence-artifacts"
    )
    current_app.extensions["model_intelligence_artifact_store"] = store
    return store


def _identity() -> dict[str, Any]:
    return dict(get_request_auth_context() or {})


def _tenant_id() -> str:
    identity = _identity()
    subject = str(
        identity.get("sub") or identity.get("username") or ""
    ).strip()
    tenant = str(
        identity.get("tenant_id")
        or identity.get("tenant")
        or subject
    ).strip()
    if not subject or not tenant:
        raise ModelIntelligenceRouteError(
            "model_intelligence_unauthenticated",
            status_code=401,
        )
    return tenant


def _require_capability(*, write: bool = False) -> None:
    """Authorize the current request through the shared tenant-scoped policy."""
    from agent.services.model_intelligence_security_policy import (
        ModelIntelligenceAccessPolicy,
        ModelIntelligenceAction,
        ModelIntelligencePrincipal,
        ModelIntelligenceResourceKind,
        ModelIntelligenceResourceScope,
        ModelIntelligenceRole,
        ModelIntelligenceSecurityError,
    )

    identity = _identity()
    claims = identity if isinstance(identity, dict) else {"sub": str(identity)}
    subject_id = str(
        claims.get("sub")
        or claims.get("username")
        or claims.get("user_id")
        or "authenticated"
    )
    tenant_id = _tenant_id()

    raw_roles: list[str] = []
    for key in ("role", "roles"):
        value = claims.get(key)
        if isinstance(value, str):
            raw_roles.extend(part for part in value.replace(",", " ").split() if part)
        elif isinstance(value, (list, tuple, set, frozenset)):
            raw_roles.extend(str(part) for part in value)

    raw_capabilities: list[str] = []
    for key in ("capabilities", "permissions", "scope", "scopes"):
        value = claims.get(key)
        if isinstance(value, str):
            raw_capabilities.extend(part for part in value.replace(",", " ").split() if part)
        elif isinstance(value, (list, tuple, set, frozenset)):
            raw_capabilities.extend(str(part) for part in value)

    normalized_roles = {value.strip().lower().replace("-", "_") for value in raw_roles}
    normalized_capabilities = {value.strip().lower() for value in raw_capabilities}
    policy_roles: set[ModelIntelligenceRole] = set()
    if normalized_roles & {"admin", "tenant_admin", "superadmin"} or bool(
        getattr(g, "is_admin", False)
    ):
        policy_roles.add(ModelIntelligenceRole.TENANT_ADMIN)
    if "operator" in normalized_roles:
        policy_roles.add(ModelIntelligenceRole.OPERATOR)
    if "analyst" in normalized_roles or "model_intelligence.write" in normalized_capabilities:
        policy_roles.add(ModelIntelligenceRole.ANALYST)
    if "viewer" in normalized_roles or "model_intelligence.read" in normalized_capabilities:
        policy_roles.add(ModelIntelligenceRole.VIEWER)
    if not policy_roles:
        raise ModelIntelligenceRouteError(
            "rbac_action_denied",
            status_code=403,
        )

    endpoint = str(request.endpoint or "")
    path = request.path.rstrip("/")
    view_args = request.view_args or {}
    job_id = str(view_args.get("job_id") or "jobs")
    artifact_id = str(view_args.get("artifact_id") or job_id)

    if write:
        if endpoint.endswith("cancel_job") or path.endswith("/cancel"):
            action = ModelIntelligenceAction.CANCEL_ANALYSIS
        else:
            action = ModelIntelligenceAction.SUBMIT_ANALYSIS
        kind = ModelIntelligenceResourceKind.JOB
        resource_id = job_id
    elif endpoint.endswith("get_report") or path.endswith("/report"):
        action = ModelIntelligenceAction.READ_REPORT
        kind = ModelIntelligenceResourceKind.REPORT
        resource_id = job_id
    elif "artifact" in endpoint or "/artifacts/" in path or path.endswith("/graph"):
        action = ModelIntelligenceAction.READ_ARTIFACT
        kind = ModelIntelligenceResourceKind.ARTIFACT
        resource_id = artifact_id
    else:
        action = ModelIntelligenceAction.READ_JOB
        kind = ModelIntelligenceResourceKind.JOB
        resource_id = job_id

    try:
        principal = ModelIntelligencePrincipal(
            subject_id=subject_id,
            tenant_id=tenant_id,
            roles=frozenset(policy_roles),
        )
        ModelIntelligenceAccessPolicy().require(
            principal,
            ModelIntelligenceResourceScope(
                tenant_id=tenant_id,
                kind=kind,
                resource_id=resource_id,
            ),
            action,
        )
    except ModelIntelligenceSecurityError as exc:
        status = 404 if exc.reason_code == "tenant_scope_mismatch" else 403
        raise ModelIntelligenceRouteError(
            exc.reason_code,
            status_code=status,
        ) from exc


def _json_body(*, allow_empty: bool = False) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        raise ModelIntelligenceRouteError(
            "model_intelligence_request_too_large",
            status_code=413,
        )
    if allow_empty and not request.data:
        return {}
    body = request.get_json(silent=True)
    if not isinstance(body, Mapping) or any(
        not isinstance(key, str) for key in body
    ):
        raise ModelIntelligenceRouteError(
            "model_intelligence_json_invalid"
        )
    return dict(body)


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not value:
        raise ModelIntelligenceRouteError(
            "model_analysis_idempotency_key_required"
        )
    return value


def _derived_job_id(*, tenant_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "idempotency_key": idempotency_key,
                "tenant_id": tenant_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    return f"analysis-{digest[:48]}"


def _query_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ModelIntelligenceRouteError(
            "model_analysis_query_integer_invalid"
        ) from exc
    if not minimum <= value <= maximum:
        raise ModelIntelligenceRouteError(
            "model_analysis_query_integer_invalid"
        )
    return value


def _job_view(record: ModelAnalysisJobRecord) -> dict[str, Any]:
    completion = record.completion
    artifacts = (
        [artifact.to_wire() for artifact in completion.artifacts]
        if completion is not None
        else []
    )
    return {
        "schema": "ananta.model-intelligence.job-status.v1",
        "job": record.job.to_wire(),
        "status": record.state.value,
        "version": record.version,
        "attempt": record.attempt,
        "reason_code": record.reason_code,
        "artifacts": artifacts,
        "error": (
            completion.error.to_wire()
            if completion is not None and completion.error is not None
            else None
        ),
    }


def _job_etag(record: ModelAnalysisJobRecord) -> str:
    return f'W/"{record.job.job_id}:{record.version}"'


def _etag_matches(raw: str | None, expected: str) -> bool:
    return expected in {
        item.strip()
        for item in str(raw or "").split(",")
        if item.strip()
    }


def _require_if_match(record: ModelAnalysisJobRecord) -> None:
    raw = str(request.headers.get("If-Match") or "").strip()
    if not raw:
        raise ModelIntelligenceRouteError(
            "model_analysis_precondition_required",
            status_code=428,
        )
    if not _etag_matches(raw, _job_etag(record)):
        raise ModelIntelligenceRouteError(
            "model_analysis_precondition_failed",
            status_code=412,
        )


def _job_artifact(
    job_id: str,
    artifact_id: str,
) -> tuple[ModelAnalysisJobRecord, ArtifactRef]:
    record = _job_service().get(
        tenant_id=_tenant_id(),
        job_id=job_id,
    )
    artifact = _find_artifact(
        record,
        artifact_id=artifact_id,
    )
    return record, artifact


def _find_artifact(
    record: ModelAnalysisJobRecord,
    *,
    artifact_id: str | None = None,
    kinds: frozenset[str] | None = None,
) -> ArtifactRef:
    completion = record.completion
    if completion is None:
        raise ModelIntelligenceRouteError(
            "model_analysis_artifact_not_found",
            status_code=404,
        )
    matches = [
        artifact
        for artifact in completion.artifacts
        if (
            artifact_id is None or artifact.artifact_id == artifact_id
        )
        and (kinds is None or artifact.kind in kinds)
    ]
    if len(matches) != 1:
        raise ModelIntelligenceRouteError(
            "model_analysis_artifact_not_found",
            status_code=404,
        )
    return matches[0]


def _store_ref(
    tenant_id: str,
    artifact: ArtifactRef,
) -> ModelIntelligenceArtifactRef:
    return ModelIntelligenceArtifactRef(
        digest=f"sha256:{artifact.sha256}",
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        tenant_scope=hashlib.sha256(
            tenant_id.encode("utf-8")
        ).hexdigest(),
        artifact_kind=artifact.kind,
    )


def _graph_artifact(content: bytes, *, model_id: str) -> ModelGraphArtifact:
    if len(content) > 64 * 1024 * 1024:
        raise ModelIntelligenceRouteError(
            "model_graph_artifact_too_large",
            status_code=413,
        )
    try:
        raw = json.loads(content.decode("utf-8"))
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema_version") != "model_graph.v1"
            or raw.get("model_id") != model_id
            or not isinstance(raw.get("nodes"), list)
            or not isinstance(raw.get("edges"), list)
            or len(raw["nodes"]) > 100_000
            or len(raw["edges"]) > 250_000
        ):
            raise ValueError
        nodes = tuple(
            ModelGraphNode(
                node_id=str(node["node_id"]),
                kind=str(node["kind"]),
                label=str(node["label"]),
                attributes=dict(node.get("attributes") or {}),
            )
            for node in raw["nodes"]
            if isinstance(node, Mapping)
        )
        edges = tuple(
            ModelGraphEdge(
                edge_id=str(edge["edge_id"]),
                source_node_id=str(edge["source_node_id"]),
                target_node_id=str(edge["target_node_id"]),
                kind=str(edge["kind"]),
            )
            for edge in raw["edges"]
            if isinstance(edge, Mapping)
        )
        if len(nodes) != len(raw["nodes"]) or len(edges) != len(raw["edges"]):
            raise ValueError
        return ModelGraphArtifact(
            schema_version="model_graph.v1",
            model_id=model_id,
            nodes=nodes,
            edges=edges,
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelIntelligenceRouteError(
            "model_graph_artifact_invalid"
        ) from exc


def _graph_etag(
    artifact: ArtifactRef,
    query: Mapping[str, Any],
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "artifact_sha256": artifact.sha256,
                "query": sorted((str(key), str(value)) for key, value in query.items()),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    return f'"graph-{digest}"'


def _error_response(exc: Exception):
    if isinstance(exc, ModelIntelligenceRouteError):
        reason = exc.reason_code
        status = exc.status_code
        retryable = exc.retryable
    elif isinstance(exc, ModelAnalysisJobServiceError):
        reason = exc.reason_code
        retryable = exc.retryable
        if reason == "model_analysis_job_not_found":
            status = 404
        elif reason in {
            "model_analysis_global_queue_full",
            "model_analysis_tenant_queue_full",
            "model_analysis_tenant_active_limit",
        }:
            status = 429
        elif reason == "model_analysis_task_submission_unavailable":
            status = 503
        elif reason.endswith("_conflict"):
            status = 409
        else:
            status = 400
    elif isinstance(exc, ModelIntelligenceArtifactStoreError):
        reason = exc.reason_code
        retryable = reason in {"artifact_read_failed", "artifact_write_failed"}
        status = 503 if retryable else 404 if reason in {
            "artifact_not_found",
            "artifact_access_denied",
        } else 400
    elif isinstance(exc, ModelGraphError):
        reason = exc.code
        retryable = reason == "model_graph_query_timeout"
        status = 504 if retryable else 404 if reason == "model_graph_start_node_missing" else 400
    else:
        reason = "model_intelligence_internal_error"
        status = 500
        retryable = False
    return jsonify(
        {
            "ok": False,
            "error": {
                "reason_code": reason,
                "retryable": retryable,
                "details": {},
            },
        }
    ), status


__all__ = ["model_intelligence_bp"]


def _model_intelligence_event_context(response: Response) -> tuple[str, str, str | None, str | None]:
    """Return only bounded, non-content event dimensions for the current request."""
    path = request.path.rstrip("/")
    endpoint = str(request.endpoint or "")
    if request.method == "POST" and path.endswith("/cancel"):
        action, resource_kind = "cancel_analysis", "job"
    elif request.method == "POST" and path.endswith("/jobs"):
        action, resource_kind = "submit_analysis", "job"
    elif endpoint.endswith("get_report") or path.endswith("/report"):
        action, resource_kind = "read_report", "report"
    elif "artifact" in endpoint or "/artifacts/" in path or path.endswith("/graph"):
        action, resource_kind = "read_artifact", "artifact"
    else:
        action, resource_kind = "read_job", "job"

    payload = response.get_json(silent=True) if response.is_json else None
    job_payload = payload.get("job", payload) if isinstance(payload, dict) else {}
    job_id = job_payload.get("job_id") if isinstance(job_payload, dict) else None
    state = job_payload.get("state") if isinstance(job_payload, dict) else None
    if not isinstance(job_id, str):
        candidate = (request.view_args or {}).get("job_id")
        job_id = candidate if isinstance(candidate, str) else None
    if not isinstance(state, str):
        state = None
    return action, resource_kind, job_id, state


@model_intelligence_bp.after_request
def _emit_sanitized_model_intelligence_events(response: Response) -> Response:
    """Best-effort emission through injected ports; observability never changes API results."""
    from hashlib import sha256

    from agent.services.model_analysis_task_port import HubModelAnalysisTaskSubmissionPort
    from agent.services.model_intelligence_observability import (
        HmacModelIntelligenceCorrelationService,
        ModelIntelligenceOperationalEvent,
    )
    from agent.services.model_intelligence_security_policy import (
        sanitize_model_intelligence_audit_event,
    )

    try:
        action, resource_kind, job_id, state = _model_intelligence_event_context(response)
        outcome = "success" if response.status_code < 400 else "error"
        audit_fields: dict[str, object] = {
            "action": action,
            "resource_kind": resource_kind,
            "outcome": outcome,
        }
        if state:
            audit_fields["state"] = state
        if response.status_code == 403:
            audit_fields["reason_code"] = "policy_denied"
        audit_event = sanitize_model_intelligence_audit_event(
            "model_intelligence_api_request",
            audit_fields,
        )
        audit_port = current_app.extensions.get("model_intelligence_audit_event_port")
        audit_emit = getattr(audit_port, "emit", None)
        if callable(audit_emit):
            audit_emit(audit_event)

        is_submission = request.method == "POST" and request.path.rstrip("/").endswith("/jobs")
        is_cancellation = request.method == "POST" and request.path.rstrip("/").endswith("/cancel")
        operational_port = current_app.extensions.get(
            "model_intelligence_operational_event_port"
        )
        operational_emit = getattr(operational_port, "emit", None)
        if (
            callable(operational_emit)
            and response.status_code < 400
            and job_id
            and (is_submission or is_cancellation)
        ):
            correlation_service = current_app.extensions.get(
                "model_intelligence_correlation_service"
            )
            if correlation_service is None:
                configured_secret = current_app.config.get(
                    "MODEL_INTELLIGENCE_CORRELATION_SECRET"
                ) or current_app.secret_key
                if configured_secret:
                    secret_bytes = (
                        configured_secret
                        if isinstance(configured_secret, bytes)
                        else str(configured_secret).encode("utf-8")
                    )
                    correlation_service = HmacModelIntelligenceCorrelationService(
                        sha256(secret_bytes).digest()
                    )
                    current_app.extensions[
                        "model_intelligence_correlation_service"
                    ] = correlation_service
            if correlation_service is not None:
                correlation = correlation_service.correlate(
                    hub_job_id=job_id,
                    worker_task_id=HubModelAnalysisTaskSubmissionPort.execution_task_id(
                        job_id
                    ),
                )
                normalized_state = {
                    "submission_pending": "queued",
                    "queued": "queued",
                    "running": "running",
                    "cancel_requested": "running",
                    "succeeded": "succeeded",
                    "failed": "failed",
                    "cancelled": "cancelled",
                }.get(state or "", "queued" if is_submission else "running")
                operational_emit(
                    ModelIntelligenceOperationalEvent(
                        state=normalized_state,
                        reason_code="accepted" if is_submission else "cancelled",
                        correlation=correlation,
                        duration_seconds=0,
                    )
                )
    except Exception:
        current_app.logger.debug(
            "model-intelligence event emission failed",
            exc_info=True,
        )
    return response



def _model_intelligence_request_aliases() -> None:
    """Accept the bounded Angular request DTO without changing the domain contract."""
    if request.method != "POST" or not request.path.rstrip("/").endswith("/jobs"):
        return
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return
    import_ref = payload.pop("import_ref", None)
    if isinstance(import_ref, str) and import_ref:
        payload.setdefault("model_id", import_ref)
    requested = payload.get("requested_artifacts")
    if not isinstance(requested, list):
        requested = payload.get("artifact_kinds")
    if isinstance(requested, list):
        payload.setdefault("requested_artifact_kinds", requested)
    payload.pop("requested_artifacts", None)
    payload.pop("artifact_kinds", None)


model_intelligence_bp.before_request(_model_intelligence_request_aliases)


def _bounded_integer(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, maximum))


def _frontend_job_dto(source: object) -> dict[str, object]:
    value = source if isinstance(source, dict) else {}
    extensions = value.get("extensions") if isinstance(value.get("extensions"), dict) else {}
    identity = (
        value.get("model_identity")
        if isinstance(value.get("model_identity"), dict)
        else {}
    )
    state = str(value.get("status") or value.get("state") or "unknown")
    status = {
        "submission_pending": "queued",
        "queued": "queued",
        "claimed": "claimed",
        "running": "running",
        "cancel_requested": "cancel_requested",
        "succeeded": "completed",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(state, "unknown")
    default_progress = {
        "queued": 0,
        "claimed": 5,
        "running": 50,
        "cancel_requested": 50,
        "completed": 100,
        "failed": 100,
        "cancelled": 100,
        "unknown": 0,
    }[status]
    requested = (
        value.get("requested_artifact_kinds")
        or value.get("requested_artifacts")
        or value.get("artifact_kinds")
        or []
    )
    if not isinstance(requested, (list, tuple)):
        requested = []
    job_id = str(value.get("job_id") or "unknown")
    model_id = (
        value.get("model_id")
        or identity.get("model_id")
        or identity.get("identity_id")
        or extensions.get("import_ref")
        or job_id
    )
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    reason_code = value.get("reason_code") or error.get("reason_code")
    result: dict[str, object] = {
        "job_id": job_id,
        "model_id": str(model_id),
        "analysis_kind": str(value.get("analysis_kind") or "full"),
        "profile_id": str(value.get("profile_id") or extensions.get("profile_id") or "bounded-ui"),
        "requested_artifact_kinds": [str(item) for item in requested],
        "status": status,
        "progress_percent": _bounded_integer(
            value.get("progress_percent"),
            default=default_progress,
            maximum=100,
        ),
    }
    optional_values = {
        "schema": value.get("schema"),
        "hub_task_id": value.get("hub_task_id"),
        "import_ref": value.get("import_ref") or extensions.get("import_ref"),
        "request_sha256": value.get("request_sha256") or value.get("request_digest"),
        "max_runtime_seconds": value.get("max_runtime_seconds"),
        "max_output_bytes": value.get("max_output_bytes"),
        "reason_code": reason_code,
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
    }
    result.update({key: item for key, item in optional_values.items() if item is not None})
    return result


def _frontend_report_dto(source: object) -> dict[str, object]:
    from hashlib import sha256
    import json

    value = source if isinstance(source, dict) else {}
    nested = value.get("report") if isinstance(value.get("report"), dict) else value
    raw_sections = nested.get("sections") if isinstance(nested, dict) else []
    if not isinstance(raw_sections, list):
        raw_sections = []
    sections: list[dict[str, object]] = []
    for index, item in enumerate(raw_sections):
        section = item if isinstance(item, dict) else {"data": item}
        raw_status = str(section.get("status") or "available")
        status = raw_status if raw_status in {"available", "unsupported", "not_run", "failed"} else "failed"
        normalized: dict[str, object] = {
            "name": str(section.get("name") or section.get("section") or f"section-{index + 1}"),
            "status": status,
            "data": section.get("data"),
        }
        if section.get("reason_code") is not None:
            normalized["reason_code"] = str(section["reason_code"])
        sections.append(normalized)
    schema = str(nested.get("schema") or "ananta.model-intelligence.report.v1")
    digest = nested.get("content_digest") or nested.get("sha256")
    if not isinstance(digest, str) or not digest:
        canonical = json.dumps(
            {"schema": schema, "sections": sections},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        digest = f"sha256:{sha256(canonical).hexdigest()}"
    return {"schema": schema, "content_digest": digest, "sections": sections}


def _frontend_graph_dto(source: object) -> dict[str, object]:
    value = source if isinstance(source, dict) else {}
    nested = value.get("graph") if isinstance(value.get("graph"), dict) else value
    raw_nodes = nested.get("nodes") if isinstance(nested, dict) else []
    raw_edges = nested.get("edges") if isinstance(nested, dict) else []
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    if isinstance(raw_nodes, list):
        for item in raw_nodes:
            node = item if isinstance(item, dict) else {}
            node_id = str(node.get("node_id") or node.get("id") or "")
            if node_id:
                nodes.append(
                    {
                        "node_id": node_id,
                        "label": str(node.get("label") or node.get("name") or node_id),
                        "kind": str(node.get("kind") or node.get("type") or "unknown"),
                    }
                )
    if isinstance(raw_edges, list):
        for item in raw_edges:
            edge = item if isinstance(item, dict) else {}
            source_id = str(edge.get("source_node_id") or edge.get("source") or "")
            target_id = str(edge.get("target_node_id") or edge.get("target") or "")
            if source_id and target_id:
                edge_id = str(
                    edge.get("edge_id")
                    or edge.get("id")
                    or f"{source_id}:{target_id}:{len(edges)}"
                )
                edges.append(
                    {
                        "edge_id": edge_id,
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                        "kind": str(edge.get("kind") or edge.get("type") or "unknown"),
                    }
                )
    return {
        "schema": str(nested.get("schema") or "ananta.model-intelligence.graph.v1"),
        "nodes": nodes,
        "edges": edges,
        "truncated": bool(nested.get("truncated", False)),
    }


def _replace_json_response(response: Response, payload: object) -> Response:
    response.set_data(current_app.json.dumps(payload))
    response.content_type = "application/json"
    response.content_length = len(response.get_data())
    return response


@model_intelligence_bp.after_request
def _normalize_model_intelligence_frontend_dtos(response: Response) -> Response:
    if response.status_code >= 400 or response.status_code == 304 or not response.is_json:
        return response
    payload = response.get_json(silent=True)
    if (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and isinstance(payload.get("data"), dict)
    ):
        return response
    path = request.path.rstrip("/")
    base = "/api/model-intelligence"
    if path == f"{base}/capabilities" and isinstance(payload, dict):
        nested = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else payload
        limits = nested.get("limits") if isinstance(nested.get("limits"), dict) else {}
        normalized: dict[str, object] = {
            "supported": bool(nested.get("supported", True)),
            "max_graph_nodes": _bounded_integer(
                nested.get("max_graph_nodes") or limits.get("max_graph_nodes"),
                default=int(current_app.config.get("MODEL_INTELLIGENCE_MAX_GRAPH_NODES", 500)),
                maximum=10_000,
            ),
            "max_graph_edges": _bounded_integer(
                nested.get("max_graph_edges") or limits.get("max_graph_edges"),
                default=int(current_app.config.get("MODEL_INTELLIGENCE_MAX_GRAPH_EDGES", 1_000)),
                maximum=20_000,
            ),
        }
        if nested.get("reason_code") is not None:
            normalized["reason_code"] = str(nested["reason_code"])
        return _replace_json_response(response, normalized)
    if path == f"{base}/jobs" and request.method == "GET" and isinstance(payload, dict):
        raw_items = payload.get("items") or payload.get("jobs") or []
        items = [_frontend_job_dto(item) for item in raw_items] if isinstance(raw_items, list) else []
        return _replace_json_response(
            response,
            {"items": items, "next_cursor": payload.get("next_cursor")},
        )
    if (
        (path == f"{base}/jobs" and request.method == "POST")
        or (path.startswith(f"{base}/jobs/") and path.endswith("/cancel"))
        or (
            path.startswith(f"{base}/jobs/")
            and "/artifacts/" not in path
            and not path.endswith("/report")
            and not path.endswith("/graph")
        )
    ):
        source = payload.get("job", payload) if isinstance(payload, dict) else payload
        return _replace_json_response(response, _frontend_job_dto(source))
    if path.endswith("/report"):
        return response
    if path.endswith("/graph"):
        return _replace_json_response(response, _frontend_graph_dto(payload))
    return response

from __future__ import annotations

import base64
import hashlib
import os
import stat
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import Blueprint, current_app, g, request, send_file

from agent.auth import check_auth, check_registered_worker_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.models import ArtifactRagIndexRequest, ArtifactUploadRequest
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
)
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.remote_federation_policy_service import get_remote_federation_policy_service
from agent.services.repository_registry import get_repository_registry
from agent.services.retrieval_orchestration_contract import build_retrieval_orchestration_contract
from agent.services.retrieval_service import get_retrieval_service
from agent.services.service_registry import get_core_services
from agent.services.workflow_worker_service_auth import (
    KNOWLEDGE_INDEX_PAYLOAD_SCOPE,
)
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)
from ananta_contracts.knowledge_index_payload_capability import (
    KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER,
    decode_knowledge_index_payload_capability,
)
from ananta_contracts.knowledge_index_worker_output_capability import (
    KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER,
    decode_knowledge_index_output_capability,
)
from ananta_contracts.knowledge_index_legacy_output_capability import (
    KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER,
)

artifacts_bp = Blueprint("artifacts", __name__)
_KNOWLEDGE_INDEX_PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
_KNOWLEDGE_INDEX_PAYLOAD_MAX_BYTES = MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES
_KNOWLEDGE_INDEX_OUTPUT_MAX_BYTES = 128 * 1024 * 1024


def _read_pinned_verified_artifact_bytes(
    storage_path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    maximum_size: int,
    not_found_reason: str,
    integrity_reason: str,
) -> bytes:
    """Read and verify one regular file without reopening its path."""

    normalized_digest = str(expected_sha256 or "").strip().lower()
    if (
        expected_size < 0
        or expected_size > maximum_size
        or len(normalized_digest) != 64
        or any(character not in "0123456789abcdef" for character in normalized_digest)
    ):
        raise NotFoundError(integrity_reason)

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        # Internal capability-bound artifacts fail closed when the platform
        # cannot guarantee that the final path component is not a symlink.
        raise NotFoundError(not_found_reason)

    descriptor: int | None = None
    try:
        descriptor = os.open(
            storage_path,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
        )
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = None
        with handle:
            before = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != expected_size
            ):
                raise NotFoundError(integrity_reason)
            content = handle.read(expected_size + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise NotFoundError(not_found_reason) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or after.st_nlink != 1
        or after.st_size != expected_size
        or len(content) != expected_size
        or hashlib.sha256(content).hexdigest() != normalized_digest
    ):
        raise NotFoundError(integrity_reason)
    return content


def _public_artifact(artifact_id: str):
    artifact = _artifact_repo().get_by_id(artifact_id)
    if not is_artifact_visible_on_generic_surfaces(artifact):
        return None
    return artifact


def _require_public_artifact(artifact_id: str):
    artifact = _public_artifact(artifact_id)
    if artifact is None:
        raise NotFoundError()
    return artifact


def _check_knowledge_index_payload_access(target):
    @wraps(target)
    def wrapper(artifact_id: str, *args, **kwargs):
        encoded_capability = str(
            request.headers.get(
                KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER
            )
            or ""
        ).strip()
        if not encoded_capability:
            try:
                identity = dict(getattr(g, "service_identity", {}) or {})
                authorizer = current_app.extensions.get(
                    "legacy_knowledge_index_payload_assignment_authorizer"
                )
                if authorizer is None:
                    raise ValueError(
                        "legacy_knowledge_index_payload_authorizer_unavailable"
                    )
                authorizer.authorize(
                    artifact_id=artifact_id,
                    worker_id=str(identity.get("worker_id") or ""),
                    worker_url=str(identity.get("worker_url") or ""),
                )
            except Exception as exc:
                reason_code = str(
                    getattr(exc, "reason_code", "")
                    or "knowledge_index_payload_capability_required"
                )
                status_code = int(getattr(exc, "status_code", 401) or 401)
                return api_response(
                    status="error",
                    message=(
                        "forbidden" if status_code == 403 else "unauthorized"
                    ),
                    data={"reason_code": reason_code},
                    code=status_code,
                )
            return target(artifact_id, *args, **kwargs)
        try:
            manifest = decode_knowledge_index_payload_capability(
                encoded_capability
            )
            artifact = _artifact_repo().get_by_id(artifact_id)
            latest_version_id = str(
                getattr(artifact, "latest_version_id", None) or ""
            ).strip()
            version = (
                _artifact_version_repo().get_by_id(latest_version_id)
                if latest_version_id
                else None
            )
            authorizer = current_app.extensions.get(
                "knowledge_index_payload_capability_authorizer"
            )
            if authorizer is None or artifact is None or version is None:
                raise ValueError(
                    "knowledge_index_payload_authorizer_unavailable"
                )
            authorizer.authorize(
                artifact_id=artifact_id,
                artifact_sha256=str(version.sha256 or ""),
                artifact_size_bytes=int(version.size_bytes or 0),
                artifact_media_type=str(version.media_type or ""),
                manifest=manifest,
                # The outer registered-Worker boundary authenticates and
                # normalizes this identity.  Never re-trust caller-controlled
                # identity headers inside the capability decision.
                worker_id=str(
                    getattr(g, "auth_payload", {}).get("worker_id") or ""
                ),
                worker_url=str(
                    getattr(g, "auth_payload", {}).get("worker_url") or ""
                ),
            )
        except Exception as exc:
            reason_code = str(
                getattr(
                    exc,
                    "reason_code",
                    "knowledge_index_payload_capability_invalid",
                )
            )
            status_code = int(getattr(exc, "status_code", 401))
            return api_response(
                status="error",
                message="unauthorized",
                data={"reason_code": reason_code},
                code=status_code,
            )
        return target(artifact_id, *args, **kwargs)

    return wrapper


def _check_knowledge_index_worker_output_access(target):
    @wraps(target)
    def wrapper(artifact_id: str, *args, **kwargs):
        encoded_capability = str(
            request.headers.get(KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER) or ""
        ).strip()
        try:
            artifact = _artifact_repo().get_by_id(artifact_id)
            metadata = dict(
                getattr(artifact, "artifact_metadata", None) or {}
            ) if artifact else {}
            latest_version_id = str(
                getattr(artifact, "latest_version_id", None) or ""
            ).strip()
            version = (
                _artifact_version_repo().get_by_id(latest_version_id)
                if latest_version_id
                else None
            )
            authorizer = current_app.extensions.get(
                "knowledge_index_worker_output_capability_authorizer"
            )
            if authorizer is None or artifact is None or version is None:
                raise ValueError(
                    "knowledge_index_output_authorizer_unavailable"
                )
            request_binding = {
                "job_id": str(
                    request.headers.get(KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER)
                    or ""
                ),
                "knowledge_index_id": str(
                    request.headers.get(KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER)
                    or ""
                ),
                "run_id": str(
                    request.headers.get(KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER)
                    or ""
                ),
                "output_role": str(
                    request.headers.get(KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER)
                    or ""
                ),
            }
            if not encoded_capability:
                legacy = current_app.extensions.get(
                    "legacy_knowledge_index_worker_output_assignment_authorizer"
                )
                if legacy is None:
                    raise ValueError("knowledge_index_output_authorizer_unavailable")
                legacy.authorize(
                    artifact_id=artifact_id,
                    artifact_sha256=str(
                        request.headers.get(KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER)
                        or ""
                    ),
                    artifact_size_bytes=str(
                        request.headers.get(KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER)
                        or ""
                    ),
                    artifact_media_type=str(
                        request.headers.get(KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER)
                        or ""
                    ),
                    artifact_metadata=metadata,
                    encoded_capability=str(
                        request.headers.get(
                            KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER
                        )
                        or ""
                    ),
                    **request_binding,
                )
            else:
                manifest = decode_knowledge_index_output_capability(
                    encoded_capability
                )
                authorizer.authorize(
                    artifact_id=artifact_id,
                    artifact_sha256=str(
                        request.headers.get(
                            KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER
                        )
                        or ""
                    ),
                    artifact_size_bytes=str(
                        request.headers.get(KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER)
                        or ""
                    ),
                    artifact_media_type=str(
                        request.headers.get(
                            KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER
                        )
                        or ""
                    ),
                    artifact_metadata=metadata,
                    manifest=manifest,
                    **request_binding,
                )
            if (
                str(version.sha256 or "").lower()
                != str(
                    request.headers.get(
                        KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER
                    )
                    or ""
                ).lower()
                or int(version.size_bytes or 0)
                != int(
                    request.headers.get(KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER)
                    or -1
                )
                or str(version.media_type or "").lower()
                != str(
                    request.headers.get(
                        KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER
                    )
                    or ""
                ).lower()
            ):
                raise ValueError(
                    "knowledge_index_output_version_binding_mismatch"
                )
        except Exception as exc:
            reason_code = str(
                getattr(
                    exc,
                    "reason_code",
                    "knowledge_index_output_capability_invalid",
                )
            )
            status_code = int(getattr(exc, "status_code", 401))
            return api_response(
                status="error",
                message="unauthorized",
                data={"reason_code": reason_code},
                code=status_code,
            )
        return target(artifact_id, *args, **kwargs)

    return wrapper


def get_ingestion_service():
    return get_core_services().ingestion_service


def get_knowledge_index_job_service():
    return get_core_services().knowledge_index_job_service


def get_rag_helper_index_service():
    return get_core_services().rag_helper_index_service


def _artifact_repo():
    return get_repository_registry().artifact_repo


def _artifact_version_repo():
    return get_repository_registry().artifact_version_repo


def _extracted_document_repo():
    return get_repository_registry().extracted_document_repo


def _knowledge_index_repo():
    return get_repository_registry().knowledge_index_repo


def _knowledge_index_run_repo():
    return get_repository_registry().knowledge_index_run_repo


def _knowledge_link_repo():
    return get_repository_registry().knowledge_link_repo


def _agent_session_repo():
    return get_repository_registry().agent_session_repo


def _task_repo():
    return get_repository_registry().task_repo


def _artifact_upload_request() -> ArtifactUploadRequest:
    return ArtifactUploadRequest(collection_name=str(request.form.get("collection_name") or "").strip() or None)


def _artifact_rag_index_request() -> ArtifactRagIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return ArtifactRagIndexRequest.model_validate(payload)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _enforce_mutation_gate(operation: str, *, artifact_id: str | None = None):
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    tool_call = {
        "name": operation,
        "args": {"artifact_id": artifact_id} if artifact_id else {},
    }
    approval = get_approval_policy_service().evaluate(
        command=None,
        tool_calls=[tool_call],
        task={"id": f"artifact:{artifact_id}" if artifact_id else "artifact:route"},
        agent_cfg=cfg,
    )
    risk = evaluate_execution_risk(
        command=None,
        tool_calls=[tool_call],
        task={"id": f"artifact:{artifact_id}" if artifact_id else "artifact:route"},
        agent_cfg=cfg,
    )
    decision = get_mutation_gate_service().evaluate(
        command=None,
        tool_calls=[tool_call],
        task={"id": f"artifact:{artifact_id}" if artifact_id else "artifact:route"},
        agent_cfg=cfg,
        approval_decision=approval,
        risk_decision=risk,
        trace_id=None,
        actor=_current_username(),
    ).as_dict()
    get_execution_audit_service().emit(
        operation_type="mutation_gate_decision",
        outcome=str(decision.get("classification") or "unknown"),
        trace_id=None,
        goal_id=None,
        task_id=None,
        actor_role="hub",
        details={
            "reason_code": decision.get("reason_code"),
            "mutation_class": decision.get("mutation_class"),
            "normalized_target": decision.get("normalized_target"),
            "approval_scope": decision.get("approval_scope"),
            "source": "artifacts_route",
            "operation": operation,
            "artifact_id": artifact_id,
        },
    )
    if decision.get("classification") in {"blocked", "confirm_required"}:
        return api_response(
            status="error",
            message="mutation_gate_blocked",
            data={"reason_code": decision.get("reason_code"), "decision": decision},
            code=403,
        )
    return None


def _serialize_artifact_detail(artifact_id: str) -> dict | None:
    artifact = _public_artifact(artifact_id)
    if artifact is None:
        return None
    versions = _artifact_version_repo().get_by_artifact(artifact_id)
    documents = _extracted_document_repo().get_by_artifact(artifact_id)
    links = _knowledge_link_repo().get_by_artifact(artifact_id)
    knowledge_index = _knowledge_index_repo().get_by_artifact(artifact_id)
    index_runs = _knowledge_index_run_repo().get_by_knowledge_index(knowledge_index.id) if knowledge_index else []
    return {
        "artifact": artifact.model_dump(),
        "versions": [item.model_dump() for item in versions],
        "extracted_documents": [item.model_dump() for item in documents],
        "knowledge_links": [item.model_dump() for item in links],
        "knowledge_index": knowledge_index.model_dump() if knowledge_index else None,
        "knowledge_index_runs": [item.model_dump() for item in index_runs],
    }


def _enforce_remote_artifact_access(operation: str):
    caller_instance_id = str(request.headers.get("X-Ananta-Instance-ID") or "").strip()
    if not caller_instance_id:
        return None
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    policy = get_remote_federation_policy_service().normalize_backend(
        {
            "allowed_operations": ["models", "chat", operation],
        },
        cfg=cfg,
    )
    decision = get_remote_federation_policy_service().evaluate(
        backend_policy=policy,
        operation=operation,
        hop_count=request.headers.get("X-Ananta-Hop-Count", type=int),
        provenance={"caller_instance_id": caller_instance_id},
    )
    if not decision.allowed:
        return api_response(
            status="error",
            message="forbidden",
            data={"details": decision.reason, "caller_instance_id": caller_instance_id},
            code=403,
        )
    return None


def _model_status(item) -> str:
    direct = getattr(item, "status", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    if hasattr(item, "model_dump"):
        payload = item.model_dump()
        if isinstance(payload, dict):
            value = payload.get("status")
            if isinstance(value, str):
                return value
    return ""


@artifacts_bp.route("/artifacts/upload", methods=["POST"])
@check_auth
def upload_artifact():
    blocked = _enforce_mutation_gate("artifact_upload")
    if blocked:
        return blocked
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise BadRequestError("file_required")

    content = uploaded.read()
    if not content:
        raise BadRequestError("file_empty")

    upload_request = _artifact_upload_request()
    artifact, version, collection = get_ingestion_service().upload_artifact(
        filename=uploaded.filename,
        content=content,
        created_by=_current_username(),
        media_type=uploaded.mimetype,
        collection_name=upload_request.collection_name,
    )
    metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
    project_id = str(request.form.get("project_id") or "").strip()
    task_id = str(request.form.get("task_id") or "").strip()
    session_id = str(request.form.get("session_id") or "").strip()
    artifact_type = str(request.form.get("type") or "").strip()
    tool_call_id = str(request.form.get("tool_call_id") or "").strip()
    if session_id and (not task_id or not project_id):
        linked_session = _agent_session_repo().get_by_id(session_id)
        if linked_session is not None:
            if not task_id:
                task_id = str(getattr(linked_session, "task_id", "") or "").strip()
            if not project_id:
                project_id = str(getattr(linked_session, "team_id", "") or "").strip()
    if task_id and not project_id:
        linked_task = _task_repo().get_by_id(task_id)
        if linked_task is not None:
            project_id = str(getattr(linked_task, "team_id", "") or "").strip()
    if project_id:
        metadata["project_id"] = project_id
    if task_id:
        metadata["task_id"] = task_id
    if session_id:
        metadata["session_id"] = session_id
    if artifact_type:
        metadata["type"] = artifact_type
    if tool_call_id:
        metadata["tool_call_id"] = tool_call_id
    if metadata:
        artifact.artifact_metadata = metadata
        artifact.updated_at = artifact.updated_at or artifact.created_at
        artifact = _artifact_repo().save(artifact)
    return api_response(
        data={
            "artifact": artifact.model_dump(),
            "version": version.model_dump(),
            "collection": collection.model_dump() if collection else None,
        },
        code=201,
    )


@artifacts_bp.route("/artifacts", methods=["GET"])
@check_auth
def list_artifacts():
    blocked = _enforce_remote_artifact_access("artifact")
    if blocked:
        return blocked
    project_id = str(request.args.get("project_id") or "").strip()
    task_id = str(request.args.get("task_id") or "").strip()
    session_id = str(request.args.get("session_id") or "").strip()
    artifact_type = str(request.args.get("type") or "").strip().lower()
    limit = max(1, min(int(request.args.get("limit", type=int) or 200), 1000))
    offset = max(0, int(request.args.get("offset", type=int) or 0))

    rows = _artifact_repo().get_all()
    filtered = []
    for item in rows:
        if not is_artifact_visible_on_generic_surfaces(item):
            continue
        metadata = dict(getattr(item, "artifact_metadata", None) or {})
        if project_id and str(metadata.get("project_id") or "") != project_id:
            continue
        if task_id and str(metadata.get("task_id") or "") != task_id:
            continue
        if session_id and str(metadata.get("session_id") or "") != session_id:
            continue
        if artifact_type:
            media_type = str(getattr(item, "latest_media_type", "") or "").lower()
            meta_type = str(metadata.get("type") or "").lower()
            if artifact_type not in media_type and artifact_type != meta_type:
                continue
        filtered.append(item)

    paged = filtered[offset: offset + limit]
    return api_response(
        data=[item.model_dump() for item in paged],
        message=None,
    )


@artifacts_bp.route("/artifacts/<artifact_id>", methods=["GET"])
@check_auth
def get_artifact(artifact_id: str):
    blocked = _enforce_remote_artifact_access("artifact")
    if blocked:
        return blocked
    payload = _serialize_artifact_detail(artifact_id)
    if payload is None:
        raise NotFoundError()
    return api_response(data=payload)


@artifacts_bp.route("/artifacts/<artifact_id>/extract", methods=["POST"])
@check_auth
def extract_artifact(artifact_id: str):
    _require_public_artifact(artifact_id)
    blocked = _enforce_mutation_gate("artifact_extract", artifact_id=artifact_id)
    if blocked:
        return blocked
    artifact, version, document = get_ingestion_service().extract_artifact(artifact_id)
    if artifact is None:
        raise NotFoundError()
    if version is None or document is None:
        raise NotFoundError("artifact_version_not_found")
    return api_response(
        data={
            "artifact": artifact.model_dump(),
            "version": version.model_dump(),
            "document": document.model_dump(),
        }
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-index", methods=["POST"])
@check_auth
def index_artifact_for_rag(artifact_id: str):
    _require_public_artifact(artifact_id)
    blocked = _enforce_mutation_gate("artifact_rag_index", artifact_id=artifact_id)
    if blocked:
        return blocked
    payload = _artifact_rag_index_request()
    if payload.async_mode:
        job = get_knowledge_index_job_service().submit_artifact_job(
            artifact_id=artifact_id,
            created_by=_current_username(),
            profile_name=payload.profile_name,
            profile_overrides=payload.profile_overrides,
            graph_visual_metrics=(
                payload.graph_visual_metrics.model_dump(by_alias=True)
                if payload.graph_visual_metrics is not None
                else None
            ),
        )
        return api_response(status="accepted", code=202, data={"job": job})
    knowledge_index, run = get_rag_helper_index_service().index_artifact(
        artifact_id,
        created_by=_current_username(),
        profile_name=payload.profile_name,
        profile_overrides=payload.profile_overrides,
    )
    run_status = _model_status(run)
    status = "success" if run_status == "completed" else "error"
    code = 200 if run_status == "completed" else 500
    return api_response(
        status=status,
        code=code,
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "run": run.model_dump(),
        },
        message=None if run_status == "completed" else "rag_index_failed",
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-status", methods=["GET"])
@check_auth
def get_artifact_rag_status(artifact_id: str):
    _require_public_artifact(artifact_id)
    knowledge_index, runs = get_rag_helper_index_service().get_artifact_status(artifact_id)
    if knowledge_index is None:
        raise NotFoundError("rag_index_not_found")
    return api_response(
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "runs": [item.model_dump() for item in runs],
        }
    )


@artifacts_bp.route("/artifacts/<artifact_id>/content", methods=["GET"])
@check_auth
def get_artifact_content(artifact_id: str):
    """Serve raw artifact bytes or normalized JSON payload (compatible adapter)."""
    _require_public_artifact(artifact_id)
    version_repo = _artifact_version_repo()
    versions = version_repo.get_by_artifact(artifact_id)
    if not versions:
        raise NotFoundError()
    latest = versions[0]
    storage_path = Path(latest.storage_path)
    if not storage_path.exists():
        raise NotFoundError("artifact_file_not_found")

    normalized = str(request.args.get("normalized") or "").strip().lower() in {"1", "true", "yes"}
    if normalized:
        offset = max(0, int(request.args.get("offset", default=0, type=int) or 0))
        limit = int(request.args.get("limit", default=262144, type=int) or 262144)
        limit = max(1024, min(limit, 1024 * 1024))
        content = storage_path.read_bytes()
        chunk = content[offset: offset + limit]
        next_offset = offset + len(chunk)
        return api_response(
            data={
                "artifact_id": artifact_id,
                "type": str(latest.media_type or "application/octet-stream"),
                "encoding": "base64",
                "payload": base64.b64encode(chunk).decode("ascii"),
                "size_bytes": int(latest.size_bytes or len(content)),
                "offset": offset,
                "limit": limit,
                "next_offset": next_offset if next_offset < len(content) else None,
                "has_more": next_offset < len(content),
                "filename": str(latest.original_filename or "artifact.bin"),
            }
        )

    return send_file(
        str(storage_path),
        mimetype=latest.media_type or "application/octet-stream",
        as_attachment=True,
        download_name=latest.original_filename or "artifact.bin",
    )


@artifacts_bp.route(
    "/internal/knowledge-index/payload-artifacts/<artifact_id>",
    methods=["GET"],
)
@check_registered_worker_auth(scope=KNOWLEDGE_INDEX_PAYLOAD_SCOPE)
@_check_knowledge_index_payload_access
def get_knowledge_index_payload_artifact(artifact_id: str):
    """Serve only bounded system payloads to an identity-bound index worker."""

    artifact = _artifact_repo().get_by_id(artifact_id)
    metadata = dict(getattr(artifact, "artifact_metadata", None) or {}) if artifact else {}
    if artifact is None or metadata.get("system_artifact_kind") != "knowledge_index_job_payload":
        raise NotFoundError()
    latest_version_id = str(getattr(artifact, "latest_version_id", None) or "").strip()
    latest = _artifact_version_repo().get_by_id(latest_version_id) if latest_version_id else None
    if latest is None or str(latest.media_type or "").lower() != _KNOWLEDGE_INDEX_PAYLOAD_MEDIA_TYPE:
        raise NotFoundError("knowledge_index_payload_not_found")
    size_bytes = int(latest.size_bytes or 0)
    if size_bytes < 0 or size_bytes > _KNOWLEDGE_INDEX_PAYLOAD_MAX_BYTES:
        raise NotFoundError("knowledge_index_payload_size_invalid")
    storage_path = Path(str(latest.storage_path or ""))
    content = _read_pinned_verified_artifact_bytes(
        storage_path,
        expected_size=size_bytes,
        expected_sha256=str(latest.sha256 or ""),
        maximum_size=_KNOWLEDGE_INDEX_PAYLOAD_MAX_BYTES,
        not_found_reason="knowledge_index_payload_not_found",
        integrity_reason="knowledge_index_payload_integrity_invalid",
    )
    digest = hashlib.sha256(content).hexdigest()
    response = send_file(
        BytesIO(content),
        mimetype=_KNOWLEDGE_INDEX_PAYLOAD_MEDIA_TYPE,
        as_attachment=True,
        download_name=latest.original_filename or "knowledge-index-payload.json",
    )
    response.headers["X-Artifact-SHA256"] = digest
    response.headers["X-Artifact-Size"] = str(size_bytes)
    return response


@artifacts_bp.route(
    "/internal/knowledge-index/output-artifacts/<artifact_id>",
    methods=["GET"],
)
@_check_knowledge_index_worker_output_access
def get_knowledge_index_worker_output_artifact(artifact_id: str):
    """Serve one job-bound Worker output to the delegating Hub."""

    artifact = _artifact_repo().get_by_id(artifact_id)
    metadata = dict(
        getattr(artifact, "artifact_metadata", None) or {}
    ) if artifact else {}
    if (
        artifact is None
        or metadata.get("system_artifact_kind")
        != "knowledge_index_worker_output"
    ):
        raise NotFoundError()
    latest_version_id = str(
        getattr(artifact, "latest_version_id", None) or ""
    ).strip()
    latest = (
        _artifact_version_repo().get_by_id(latest_version_id)
        if latest_version_id
        else None
    )
    if latest is None:
        raise NotFoundError("knowledge_index_output_not_found")
    size_bytes = int(latest.size_bytes or 0)
    if size_bytes < 0 or size_bytes > _KNOWLEDGE_INDEX_OUTPUT_MAX_BYTES:
        raise NotFoundError("knowledge_index_output_integrity_invalid")
    storage_path = Path(str(latest.storage_path or ""))
    content = _read_pinned_verified_artifact_bytes(
        storage_path,
        expected_size=size_bytes,
        expected_sha256=str(latest.sha256 or ""),
        maximum_size=_KNOWLEDGE_INDEX_OUTPUT_MAX_BYTES,
        not_found_reason="knowledge_index_output_not_found",
        integrity_reason="knowledge_index_output_integrity_invalid",
    )
    digest = hashlib.sha256(content).hexdigest()
    response = send_file(
        BytesIO(content),
        mimetype=latest.media_type or "application/octet-stream",
        as_attachment=True,
        download_name=latest.original_filename or "knowledge-index-output.bin",
    )
    response.headers["X-Artifact-SHA256"] = digest
    response.headers["X-Artifact-Size"] = str(size_bytes)
    return response


@artifacts_bp.route("/artifacts/<artifact_id>/rag-preview", methods=["GET"])
@check_auth
def get_artifact_rag_preview(artifact_id: str):
    _require_public_artifact(artifact_id)
    limit = request.args.get("limit", default=5, type=int) or 5
    preview = get_rag_helper_index_service().get_artifact_preview(artifact_id, limit=max(1, min(limit, 25)))
    if preview is None:
        raise NotFoundError("rag_index_not_found")
    return api_response(data=preview)


@artifacts_bp.route("/artifacts/<artifact_id>/rag-jobs/<job_id>", methods=["GET"])
@check_auth
def get_artifact_rag_job(artifact_id: str, job_id: str):
    _require_public_artifact(artifact_id)
    job = get_knowledge_index_job_service().get_job(job_id)
    if job is None or str(job.get("scope_id")) != artifact_id:
        raise NotFoundError("rag_job_not_found")
    return api_response(data={"job": job})


@artifacts_bp.route("/artifacts/retrieval-preflight", methods=["GET"])
@check_auth
def get_retrieval_preflight():
    return api_response(data=get_retrieval_service().get_source_preflight())


@artifacts_bp.route("/artifacts/orchestration-contract", methods=["GET"])
@check_auth
def get_artifact_orchestration_contract():
    return api_response(data=build_retrieval_orchestration_contract(entrypoint_group="artifacts"))

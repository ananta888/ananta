"""Thin additive v1 HTTP surface for semantic-compute contracts."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_service_auth, check_user_auth
from agent.models.semantic_principal import SemanticPrincipal
from agent.services.repository_registry import get_repository_registry
from agent.services.semantic_compute_execution_service import (
    SemanticComputeExecutionError,
    get_semantic_compute_execution_service,
)
from agent.services.semantic_compute_explanation_service import (
    SemanticComputeExplanationError,
    SemanticComputeExplanationService,
)
from agent.services.semantic_compute_task_service import (
    SemanticComputeTaskError,
    get_semantic_compute_task_service,
)
from agent.services.semantic_contract_service import (
    SemanticContractServiceError,
    get_semantic_contract_service,
)
from agent.services.semantic_media_permission_service import (
    SemanticMediaPermissionError,
)
from agent.services.semantic_server_compute_service import (
    SemanticServerComputeError,
    get_semantic_server_compute_service,
)
from agent.services.workflow_worker_service_auth import (
    SEMANTIC_COMPUTE_WORKER_SCOPE,
)
from agent.routes.semantic_media_contract_route_parsing import (
    _advertisements,
    _body,
    _boolean,
    _bounded_int,
    _bounded_string,
    _error,
    _idempotency_key,
    _identifier,
    _mapping,
    _optional_identifier,
    _principal,
    _query_scope,
    _resource_budget,
    _revision_precondition,
    _worker_body,
    _worker_url,
)
from agent.routes.semantic_media_contract_route_authority import (
    SemanticContractRouteAuthority,
    _capability_record,
    _hub_fallback_healthy,
    _hub_security_confirmed,
    _require_hub_compute_enabled,
    _require_semantic_capability,
    _semantic_permission_service,
    _training_capability_authorised,
)


semantic_media_contracts_bp = Blueprint("semantic_media_contracts", __name__)

# Composition seam: the module-level authority resolves share membership and
# capability issuance with the production services; tests replace it with an
# instance built from explicit test doubles.
_route_authority = SemanticContractRouteAuthority()


def _establish_membership(principal: SemanticPrincipal, body: dict[str, Any]) -> None:
    _route_authority.establish_membership(principal, body)


_CREATE_FIELDS = {
    "session_id",
    "room_id",
    "epoch",
    "policy_version",
    "consent_version",
    "proposal",
    "advertisements",
}
_MUTATION_FIELDS = {
    "session_id",
    "epoch",
    "expected_revision",
    "consent_version",
    "proposal",
    "advertisements",
}
_CANDIDATE_KEY_FIELDS = {"session_id", "epoch", "key_id", "public_key_b64", "expires_at_ms"}
_CAPABILITY_FIELDS = {
    "schema",
    "advertisement_id",
    "session_id",
    "room_id",
    "epoch",
    "sender_id",
    "algorithms",
    "roles",
    "task_types",
    "resource_profile",
    "measurements_expires_at_ms",
    "expires_at_ms",
    "max_delay_ms",
    "max_artifact_bytes",
    "signature",
}
_SCHEDULE_FIELDS = {
    "session_id",
    "epoch",
    "expected_revision",
    "task_type",
    "audience",
    "sequence_start",
    "sequence_end",
    "resource_budget",
    "deadline_epoch_ms",
    "validator_count",
    "hot_standby",
}
_LEASE_MUTATION_FIELDS = {
    "session_id",
    "epoch",
    "expected_version",
    "fencing_token",
    "resource_budget",
    "expires_at_ms",
}
_SERVER_TASK_FIELDS = {
    "session_id",
    "epoch",
    "expected_revision",
    "parent_task_id",
    "task_type",
    "audience",
    "input_refs",
    "sequence_start",
    "sequence_end",
    "resource_budget",
    "deadline_epoch_ms",
}
_CAPABILITY_GRANT_FIELDS = {
    "session_id",
    "room_id",
    "epoch",
    "subject_id",
    "subject_role",
    "capability",
    "scope_kind",
    "scope_id",
    "direction",
    "data_type",
    "purpose",
    "expires_at_ms",
}


@semantic_media_contracts_bp.post("/v1/semantic-media/capability-grants")
@check_user_auth
def issue_semantic_media_capability_grant():
    """Attenuate current Hub-owned Share rights into a signed grant."""

    required = _CAPABILITY_GRANT_FIELDS - {"room_id"}
    try:
        body = _body(_CAPABILITY_GRANT_FIELDS, required=required)
        principal = _principal()
        session_id = _identifier(body["session_id"], "session_id")
        epoch = _bounded_int(body["epoch"], "epoch", 1, 2_147_483_647)
        share, target_permissions = _route_authority.capability_issuance_authority(
            principal,
            session_id=session_id,
            epoch=epoch,
            subject_id=_identifier(body["subject_id"], "subject_id"),
        )
        scope_kind = _bounded_string(body["scope_kind"], "scope_kind", 4, 16)
        scope_id = _identifier(body["scope_id"], "scope_id")
        room_id = _optional_identifier(body.get("room_id"), "room_id")
        if scope_kind == "session":
            if scope_id != session_id or room_id is not None:
                raise SemanticMediaPermissionError("scope_invalid", status_code=400)
        elif scope_kind == "room":
            if room_id is None or scope_id != room_id:
                raise SemanticMediaPermissionError("scope_invalid", status_code=400)
        else:
            raise SemanticMediaPermissionError("scope_invalid", status_code=400)
        authorised = _route_authority.attenuated_capabilities(
            session_id,
            target_permissions,
            allow_training=_training_capability_authorised(principal, body),
        )
        grant = _semantic_permission_service(required=True).issue(
            authorised_capabilities=authorised,
            owner_id=str(share["owner_user_id"]),
            tenant_id=principal.tenant_id,
            subject_id=str(body["subject_id"]),
            subject_role=_bounded_string(body["subject_role"], "subject_role", 3, 32),
            capability=_bounded_string(body["capability"], "capability", 3, 32),
            scope_kind=scope_kind,
            scope_id=scope_id,
            direction=_bounded_string(body["direction"], "direction", 3, 16),
            data_type=_bounded_string(body["data_type"], "data_type", 1, 128),
            purpose=_bounded_string(body["purpose"], "purpose", 1, 128),
            epoch=epoch,
            expires_at=_bounded_int(
                body["expires_at_ms"], "expires_at_ms", 1, 9_007_199_254_740_991
            )
            / 1000.0,
            idempotency_key=_idempotency_key(),
        )
        payload = _capability_record(grant, revoked_at=None, revoked_by=None, revocation_version=0)
        return jsonify({"ok": True, "grant": payload, "data": payload}), 201
    except (SemanticContractServiceError, SemanticMediaPermissionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.get("/v1/semantic-media/capability-grants")
@check_user_auth
def list_semantic_media_capability_grants():
    try:
        principal = _principal()
        session_id = _identifier(request.args.get("session_id"), "session_id")
        epoch = _bounded_int(request.args.get("epoch"), "epoch", 1, 2_147_483_647)
        scope_kind = _bounded_string(request.args.get("scope_kind", "session"), "scope_kind", 4, 16)
        scope_id = _identifier(request.args.get("scope_id", session_id), "scope_id")
        share, membership_permissions = _route_authority.share_membership_authority(
            principal,
            session_id=session_id,
            epoch=epoch,
        )
        del membership_permissions
        is_owner = str(share.get("owner_user_id") or "") == principal.subject
        requested_subject = request.args.get("subject_id")
        subject_id = (
            _identifier(requested_subject, "subject_id")
            if requested_subject is not None
            else (None if is_owner else principal.subject)
        )
        if not is_owner and subject_id != principal.subject:
            raise SemanticMediaPermissionError("capability_list_denied")
        records = _semantic_permission_service(required=True).list_scope(
            tenant_id=principal.tenant_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            epoch=epoch,
            owner_id=principal.subject if is_owner else None,
            subject_id=subject_id,
            limit=_bounded_int(request.args.get("limit", "100"), "limit", 1, 200),
        )
        payload = [
            _capability_record(
                record.grant,
                revoked_at=record.revoked_at,
                revoked_by=record.revoked_by,
                revocation_version=record.revocation_version,
            )
            for record in records
        ]
        return jsonify({"ok": True, "grants": payload, "data": payload}), 200
    except (SemanticContractServiceError, SemanticMediaPermissionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/capability-grants/<grant_id>/revoke")
@check_user_auth
def revoke_semantic_media_capability_grant(grant_id: str):
    try:
        principal = _principal()
        record = _semantic_permission_service(required=True).revoke(
            _identifier(grant_id, "grant_id"),
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
        )
        payload = _capability_record(
            record.grant,
            revoked_at=record.revoked_at,
            revoked_by=record.revoked_by,
            revocation_version=record.revocation_version,
        )
        return jsonify({"ok": True, "grant": payload, "data": payload}), 200
    except (SemanticContractServiceError, SemanticMediaPermissionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/contracts")
@semantic_media_contracts_bp.post("/v1/semantic-media/contracts/offers")
@check_user_auth
def create_semantic_contract_offer():
    try:
        body = _body(_CREATE_FIELDS, required={"session_id", "epoch", "policy_version", "consent_version", "proposal"})
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "publish", direction="egress")
        result = get_semantic_contract_service().create_offer(
            principal,
            session_id=_identifier(body["session_id"], "session_id"),
            room_id=_optional_identifier(body.get("room_id"), "room_id"),
            epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
            policy_version=_identifier(body["policy_version"], "policy_version"),
            consent_version=_bounded_int(body["consent_version"], "consent_version", 1, 2_147_483_647),
            security_confirmed=_hub_security_confirmed(),
            fallback_healthy=_hub_fallback_healthy(),
            proposal=_mapping(body["proposal"], "proposal"),
            advertisements=_advertisements(body.get("advertisements", [])),
            idempotency_key=_idempotency_key(),
        )
        return jsonify({"ok": True, "contract": result, "data": result}), 201
    except SemanticContractServiceError as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/contracts/<contract_id>/<action>")
@check_user_auth
def mutate_semantic_contract(contract_id: str, action: str):
    if action not in {"counter", "accept", "activate", "revoke", "fallback"}:
        return _error(SemanticContractServiceError("action_not_found", status_code=404))
    try:
        body = _body(
            _MUTATION_FIELDS,
            required={"session_id", "epoch", "consent_version"},
        )
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "publish", direction="egress")
        result = get_semantic_contract_service().mutate(
            principal,
            contract_id=_identifier(contract_id, "contract_id"),
            session_id=_identifier(body["session_id"], "session_id"),
            epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
            action=action,
            expected_revision=_revision_precondition(body),
            idempotency_key=_idempotency_key(),
            proposal=_mapping(body.get("proposal", {}), "proposal"),
            consent_version=_bounded_int(body["consent_version"], "consent_version", 0, 2_147_483_647),
            security_confirmed=_hub_security_confirmed(),
            fallback_healthy=_hub_fallback_healthy(),
            advertisements=_advertisements(body.get("advertisements", [])),
        )
        return jsonify({"ok": True, "contract": result, "data": result}), 200
    except SemanticContractServiceError as exc:
        return _error(exc)


@semantic_media_contracts_bp.get("/v1/semantic-media/contracts/<contract_id>")
@check_user_auth
def semantic_contract_detail(contract_id: str):
    try:
        body = _query_scope()
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "subscribe", direction="ingress")
        result = get_semantic_contract_service().detail(
            principal,
            contract_id=_identifier(contract_id, "contract_id"),
            session_id=str(body["session_id"]),
            epoch=int(body["epoch"]),
        )
        return jsonify({"ok": True, "contract": result, "data": result}), 200
    except SemanticContractServiceError as exc:
        return _error(exc)


@semantic_media_contracts_bp.get("/v1/semantic-media/contracts")
@check_user_auth
def list_semantic_contracts():
    try:
        body = _query_scope()
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "subscribe", direction="ingress")
        offset = _bounded_int(request.args.get("offset", "0"), "offset", 0, 10_000_000)
        limit = _bounded_int(request.args.get("limit", "50"), "limit", 1, 100)
        result = get_semantic_contract_service().list(
            principal,
            session_id=str(body["session_id"]),
            epoch=int(body["epoch"]),
            offset=offset,
            limit=limit,
        )
        return jsonify({"ok": True, "contracts": result, "data": result}), 200
    except SemanticContractServiceError as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/compute/candidate-keys")
@check_user_auth
def register_semantic_compute_candidate_key():
    try:
        body = _body(_CANDIDATE_KEY_FIELDS, required=_CANDIDATE_KEY_FIELDS)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "compute", direction="egress")
        result = get_semantic_compute_execution_service().register_candidate_key(
            principal,
            session_id=_identifier(body["session_id"], "session_id"),
            epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
            key_id=_identifier(body["key_id"], "key_id"),
            public_key_b64=_bounded_string(body["public_key_b64"], "public_key_b64", 40, 128),
            expires_at_ms=_bounded_int(body["expires_at_ms"], "expires_at_ms", 1, 9_007_199_254_740_991),
        )
        return jsonify({"ok": True, "candidate_key": result, "data": result}), 201
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/compute/capabilities")
@check_user_auth
def advertise_semantic_compute_candidate():
    required = _CAPABILITY_FIELDS - {"room_id"}
    try:
        body = _body(_CAPABILITY_FIELDS, required=required)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "compute", direction="egress")
        result = get_semantic_compute_execution_service().advertise_candidate(principal, advertisement=body)
        return jsonify({"ok": True, "capability": result, "data": result}), 201
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.get("/v1/semantic-media/compute/capabilities")
@check_user_auth
def list_semantic_compute_candidate_claims():
    try:
        body = _query_scope()
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "subscribe", direction="ingress")
        result = get_semantic_compute_execution_service().list_candidate_claims(
            principal,
            session_id=str(body["session_id"]),
            epoch=int(body["epoch"]),
            room_id=_optional_identifier(request.args.get("room_id"), "room_id"),
        )
        return jsonify({"ok": True, "capabilities": result, "data": result}), 200
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/contracts/<contract_id>/schedule")
@check_user_auth
def schedule_semantic_compute(contract_id: str):
    try:
        body = _body(_SCHEDULE_FIELDS, required=_SCHEDULE_FIELDS)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "compute", direction="egress")
        expected_revision = _revision_precondition(body)
        result = get_semantic_compute_execution_service().schedule(
            principal,
            contract_id=_identifier(contract_id, "contract_id"),
            session_id=_identifier(body["session_id"], "session_id"),
            epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
            expected_revision=expected_revision,
            task_type=_identifier(body["task_type"], "task_type"),
            audience=_identifier(body["audience"], "audience"),
            sequence_start=_bounded_int(body["sequence_start"], "sequence_start", 0, 2_147_483_647),
            sequence_end=_bounded_int(body["sequence_end"], "sequence_end", 0, 2_147_483_647),
            resource_budget=_resource_budget(body["resource_budget"]),
            deadline_epoch_ms=_bounded_int(body["deadline_epoch_ms"], "deadline_epoch_ms", 1, 9_007_199_254_740_991),
            validator_count=_bounded_int(body["validator_count"], "validator_count", 0, 2),
            hot_standby=_boolean(body["hot_standby"], "hot_standby"),
            idempotency_key=_idempotency_key(),
        )
        return jsonify({"ok": True, "schedule": result, "data": result}), 201
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.get("/v1/semantic-media/contracts/<contract_id>/leases")
@check_user_auth
def list_semantic_compute_leases(contract_id: str):
    try:
        body = _query_scope()
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "subscribe", direction="ingress")
        result = get_semantic_compute_execution_service().list_leases(
            principal,
            session_id=str(body["session_id"]),
            epoch=int(body["epoch"]),
            contract_id=_identifier(contract_id, "contract_id"),
            limit=_bounded_int(request.args.get("limit", "100"), "limit", 1, 200),
        )
        return jsonify({"ok": True, "leases": result, "data": result}), 200
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/leases/<lease_id>/<action>")
@check_user_auth
def mutate_semantic_compute_lease(lease_id: str, action: str):
    if action not in {"revoke", "reduce"}:
        return _error(SemanticComputeExecutionError("action_not_found", status_code=404))
    required = {"session_id", "epoch", "expected_version", "fencing_token"}
    if action == "reduce":
        required.add("resource_budget")
    try:
        body = _body(_LEASE_MUTATION_FIELDS, required=required)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "compute", direction="egress")
        common = {
            "lease_id": _identifier(lease_id, "lease_id"),
            "session_id": _identifier(body["session_id"], "session_id"),
            "epoch": _bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
            "expected_version": _bounded_int(body["expected_version"], "expected_version", 1, 2_147_483_647),
            "fencing_token": _bounded_int(body["fencing_token"], "fencing_token", 1, 9_007_199_254_740_991),
            "idempotency_key": _idempotency_key(),
        }
        service = get_semantic_compute_execution_service()
        if action == "revoke":
            result = service.revoke_lease(principal, **common)
        else:
            result = service.reduce_lease(
                principal,
                **common,
                resource_budget=_resource_budget(body["resource_budget"]),
                expires_at_ms=(
                    _bounded_int(body["expires_at_ms"], "expires_at_ms", 1, 9_007_199_254_740_991)
                    if body.get("expires_at_ms") is not None
                    else None
                ),
            )
        return jsonify({"ok": True, "lease": result, "data": result}), 200
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/contracts/<contract_id>/server-tasks")
@check_user_auth
def delegate_semantic_server_compute(contract_id: str):
    try:
        body = _body(_SERVER_TASK_FIELDS, required=_SERVER_TASK_FIELDS)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "compute", direction="egress")
        _require_hub_compute_enabled()
        raw_refs = body["input_refs"]
        if not isinstance(raw_refs, list) or len(raw_refs) > 16:
            raise SemanticContractServiceError("input_refs_invalid", status_code=400)
        input_refs = [_bounded_string(value, "input_ref", 10, 256) for value in raw_refs]
        result = (
            get_semantic_server_compute_service()
            .delegate(
                principal,
                parent_task_id=_identifier(body["parent_task_id"], "parent_task_id"),
                contract_id=_identifier(contract_id, "contract_id"),
                session_id=_identifier(body["session_id"], "session_id"),
                epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
                expected_revision=_revision_precondition(body),
                task_type=_identifier(body["task_type"], "task_type"),
                audience=_identifier(body["audience"], "audience"),
                input_refs=input_refs,
                sequence_start=_bounded_int(body["sequence_start"], "sequence_start", 0, 2_147_483_647),
                sequence_end=_bounded_int(body["sequence_end"], "sequence_end", 0, 2_147_483_647),
                deadline_epoch_ms=_bounded_int(
                    body["deadline_epoch_ms"], "deadline_epoch_ms", 1, 9_007_199_254_740_991
                ),
                resource_budget=_resource_budget(body["resource_budget"]),
                idempotency_key=_idempotency_key(),
            )
            .to_dict()
        )
        return jsonify({"ok": True, "delegation": result, "data": result}), 201
    except (SemanticContractServiceError, SemanticServerComputeError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/internal/compute/authorize")
@check_service_auth(scope=SEMANTIC_COMPUTE_WORKER_SCOPE)
def authorize_semantic_compute_worker():
    try:
        _require_hub_compute_enabled()
        body = _worker_body({"task"}, required={"task"}, maximum_bytes=196_608)
        worker_url = _worker_url()
        task = get_semantic_compute_task_service().authorize_execution(body["task"], expected_executor_id=worker_url)
        return jsonify(
            {
                "ok": True,
                "data": {
                    "authorized": True,
                    "task_id": task.task_id,
                    "deadline_epoch_ms": task.deadline_epoch_ms,
                    "reason_code": "semantic_compute_authorized",
                },
            }
        ), 200
    except (SemanticContractServiceError, SemanticComputeTaskError) as exc:
        return _error(
            SemanticComputeExecutionError(
                getattr(exc, "reason_code", "semantic_compute_authorization_failed"),
                status_code=409,
            )
        )


@semantic_media_contracts_bp.post("/v1/semantic-media/internal/compute/inputs")
@check_service_auth(scope=SEMANTIC_COMPUTE_WORKER_SCOPE)
def read_semantic_compute_worker_input():
    try:
        _require_hub_compute_enabled()
        body = _worker_body({"task", "input_ref"}, required={"task", "input_ref"}, maximum_bytes=196_608)
        task = get_semantic_compute_task_service().authorize_execution(body["task"], expected_executor_id=_worker_url())
        input_ref = _bounded_string(body["input_ref"], "input_ref", 10, 256)
        if input_ref not in set(task.input_refs) or not input_ref.startswith("artifact:"):
            raise SemanticComputeTaskError("input_ref_not_authorized")
        artifact_id = input_ref.removeprefix("artifact:")
        versions = get_repository_registry().artifact_version_repo.get_by_artifact(artifact_id)
        if not versions:
            raise SemanticComputeTaskError("input_ref_not_found")
        latest = versions[0]
        path = Path(str(latest.storage_path))
        limit = min(int(task.resource_budget["memory_bytes"]), 16 * 1024 * 1024)
        if not path.is_file() or int(latest.size_bytes or 0) > limit:
            raise SemanticComputeTaskError("input_artifact_unavailable")
        content = path.read_bytes()
        if len(content) > limit:
            raise SemanticComputeTaskError("input_artifact_too_large")
        return jsonify(
            {
                "ok": True,
                "data": {
                    "input_ref": input_ref,
                    "media_type": str(latest.media_type or "application/octet-stream"),
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "size_bytes": len(content),
                },
            }
        ), 200
    except (SemanticContractServiceError, SemanticComputeTaskError) as exc:
        return _error(
            SemanticComputeExecutionError(
                getattr(exc, "reason_code", "semantic_compute_input_failed"),
                status_code=409,
            )
        )


@semantic_media_contracts_bp.post("/v1/semantic-media/internal/compute/artifacts")
@check_service_auth(scope=SEMANTIC_COMPUTE_WORKER_SCOPE)
def publish_semantic_compute_worker_artifact():
    try:
        _require_hub_compute_enabled()
        body = _worker_body(
            {"task", "publish_ref", "content_b64"},
            required={"task", "publish_ref", "content_b64"},
            maximum_bytes=5_800_000,
        )
        task = get_semantic_compute_task_service().authorize_execution(body["task"], expected_executor_id=_worker_url())
        publish_ref = _identifier(body["publish_ref"], "publish_ref")
        if publish_ref != task.artifact_publish_ref:
            raise SemanticComputeTaskError("artifact_publish_target_mismatch")
        try:
            content = base64.b64decode(str(body["content_b64"]), validate=True)
        except (TypeError, ValueError) as exc:
            raise SemanticComputeTaskError("artifact_encoding_invalid") from exc
        if not content or len(content) > int(task.resource_budget["artifact_bytes"]):
            raise SemanticComputeTaskError("artifact_budget_exceeded")
        from agent.services.ingestion_service import IngestionService

        artifact, _version, _collection = IngestionService().upload_artifact(
            filename=f"{task.task_id}.semantic.json",
            content=content,
            created_by=f"semantic-compute:{_worker_url()}",
            media_type="application/vnd.ananta.semantic-compute-result+json",
        )
        artifact.status = "quarantined"
        artifact.artifact_metadata = {
            "semantic_compute_task_id": task.task_id,
            "semantic_compute_contract_id": task.contract_id,
            "semantic_compute_lease_id": task.lease_id,
            "semantic_compute_fencing_token": task.fencing_token,
            "publish_ref": publish_ref,
        }
        get_repository_registry().artifact_repo.save(artifact)
        return jsonify(
            {
                "ok": True,
                "data": {"artifact_ref": f"artifact:{artifact.id}", "status": "quarantined"},
            }
        ), 201
    except (SemanticContractServiceError, SemanticComputeTaskError) as exc:
        return _error(
            SemanticComputeExecutionError(
                getattr(exc, "reason_code", "semantic_compute_artifact_failed"),
                status_code=409,
            )
        )


@semantic_media_contracts_bp.post("/v1/semantic-media/internal/compute/results")
@check_service_auth(scope=SEMANTIC_COMPUTE_WORKER_SCOPE)
def admit_semantic_compute_worker_result():
    try:
        _require_hub_compute_enabled()
        body = _worker_body({"result"}, required={"result"}, maximum_bytes=262_144)
        result = get_semantic_compute_task_service().authorize_result(
            body["result"], expected_executor_id=_worker_url()
        )
        repositories = get_repository_registry()
        artifacts = []
        for reference in result.artifact_refs:
            if not reference.startswith("artifact:"):
                raise SemanticComputeTaskError("result_artifact_ref_invalid")
            artifact = repositories.artifact_repo.get_by_id(reference.removeprefix("artifact:"))
            metadata = dict(getattr(artifact, "artifact_metadata", None) or {}) if artifact else {}
            if (
                artifact is None
                or artifact.status != "quarantined"
                or metadata.get("semantic_compute_task_id") != result.task_id
                or metadata.get("semantic_compute_lease_id") != result.lease_id
                or metadata.get("semantic_compute_fencing_token") != result.fencing_token
            ):
                raise SemanticComputeTaskError("result_artifact_binding_mismatch")
            artifacts.append(artifact)
        for artifact in artifacts:
            artifact.status = "stored"
            repositories.artifact_repo.save(artifact)
        return jsonify(
            {
                "ok": True,
                "data": {
                    "accepted": True,
                    "task_id": result.task_id,
                    "artifact_refs": list(result.artifact_refs),
                },
            }
        ), 200
    except (SemanticContractServiceError, SemanticComputeTaskError) as exc:
        return _error(
            SemanticComputeExecutionError(
                getattr(exc, "reason_code", "semantic_compute_result_failed"),
                status_code=409,
            )
        )


@semantic_media_contracts_bp.get("/v1/semantic-media/contracts/<contract_id>/explanation")
@check_user_auth
def semantic_compute_explanation(contract_id: str):
    try:
        body = _query_scope()
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "subscribe", direction="ingress")
        detail = get_semantic_contract_service().detail(
            principal,
            contract_id=_identifier(contract_id, "contract_id"),
            session_id=str(body["session_id"]),
            epoch=int(body["epoch"]),
        )
        expected_revision = _bounded_int(request.args.get("expected_revision"), "expected_revision", 1, 2_147_483_647)
        expected_digest = str(request.args.get("expected_digest") or "")
        reason_by_state = {
            "offered": "offer_accepted",
            "countered": "counter_accepted",
            "accepted": "accept_accepted",
            "active": "activate_accepted",
            "revoked": "revoked_by_user",
            "fallback": "ordinary_fallback",
        }
        result = (
            SemanticComputeExplanationService()
            .explain(
                {
                    "state": detail["status"],
                    "reason_code": reason_by_state[detail["status"]],
                    "revision": detail["revision"],
                    "contract_digest": detail["digest"],
                    "profile": detail["profile"],
                    "delay_ms": detail["delay_ms"],
                },
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            .to_dict()
        )
        return jsonify({"ok": True, "explanation": result, "data": result}), 200
    except SemanticComputeExplanationError as exc:
        return _error(SemanticComputeExecutionError(exc.reason_code, status_code=409))
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


@semantic_media_contracts_bp.post("/v1/semantic-media/contracts/<contract_id>/suggestions")
@check_user_auth
def semantic_compute_suggestion(contract_id: str):
    fields = {"session_id", "epoch", "expected_revision", "expected_digest", "suggestion"}
    try:
        body = _body(fields, required=fields)
        principal = _principal()
        _establish_membership(principal, body)
        _require_semantic_capability(principal, body, "validate", direction="egress")
        detail = get_semantic_contract_service().detail(
            principal,
            contract_id=_identifier(contract_id, "contract_id"),
            session_id=_identifier(body["session_id"], "session_id"),
            epoch=_bounded_int(body["epoch"], "epoch", 1, 2_147_483_647),
        )
        expected_revision = _bounded_int(body["expected_revision"], "expected_revision", 1, 2_147_483_647)
        expected_digest = str(body["expected_digest"] or "")
        if detail["revision"] != expected_revision or detail["digest"] != expected_digest:
            raise SemanticComputeExecutionError("explanation_stale", status_code=412)
        result = SemanticComputeExplanationService().suggestion(body["suggestion"])
        return jsonify({"ok": True, "suggestion": result, "data": result}), 200
    except SemanticComputeExplanationError as exc:
        return _error(SemanticComputeExecutionError(exc.reason_code, status_code=400))
    except (SemanticContractServiceError, SemanticComputeExecutionError) as exc:
        return _error(exc)


__all__ = ["semantic_media_contracts_bp"]

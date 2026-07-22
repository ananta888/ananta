"""RBAC-protected mutations and claim-scoped broadcast flag projection."""

from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.sfu_broadcast_feature_policy import (
    SfuBroadcastFeatureMutationCommand,
    SfuBroadcastFeaturePolicy,
    SfuBroadcastFeaturePolicyError,
    SfuBroadcastFeatureProjection,
)

sfu_broadcast_feature_flags_bp = Blueprint("sfu_broadcast_feature_flags", __name__)

_MUTATION_FIELDS = frozenset(
    {
        "tenant_id",
        "region",
        "room_cohort",
        "enabled",
        "rollout_stage",
        "expected_version",
        "actor",
        "reason",
    }
)
_SCOPE_OVERRIDE_FIELDS = frozenset({"tenant_id", "region", "room_cohort", "room_id"})


@sfu_broadcast_feature_flags_bp.put(
    "/api/admin/sfu-broadcast-feature-flags/<flag>"
)
@admin_required
def mutate_sfu_broadcast_feature_flag(flag: str):
    try:
        body: Any = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise SfuBroadcastFeaturePolicyError("feature_flag_json_object_required")
        unknown = set(body) - _MUTATION_FIELDS
        if unknown:
            raise SfuBroadcastFeaturePolicyError("feature_flag_unknown_field")
        actor = str(body.get("actor") or "").strip()
        authenticated_actor = _authenticated_actor()
        if not actor:
            raise SfuBroadcastFeaturePolicyError("feature_flag_actor_required")
        if actor != authenticated_actor:
            raise SfuBroadcastFeaturePolicyError(
                "feature_flag_actor_mismatch",
                status_code=403,
            )
        idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
        outcome = _policy().mutate(
            SfuBroadcastFeatureMutationCommand(
                tenant_id=body.get("tenant_id"),
                region=body.get("region", "*"),
                room_cohort=body.get("room_cohort", "*"),
                flag=flag,
                enabled=body.get("enabled"),
                rollout_stage=body.get("rollout_stage"),
                expected_version=body.get("expected_version"),
                actor=actor,
                reason=body.get("reason"),
                idempotency_key=idempotency_key,
            )
        )
        return api_response(
            data={"mutation": outcome.payload()},
            code=201 if outcome.status == "created" else 200,
        )
    except SfuBroadcastFeaturePolicyError as exc:
        return _error(exc)


@sfu_broadcast_feature_flags_bp.get(
    "/api/sfu-broadcast-feature-flags/effective"
)
@check_auth
def get_effective_sfu_broadcast_feature_flags():
    if any(field in request.args for field in _SCOPE_OVERRIDE_FIELDS):
        return _error(
            SfuBroadcastFeaturePolicyError(
                "feature_flag_scope_override_forbidden",
                status_code=403,
            )
        )
    identity = _authenticated_identity()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or "").strip()
    if not tenant_id:
        projection = SfuBroadcastFeatureProjection.unavailable(
            "sfu_broadcast.authenticated_scope_missing"
        )
    else:
        try:
            projection = _policy().effective(
                tenant_id=tenant_id,
                region=str(identity.get("region") or "*").strip(),
                room_cohort=str(identity.get("room_cohort") or "*").strip(),
            )
        except SfuBroadcastFeaturePolicyError:
            projection = SfuBroadcastFeatureProjection.unavailable(
                "sfu_broadcast.authenticated_scope_invalid"
            )
    return api_response(data={"projection": projection.payload()})


def _policy() -> SfuBroadcastFeaturePolicy:
    policy = current_app.extensions.get("sfu_broadcast_feature_policy")
    if not isinstance(policy, SfuBroadcastFeaturePolicy):
        raise SfuBroadcastFeaturePolicyError(
            "feature_flag_policy_unavailable",
            status_code=503,
        )
    return policy


def _authenticated_identity() -> Mapping[str, object]:
    user = getattr(g, "user", None)
    if isinstance(user, Mapping) and user:
        return user
    payload = getattr(g, "auth_payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _authenticated_actor() -> str:
    identity = _authenticated_identity()
    return str(
        identity.get("sub")
        or identity.get("username")
        or identity.get("agent_id")
        or identity.get("client_id")
        or "hub-admin"
    ).strip()


def _error(exc: SfuBroadcastFeaturePolicyError):
    return api_response(
        status="error",
        message=exc.reason_code,
        data={"reason_code": exc.reason_code},
        code=exc.status_code,
    )


__all__ = ["sfu_broadcast_feature_flags_bp"]

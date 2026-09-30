"""Pure validation and derivation rules of Source Control grant admin.

Actor/request validation, ETag normalization, deterministic identities
(policy snapshot, grant family, grant ETag), cursors and timestamps. No I/O,
so every rule is unit-testable in isolation (SRP).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Mapping

from agent.db_models.source_control import SourceAccessGrantDB
from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyVersion,
)
from agent.services.source_control_grant_admin_contracts import (
    GrantAdminActor,
    GrantCreateRequest,
    GrantPreset,
    SourceControlGrantAdminError,
)
from ananta_contracts.source_control import GrantTransformation

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REVISION_ID = re.compile(r"^srev_[0-9a-f]{64}$")
DESTINATION_ID = re.compile(r"^dst_[0-9a-f]{64}$")
GRANT_ID = re.compile(r"^grant_[0-9a-f]{64}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")
MUTATOR_ROLES = frozenset({"admin", "project_owner"})
GRANT_STATES = frozenset({"draft", "active", "superseded", "revoked"})


def validate_actor(actor: GrantAdminActor) -> None:
    for value in (actor.subject_id, actor.tenant_id, actor.project_id):
        if not OPAQUE_ID.fullmatch(str(value)) or len(str(value)) > 128:
            raise SourceControlGrantAdminError(
                "grant_actor_invalid", status_code=400
            )


def require_mutator(actor: GrantAdminActor) -> None:
    validate_actor(actor)
    if not (MUTATOR_ROLES & actor.roles):
        raise SourceControlGrantAdminError(
            "grant_admin_required", status_code=403
        )


def validate_create_request(request: GrantCreateRequest) -> None:
    require_pattern(
        request.source_revision_id,
        SOURCE_REVISION_ID,
        "grant_source_revision_invalid",
    )
    require_pattern(
        request.destination_id,
        DESTINATION_ID,
        "grant_destination_invalid",
    )
    if not OPAQUE_ID.fullmatch(request.policy_id):
        raise SourceControlGrantAdminError(
            "grant_policy_id_invalid", status_code=400
        )
    if not OPAQUE_ID.fullmatch(request.preset_id):
        raise SourceControlGrantAdminError(
            "grant_preset_id_invalid", status_code=400
        )
    if (
        isinstance(request.duration_seconds, bool)
        or not isinstance(request.duration_seconds, int)
    ):
        raise SourceControlGrantAdminError(
            "grant_duration_invalid", status_code=400
        )


def require_pattern(
    value: str, pattern: re.Pattern[str], reason_code: str
) -> None:
    if not pattern.fullmatch(str(value)):
        raise SourceControlGrantAdminError(reason_code, status_code=400)


def policy_actor(actor: GrantAdminActor) -> ContextPolicyActor:
    return ContextPolicyActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


def normalize_etag(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    if not SHA256.fullmatch(normalized):
        raise SourceControlGrantAdminError(
            "grant_if_match_invalid", status_code=428
        )
    return normalized


def policy_snapshot_id(policy: ContextPolicyVersion) -> str:
    return "cpv_" + digest(
        {
            "tenant_id": policy.tenant_id,
            "project_id": policy.project_id,
            "policy_id": policy.policy_id,
            "version": policy.version,
            "policy_digest": policy.policy_digest,
        }
    )


def preview_allows_transformation(
    *,
    decision: str,
    transformation: GrantTransformation,
) -> bool:
    compatible = {
        GrantTransformation.RAW: frozenset({"allow"}),
        GrantTransformation.REDACTED: frozenset(
            {"allow", "allow_redacted"}
        ),
        GrantTransformation.SUMMARY: frozenset(
            {"allow", "allow_summary_only"}
        ),
    }
    return str(decision) in compatible[transformation]


def grant_family_id(
    *,
    actor: GrantAdminActor,
    request: GrantCreateRequest,
    preset: GrantPreset,
    policy_version: str,
) -> str:
    return "grfam_" + digest(
        {
            "tenant_id": actor.tenant_id,
            "project_id": actor.project_id,
            "source_revision_id": request.source_revision_id,
            "destination_id": request.destination_id,
            "operation": preset.operation.value,
            "transformation": preset.transformation.value,
            "purpose": preset.purpose,
            "policy_version": policy_version,
        }
    )


def grant_etag(row: SourceAccessGrantDB) -> str:
    return digest(
        {
            "grant_id": row.grant_id,
            "lock_version": row.lock_version,
            "state": row.state,
            "updated_at_epoch": row.updated_at_epoch,
        }
    )


def digest(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def iso_timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def encode_cursor(grant_id: str) -> str:
    return (
        base64.urlsafe_b64encode(grant_id.encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )


def decode_cursor(cursor: str | None) -> str | None:
    if cursor in (None, ""):
        return None
    try:
        raw = str(cursor)
        raw += "=" * (-len(raw) % 4)
        grant_id = base64.urlsafe_b64decode(raw).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlGrantAdminError(
            "grant_cursor_invalid", status_code=400
        ) from exc
    if not GRANT_ID.fullmatch(grant_id):
        raise SourceControlGrantAdminError(
            "grant_cursor_invalid", status_code=400
        )
    return grant_id

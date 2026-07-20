"""Hub-authoritative issuance and evaluation of semantic-media capabilities."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import replace
from typing import Collection

from agent.repositories.semantic_media_capability_grant_repository import (
    PersistedSemanticMediaCapabilityGrant,
    SemanticMediaCapabilityGrantRepository,
    SemanticMediaCapabilityGrantRepositoryError,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from ananta_contracts.semantic_media_permissions import (
    CAPABILITY_CONTRACT_VERSION,
    SEMANTIC_CAPABILITIES,
    SemanticMediaCapabilityGrant,
)


class SemanticMediaPermissionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 403) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
        "utf-8"
    )


class SemanticMediaPermissionService:
    """Issues attenuated grants; it never derives rights from free-form text."""

    def __init__(
        self,
        signing_key: bytes,
        *,
        repository: SemanticMediaCapabilityGrantRepository,
        audit: SemanticMediaAuditPort | None = None,
        clock=time.time,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("signing_key_too_short")
        self._key = bytes(signing_key)
        self._clock = clock
        self._repository = repository
        self._audit = audit

    def issue(
        self,
        *,
        authorised_capabilities: Collection[str],
        owner_id: str,
        tenant_id: str,
        subject_id: str,
        subject_role: str,
        capability: str,
        scope_kind: str,
        scope_id: str,
        direction: str,
        data_type: str,
        purpose: str,
        epoch: int,
        expires_at: float,
        idempotency_key: str | None = None,
    ) -> SemanticMediaCapabilityGrant:
        now = float(self._clock())
        if capability not in SEMANTIC_CAPABILITIES:
            raise SemanticMediaPermissionError("capability_unknown")
        if capability not in set(authorised_capabilities):
            raise SemanticMediaPermissionError("capability_escalation_denied")
        if subject_role not in {"participant", "compute_executor", "lease_holder"}:
            # In particular, ambiguous ``master`` is not a valid role.
            raise SemanticMediaPermissionError("subject_role_invalid")
        if scope_kind not in {"session", "room"} or not _bounded(scope_id, 1, 128):
            raise SemanticMediaPermissionError("scope_invalid")
        if direction not in {"ingress", "egress", "bidirectional", "none"}:
            raise SemanticMediaPermissionError("direction_invalid")
        if not all(_bounded(value, 1, 128) for value in (owner_id, tenant_id, subject_id, data_type, purpose)):
            raise SemanticMediaPermissionError("capability_field_invalid")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or not 1 <= epoch <= 2**31 - 1:
            raise SemanticMediaPermissionError("epoch_invalid")
        if not now < float(expires_at) <= now + 86_400:
            raise SemanticMediaPermissionError("capability_expiry_invalid")
        if idempotency_key is not None and (
            not 8 <= len(idempotency_key) <= 256
            or any(character.isspace() for character in idempotency_key)
        ):
            raise SemanticMediaPermissionError("capability_idempotency_key_invalid", status_code=400)

        grant_id = (
            "semantic-grant-"
            + hmac.new(
                self._key,
                f"issue\0{tenant_id}\0{owner_id}\0{idempotency_key}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:40]
            if idempotency_key is not None
            else str(uuid.uuid4())
        )

        unsigned = SemanticMediaCapabilityGrant(
            version=CAPABILITY_CONTRACT_VERSION,
            grant_id=grant_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            subject_role=subject_role,  # type: ignore[arg-type]
            capability=capability,  # type: ignore[arg-type]
            scope_kind=scope_kind,  # type: ignore[arg-type]
            scope_id=scope_id,
            direction=direction,  # type: ignore[arg-type]
            data_type=data_type,
            purpose=purpose,
            epoch=epoch,
            issued_at=now,
            expires_at=float(expires_at),
        )
        signed = replace(unsigned, signature=self._sign(unsigned))
        try:
            return self._repository.create(
                signed,
                audit_event=self._prepare_audit(signed, transition="granted"),
            ).grant
        except SemanticMediaCapabilityGrantRepositoryError as exc:
            status = 503 if exc.reason_code == "capability_audit_required" else 409
            raise SemanticMediaPermissionError(exc.reason_code, status_code=status) from exc

    def evaluate(
        self,
        grant: SemanticMediaCapabilityGrant,
        *,
        capability: str | None = None,
        tenant_id: str,
        subject_id: str,
        scope_kind: str,
        scope_id: str,
        direction: str,
        data_type: str,
        purpose: str,
        epoch: int,
    ) -> tuple[bool, str]:
        now = float(self._clock())
        if grant.version != CAPABILITY_CONTRACT_VERSION or grant.issuer != "hub":
            return False, "capability_contract_invalid"
        if not hmac.compare_digest(grant.signature, self._sign(replace(grant, signature=""))):
            return False, "capability_signature_invalid"
        persisted = self._repository.get(grant.grant_id)
        if persisted is None:
            return False, "capability_not_persisted"
        if persisted.grant != grant:
            return False, "capability_persistence_mismatch"
        if grant.expires_at <= now:
            return False, "capability_expired"
        if persisted.revoked_at is not None:
            return False, "capability_revoked"
        checks = (
            (capability is None or grant.capability == capability, "capability_mismatch"),
            (grant.tenant_id == tenant_id, "tenant_mismatch"),
            (grant.subject_id == subject_id, "subject_mismatch"),
            (grant.scope_kind == scope_kind and grant.scope_id == scope_id, "scope_mismatch"),
            (grant.direction in {direction, "bidirectional"}, "direction_mismatch"),
            (grant.data_type == data_type, "data_type_mismatch"),
            (grant.purpose == purpose, "purpose_mismatch"),
            (grant.epoch == epoch, "epoch_mismatch"),
        )
        for allowed, reason in checks:
            if not allowed:
                return False, reason
        return True, "ok"

    def evaluate_grant_id(
        self,
        grant_id: str,
        *,
        capability: str,
        tenant_id: str,
        subject_id: str,
        scope_kind: str,
        scope_id: str,
        direction: str,
        data_type: str,
        purpose: str,
        epoch: int,
    ) -> tuple[bool, str]:
        persisted = self._repository.get(grant_id)
        if persisted is None:
            return False, "capability_not_found"
        return self.evaluate(
            persisted.grant,
            capability=capability,
            tenant_id=tenant_id,
            subject_id=subject_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            direction=direction,
            data_type=data_type,
            purpose=purpose,
            epoch=epoch,
        )

    def require_grant_id(self, grant_id: str, **context: object) -> SemanticMediaCapabilityGrant:
        allowed, reason = self.evaluate_grant_id(grant_id, **context)  # type: ignore[arg-type]
        if not allowed:
            status = 404 if reason == "capability_not_found" else 403
            raise SemanticMediaPermissionError(reason, status_code=status)
        persisted = self._repository.get(grant_id)
        if persisted is None:  # Defensive against an externally deleting adapter.
            raise SemanticMediaPermissionError("capability_not_found", status_code=404)
        return persisted.grant

    def get(self, grant_id: str) -> PersistedSemanticMediaCapabilityGrant | None:
        return self._repository.get(grant_id)

    def list_scope(
        self,
        *,
        tenant_id: str,
        scope_kind: str,
        scope_id: str,
        epoch: int,
        owner_id: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[PersistedSemanticMediaCapabilityGrant, ...]:
        if not 1 <= limit <= 200:
            raise SemanticMediaPermissionError("capability_limit_invalid", status_code=400)
        return self._repository.list_scope(
            tenant_id=tenant_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            epoch=epoch,
            owner_id=owner_id,
            subject_id=subject_id,
            limit=limit,
        )

    def revoke(
        self,
        grant_id: str,
        *,
        tenant_id: str | None = None,
        actor_id: str | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant:
        current = self._repository.get(grant_id)
        if current is None or (tenant_id is not None and current.grant.tenant_id != tenant_id):
            raise SemanticMediaPermissionError("capability_not_found", status_code=404)
        actor = actor_id or current.grant.owner_id
        if actor not in {current.grant.owner_id, current.grant.subject_id}:
            raise SemanticMediaPermissionError("capability_revoke_denied")
        try:
            result = self._repository.revoke(
                grant_id,
                tenant_id=current.grant.tenant_id,
                revoked_by=actor,
                revoked_at=float(self._clock()),
                audit_event=self._prepare_audit(current.grant, transition="revoked"),
            )
        except SemanticMediaCapabilityGrantRepositoryError as exc:
            raise SemanticMediaPermissionError(exc.reason_code, status_code=503) from exc
        if result is None:
            raise SemanticMediaPermissionError("capability_not_found", status_code=404)
        return result

    def _sign(self, grant: SemanticMediaCapabilityGrant) -> str:
        return hmac.new(self._key, _canonical_bytes(grant.unsigned_dict()), hashlib.sha256).hexdigest()

    def _prepare_audit(
        self,
        grant: SemanticMediaCapabilityGrant,
        *,
        transition: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        return self._audit.prepare_transition(
            idempotency_key=f"semantic-capability:{grant.grant_id}:{transition}",
            tenant_id=grant.tenant_id,
            scope=f"{grant.scope_kind}:{grant.scope_id}",
            event_type="semantic_admission",
            transition=f"capability_{transition}",
            reason_code=f"capability_{transition}",
            epoch=grant.epoch,
            contract_ref=grant.grant_id,
        )


def _bounded(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value.encode("utf-8")) <= maximum


__all__ = ["SemanticMediaPermissionError", "SemanticMediaPermissionService"]

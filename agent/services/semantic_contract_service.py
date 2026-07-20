"""Application service for authenticated semantic-compute contract APIs."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.repositories.semantic_contract_repository import (
    ContractMutation,
    SemanticContractRepository,
    SemanticContractRepositoryError,
    SemanticPrincipal,
    get_semantic_contract_repository,
)
from agent.services.semantic_compute_negotiation import (
    NegotiationContext,
    SemanticComputeNegotiation,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from ananta_contracts.semantic_compute import SemanticComputeContractError, canonical_json


class SemanticContractServiceError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class HubContractSigner:
    """Small signing port; secret material never enters persisted/read models."""

    def __init__(self, secret: bytes | None = None, *, key_id: str = "semantic-hub-v1") -> None:
        self._secret = secret
        self._key_id = key_id

    def sign(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if not self._secret or len(self._secret) < 32:
            raise SemanticContractServiceError("signer_unavailable", status_code=503)
        digest = str(payload.get("contract_digest") or "")
        signature = hmac.new(self._secret, digest.encode(), hashlib.sha256).hexdigest()
        return {"algorithm": "hmac-sha256", "key_id": self._key_id, "value": signature}


class SemanticContractAuditPort(Protocol):
    def prepare_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 604_800_000,
    ) -> SemanticMediaAuditEvent: ...


class SemanticContractLeaseRevokerPort(Protocol):
    def revoke_contract_active(self, *, tenant_id: str, owner_subject: str, contract_id: str) -> int: ...

    def revoke_contract_active_in_session(
        self,
        db,
        *,
        tenant_id: str,
        owner_subject: str,
        contract_id: str,
    ) -> int: ...


class SemanticContractService:
    _PROPOSAL_FIELDS = frozenset(
        {
            "profile",
            "quality_level",
            "delay_ms",
            "security_mode",
            "trusted_compute_grant",
            "task_types",
            "max_artifact_bytes",
            "deadline_ms",
            "expires_at_ms",
        }
    )

    def __init__(
        self,
        repository: SemanticContractRepository,
        *,
        negotiation: SemanticComputeNegotiation | None = None,
        signer: HubContractSigner | None = None,
        clock: Callable[[], float] = time.time,
        feature_enabled: Callable[[], bool] | None = None,
        audit: SemanticContractAuditPort | None = None,
        lease_revoker: SemanticContractLeaseRevokerPort | None = None,
    ) -> None:
        self._repository = repository
        self._negotiation = negotiation or SemanticComputeNegotiation()
        secret = os.environ.get("ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY", "").encode()
        self._signer = signer or HubContractSigner(secret or None)
        self._clock = clock
        self._feature_enabled = feature_enabled or self._default_feature_enabled
        self._audit = audit
        self._lease_revoker = lease_revoker

    def establish_membership(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        role: str,
        permitted: bool,
        room_id: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        try:
            self._repository.require_membership(
                principal,
                session_id=session_id,
                epoch=epoch,
                permission="semantic_compute",
            )
            return
        except SemanticContractRepositoryError as exc:
            if exc.reason_code != "session_not_found":
                raise self._repository_error(exc) from exc
        audit_event = self._prepare_membership_transition(
            principal,
            session_id=session_id,
            epoch=epoch,
        )
        self._repository.put_membership(
            principal,
            session_id=session_id,
            epoch=epoch,
            role=role,
            permissions={"semantic_compute": bool(permitted)},
            room_id=room_id,
            expires_at=expires_at,
            audit_event=audit_event,
        )

    def create_offer(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        room_id: str | None,
        epoch: int,
        policy_version: str,
        consent_version: int,
        security_confirmed: bool,
        fallback_healthy: bool,
        proposal: Mapping[str, Any],
        advertisements: Sequence[Mapping[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        proposal = self._validated_proposal(proposal)
        self._repository.require_membership(
            principal, session_id=session_id, epoch=epoch, permission="semantic_compute"
        )
        request_digest = hashlib.sha256(
            canonical_json(
                {
                    "session_id": session_id,
                    "room_id": room_id,
                    "epoch": epoch,
                    "policy_version": policy_version,
                    "consent_version": consent_version,
                    "proposal": dict(proposal),
                    "advertisements": list(advertisements),
                }
            )
        ).hexdigest()
        try:
            replay = self._repository.get_create_replay(
                principal,
                session_id=session_id,
                epoch=epoch,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        if replay is not None:
            return self._read_model(
                replay,
                replayed=True,
                reason_code="offer_accepted",
            )
        now_ms = int(self._clock() * 1000)
        decision = self._decide(
            action="offer",
            session_id=session_id,
            room_id=room_id,
            epoch=epoch,
            policy_version=policy_version,
            consent_version=consent_version,
            security_confirmed=security_confirmed,
            fallback_healthy=fallback_healthy,
            proposal=proposal,
            advertisements=advertisements,
            prior_contract=None,
            now_ms=now_ms,
            started_at_ms=now_ms,
            round_number=1,
            message_count=1,
        )
        if decision.contract is None:
            raise SemanticContractServiceError(decision.reason_code)
        payload = self._signed(decision.contract)
        audit_event = self._prepare_authoritative_transition(
            principal,
            session_id=session_id,
            epoch=epoch,
            contract_digest=str(payload["contract_digest"]),
            action="offered",
            reason_code=decision.reason_code,
            idempotency_key=f"semantic-contract:offer:{idempotency_key}",
        )
        try:
            item, replayed = self._repository.create(
                principal,
                contract_id=str(payload["contract_id"]),
                request_digest=request_digest,
                idempotency_key=idempotency_key,
                payload=payload,
                status="offered",
                negotiation_started_at_ms=now_ms,
                negotiation_round_count=1,
                negotiation_message_count=1,
                audit_event=audit_event,
            )
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        return self._read_model(item, replayed=replayed, reason_code=decision.reason_code)

    def mutate(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        session_id: str,
        epoch: int,
        action: str,
        expected_revision: int,
        idempotency_key: str,
        proposal: Mapping[str, Any],
        consent_version: int,
        security_confirmed: bool,
        fallback_healthy: bool,
        advertisements: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        proposal = self._validated_proposal(proposal)
        self._repository.require_membership(
            principal, session_id=session_id, epoch=epoch, permission="semantic_compute"
        )
        request_digest = hashlib.sha256(
            canonical_json(
                {
                    "action": action,
                    "contract_id": contract_id,
                    "session_id": session_id,
                    "epoch": epoch,
                    "expected_revision": expected_revision,
                    "proposal": dict(proposal),
                    "consent_version": consent_version,
                    "advertisements": list(advertisements),
                }
            )
        ).hexdigest()
        try:
            replay = self._repository.get_mutation_replay(
                principal,
                contract_id=contract_id,
                operation=action,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        if replay is not None:
            if replay.session_id != session_id or replay.epoch != epoch:
                raise SemanticContractServiceError("contract_not_found", status_code=404)
            return self._read_model(
                replay,
                replayed=True,
                reason_code=self._success_reason(action),
            )
        try:
            current = self._repository.get(principal, contract_id)
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        if current.session_id != session_id or current.epoch != epoch:
            raise SemanticContractServiceError("contract_not_found", status_code=404)
        now_ms = int(self._clock() * 1000)
        next_round_count = int(current.negotiation_round_count) + (1 if action == "counter" else 0)
        next_message_count = int(current.negotiation_message_count) + 1
        decision = self._decide(
            action=action,
            session_id=session_id,
            room_id=current.room_id,
            epoch=epoch,
            policy_version=current.policy_version,
            consent_version=consent_version,
            security_confirmed=security_confirmed,
            fallback_healthy=fallback_healthy,
            proposal=proposal,
            advertisements=advertisements,
            prior_contract=current.contract_payload,
            now_ms=now_ms,
            started_at_ms=int(current.negotiation_started_at_ms),
            round_number=next_round_count,
            message_count=next_message_count,
        )
        if decision.contract is None:
            raise SemanticContractServiceError(decision.reason_code)
        payload = self._signed(decision.contract)
        status = {
            "counter": "countered",
            "accept": "accepted",
            "activate": "active",
            "revoke": "revoked",
            "fallback": "fallback",
        }.get(action)
        if status is None:
            raise SemanticContractServiceError("action_invalid", status_code=400)
        audit_event = self._prepare_authoritative_transition(
            principal,
            session_id=session_id,
            epoch=epoch,
            contract_digest=str(payload["contract_digest"]),
            action=status,
            reason_code=decision.reason_code,
            idempotency_key=f"semantic-contract:{action}:{idempotency_key}",
        )
        try:
            item, replayed = self._repository.mutate(
                principal,
                contract_id=contract_id,
                mutation=ContractMutation(
                    operation=action,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    expected_revision=expected_revision,
                    expected_digest=current.digest,
                    payload=payload,
                    status=status,
                    activate=status == "active",
                    negotiation_started_at_ms=int(current.negotiation_started_at_ms),
                    negotiation_round_count=next_round_count,
                    negotiation_message_count=next_message_count,
                ),
                audit_event=audit_event,
                lease_revoker=self._lease_revoker,
            )
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        return self._read_model(item, replayed=replayed, reason_code=decision.reason_code)

    def _prepare_membership_transition(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=(
                    f"semantic-membership:granted:{principal.tenant_id}:{session_id}:{principal.subject}:{epoch}"
                ),
                tenant_id=principal.tenant_id,
                scope=f"semantic-media-session:{session_id}",
                event_type="semantic_admission",
                transition="membership_granted",
                reason_code="hub_share_authorized",
                epoch=epoch,
                job_ref=f"semantic-membership:{session_id}:{principal.subject}:{epoch}",
            )
        except Exception as exc:
            raise SemanticContractServiceError(
                "semantic_audit_unavailable",
                status_code=503,
            ) from exc

    def _prepare_authoritative_transition(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        contract_digest: str,
        action: str,
        reason_code: str,
        idempotency_key: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                scope=f"semantic-contract:{session_id}",
                event_type="semantic_contract",
                transition=action,
                reason_code=reason_code,
                epoch=epoch,
                contract_ref=contract_digest,
            )
        except Exception as exc:
            # Event construction happens before the repository transaction;
            # failure therefore cannot leave a durable unaudited mutation.
            raise SemanticContractServiceError(
                "semantic_audit_unavailable",
                status_code=503,
            ) from exc

    def detail(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        session_id: str,
        epoch: int,
    ) -> dict[str, Any]:
        self._repository.require_membership(
            principal, session_id=session_id, epoch=epoch, permission="semantic_compute"
        )
        try:
            item = self._repository.get(principal, contract_id)
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        if item.session_id != session_id or item.epoch != epoch:
            raise SemanticContractServiceError("contract_not_found", status_code=404)
        return self._read_model(item)

    def list(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        self._repository.require_membership(
            principal, session_id=session_id, epoch=epoch, permission="semantic_compute"
        )
        try:
            items = self._repository.list(principal, session_id=session_id, offset=offset, limit=limit)
        except SemanticContractRepositoryError as exc:
            raise self._repository_error(exc) from exc
        filtered = [item for item in items if item.epoch == epoch]
        return {
            "items": [self._read_model(item) for item in filtered],
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(filtered) if len(filtered) == limit else None,
        }

    def _decide(self, **kwargs):
        now_ms = int(kwargs.pop("now_ms"))
        started_at_ms = int(kwargs.pop("started_at_ms"))
        context = NegotiationContext(
            session_id=str(kwargs.pop("session_id")),
            room_id=kwargs.pop("room_id"),
            epoch=int(kwargs.pop("epoch")),
            policy_version=str(kwargs.pop("policy_version")),
            now_ms=now_ms,
            started_at_ms=started_at_ms,
            feature_enabled=self._feature_enabled(),
            permission_granted=True,
            consent_version=int(kwargs.pop("consent_version")),
            security_confirmed=bool(kwargs.pop("security_confirmed")),
            fallback_healthy=bool(kwargs.pop("fallback_healthy")),
        )
        try:
            return self._negotiation.decide(context=context, **kwargs)
        except SemanticComputeContractError as exc:
            raise SemanticContractServiceError(exc.reason_code, status_code=400) from exc

    def _signed(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(contract)
        payload["signature"] = self._signer.sign(payload)
        return payload

    @classmethod
    def _validated_proposal(cls, proposal: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal, Mapping) or set(proposal) - cls._PROPOSAL_FIELDS:
            raise SemanticContractServiceError("proposal_unknown_field", status_code=400)
        normalized = dict(proposal)
        try:
            canonical_json(normalized)
        except SemanticComputeContractError as exc:
            raise SemanticContractServiceError(exc.reason_code, status_code=400) from exc
        return normalized

    @staticmethod
    def _read_model(item, *, replayed: bool = False, reason_code: str | None = None) -> dict[str, Any]:
        payload = dict(item.contract_payload or {})
        # Closed projection: capability raw measurements and signatures never leave this endpoint.
        return {
            "contract_id": item.id,
            "session_id": item.session_id,
            "room_id": item.room_id,
            "epoch": item.epoch,
            "revision": item.revision,
            "digest": item.digest,
            "status": item.status,
            "profile": item.profile,
            "quality_level": payload.get("quality_level"),
            "security_mode": item.security_mode,
            "consent_version": item.consent_version,
            "policy_version": item.policy_version,
            "delay_ms": payload.get("delay_ms"),
            "roles": payload.get("roles", {}),
            "task_types": payload.get("task_types", []),
            "max_artifact_bytes": payload.get("max_artifact_bytes"),
            "deadline_ms": payload.get("deadline_ms"),
            "expires_at_ms": payload.get("expires_at_ms"),
            "issuer": "hub",
            "reason_code": reason_code,
            "idempotent_replay": replayed,
            "negotiation_budget": {
                "started_at_ms": item.negotiation_started_at_ms,
                "round_count": item.negotiation_round_count,
                "message_count": item.negotiation_message_count,
            },
        }

    @staticmethod
    def _success_reason(action: str) -> str:
        return {
            "offer": "offer_accepted",
            "counter": "counter_accepted",
            "accept": "accept_accepted",
            "activate": "activate_accepted",
            "revoke": "revoked_by_user",
            "fallback": "ordinary_fallback",
        }.get(action, "mutation_accepted")

    @staticmethod
    def _repository_error(exc: SemanticContractRepositoryError) -> SemanticContractServiceError:
        statuses = {
            "contract_not_found": 404,
            "session_not_found": 404,
            "pagination_out_of_bounds": 400,
            "idempotency_conflict": 409,
            "stale_revision": 412,
            "stale_digest": 412,
            "lease_revocation_unavailable": 503,
            "negotiation_budget_conflict": 412,
            "negotiation_budget_invalid": 400,
        }
        return SemanticContractServiceError(exc.reason_code, status_code=statuses.get(exc.reason_code, 409))

    @staticmethod
    def _default_feature_enabled() -> bool:
        from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags

        flags = resolve_semantic_media_feature_flags(os.environ)
        return bool(flags.get("semantic_visual_capture") or flags.get("semantic_speech_runtime"))


_service: SemanticContractService | None = None


def get_semantic_contract_service() -> SemanticContractService:
    global _service
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            configured = current_app.extensions.get("semantic_contract_service")
            if isinstance(configured, SemanticContractService):
                return configured
            from agent.repositories.semantic_lease_repository import (
                get_semantic_lease_repository,
            )

            audit = current_app.extensions.get("semantic_media_audit_recorder")
            leases = get_semantic_lease_repository(audit=audit)
            configured = SemanticContractService(
                get_semantic_contract_repository(),
                audit=audit,
                lease_revoker=leases,
            )
            current_app.extensions["semantic_contract_service"] = configured
            return configured
    except RuntimeError:
        pass
    if _service is None:
        from agent.repositories.semantic_lease_repository import get_semantic_lease_repository

        _service = SemanticContractService(
            get_semantic_contract_repository(),
            lease_revoker=get_semantic_lease_repository(),
        )
    return _service


__all__ = [
    "HubContractSigner",
    "SemanticContractAuditPort",
    "SemanticContractLeaseRevokerPort",
    "SemanticContractService",
    "SemanticContractServiceError",
    "get_semantic_contract_service",
]

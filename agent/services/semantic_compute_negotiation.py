"""Pure bounded state machine for Hub semantic-compute negotiation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ananta_contracts.semantic_compute import (
    CONTRACT_SCHEMA,
    SemanticComputeContractError,
    canonical_json,
    contract_digest,
    validate_capability_advertisement,
    validate_quality_contract,
)


@dataclass(frozen=True, slots=True)
class NegotiationLimits:
    max_rounds: int = 4
    max_messages: int = 16
    max_elapsed_ms: int = 10_000
    max_payload_bytes: int = 128 * 1024


@dataclass(frozen=True, slots=True)
class NegotiationContext:
    session_id: str
    room_id: str | None
    epoch: int
    policy_version: str
    now_ms: int
    started_at_ms: int
    feature_enabled: bool
    permission_granted: bool
    consent_version: int
    security_confirmed: bool
    fallback_healthy: bool


@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    state: str
    reason_code: str
    contract: dict[str, Any] | None
    authoritative: bool = True


class SemanticComputeNegotiation:
    """Deterministic Hub policy; clocks and policy inputs are explicit."""

    _ACTIONS = {"offer", "counter", "accept", "activate", "revoke", "fallback", "propose"}
    _MUTATING = {"offer", "counter", "accept", "activate", "revoke", "fallback"}

    def __init__(self, limits: NegotiationLimits | None = None) -> None:
        self._limits = limits or NegotiationLimits()

    def decide(
        self,
        *,
        action: str,
        context: NegotiationContext,
        proposal: Mapping[str, Any],
        advertisements: Sequence[Mapping[str, Any]] = (),
        prior_contract: Mapping[str, Any] | None = None,
        round_number: int = 1,
        message_count: int = 1,
    ) -> NegotiationDecision:
        if action not in self._ACTIONS:
            return NegotiationDecision("fallback", "unknown_action", None)
        if round_number < 1 or round_number > self._limits.max_rounds:
            return NegotiationDecision("fallback", "round_limit_exceeded", None)
        if message_count < 1 or message_count > self._limits.max_messages:
            return NegotiationDecision("fallback", "message_limit_exceeded", None)
        elapsed_ms = context.now_ms - context.started_at_ms
        # A wall-clock rollback must not extend a cross-process negotiation.
        # Monotonic clocks cannot be persisted across Hub replicas, so the
        # only safe response to a negative persisted duration is timeout.
        if elapsed_ms < 0 or elapsed_ms > self._limits.max_elapsed_ms:
            return NegotiationDecision("fallback", "negotiation_timeout", None)
        if len(canonical_json(dict(proposal))) > self._limits.max_payload_bytes:
            return NegotiationDecision("fallback", "payload_limit_exceeded", None)

        if action == "propose":
            # AI/Snake proposals are intentionally not a mutation result.
            sanitized = self._proposal_fields(proposal)
            return NegotiationDecision("suggested", "proposal_requires_hub_mutation", sanitized, False)
        if action == "revoke":
            if prior_contract is None:
                return NegotiationDecision("revoked", "already_absent", None)
            return self._revision("revoked", "revoked_by_user", context, prior_contract, proposal)
        if action == "fallback":
            if prior_contract is None:
                return NegotiationDecision("fallback", "ordinary_fallback", None)
            return self._revision("fallback", "ordinary_fallback", context, prior_contract, {"profile": "off"})

        gate = self._activation_gate(context)
        if gate is not None:
            safe_state = "fallback" if context.fallback_healthy else "off"
            return NegotiationDecision(safe_state, gate, None)

        valid_ads: list[dict[str, Any]] = []
        try:
            for advertisement in advertisements:
                normalized = validate_capability_advertisement(advertisement, now_ms=context.now_ms)
                if normalized["session_id"] != context.session_id or normalized["epoch"] != context.epoch:
                    return NegotiationDecision("fallback", "capability_scope_mismatch", None)
                if context.room_id is not None and normalized.get("room_id") != context.room_id:
                    return NegotiationDecision("fallback", "capability_scope_mismatch", None)
                valid_ads.append(normalized)
        except SemanticComputeContractError as exc:
            return NegotiationDecision("fallback", exc.reason_code, None)

        if action in {"accept", "activate"} and not valid_ads:
            return NegotiationDecision("fallback", "capability_missing", None)
        if prior_contract is not None:
            try:
                current = validate_quality_contract(prior_contract)
            except SemanticComputeContractError as exc:
                return NegotiationDecision("fallback", exc.reason_code, None)
            if current["epoch"] != context.epoch or current["session_id"] != context.session_id:
                return NegotiationDecision("fallback", "stale_epoch", None)
            if action not in {"revoke", "fallback"} and current["expires_at_ms"] <= context.now_ms:
                return NegotiationDecision("fallback", "contract_expired", None)
            return self._revision(action, f"{action}_accepted", context, current, proposal)
        if action not in {"offer", "counter"}:
            return NegotiationDecision("fallback", "contract_missing", None)
        contract = self._initial_contract(context, proposal)
        return NegotiationDecision(action, f"{action}_accepted", contract)

    @staticmethod
    def _activation_gate(context: NegotiationContext) -> str | None:
        if not context.feature_enabled:
            return "feature_disabled"
        if not context.permission_granted:
            return "permission_denied"
        if context.consent_version < 1:
            return "consent_missing"
        if not context.security_confirmed:
            return "security_unconfirmed"
        if not context.fallback_healthy:
            return "fallback_unhealthy"
        return None

    def _initial_contract(self, context: NegotiationContext, proposal: Mapping[str, Any]) -> dict[str, Any]:
        fields = self._proposal_fields(proposal)
        seed = {
            "session_id": context.session_id,
            "room_id": context.room_id,
            "epoch": context.epoch,
            "policy_version": context.policy_version,
            "proposal": fields,
        }
        contract_id = f"semantic-contract-{hashlib.sha256(canonical_json(seed)).hexdigest()[:24]}"
        expires_at_ms = int(proposal.get("expires_at_ms", context.now_ms + 300_000))
        if not context.now_ms < expires_at_ms <= context.now_ms + 3_600_000:
            raise SemanticComputeContractError("contract_expiry_invalid", "contract expiry is outside policy")
        payload = {
            "schema": CONTRACT_SCHEMA,
            "contract_id": contract_id,
            "session_id": context.session_id,
            **({"room_id": context.room_id} if context.room_id else {}),
            "epoch": context.epoch,
            "revision": 1,
            "issuer": "hub",
            "policy_version": context.policy_version,
            **fields,
            "consent_version": context.consent_version,
            "expires_at_ms": expires_at_ms,
            "contract_digest": "0" * 64,
            "signature": {
                "algorithm": "hmac-sha256",
                "key_id": "hub-pending",
                "value": "pending-signature-000000000000",
            },
        }
        payload["contract_digest"] = contract_digest(payload)
        return validate_quality_contract(payload)

    def _revision(
        self,
        state: str,
        reason_code: str,
        context: NegotiationContext,
        prior: Mapping[str, Any],
        proposal: Mapping[str, Any],
    ) -> NegotiationDecision:
        payload = dict(prior)
        fields = self._proposal_fields({**dict(prior), **dict(proposal)})
        payload.update(fields)
        payload["revision"] = int(prior["revision"]) + 1
        payload["policy_version"] = context.policy_version
        payload["consent_version"] = context.consent_version
        expires_at_ms = int(proposal.get("expires_at_ms", prior["expires_at_ms"]))
        if state not in {"revoked", "fallback"} and not context.now_ms < expires_at_ms <= context.now_ms + 3_600_000:
            raise SemanticComputeContractError("contract_expiry_invalid", "contract expiry is outside policy")
        payload["expires_at_ms"] = expires_at_ms
        payload["signature"] = {
            "algorithm": "hmac-sha256",
            "key_id": "hub-pending",
            "value": "pending-signature-000000000000",
        }
        payload["contract_digest"] = contract_digest(payload)
        return NegotiationDecision(state, reason_code, validate_quality_contract(payload))

    @staticmethod
    def _proposal_fields(proposal: Mapping[str, Any]) -> dict[str, Any]:
        profile = str(proposal.get("profile", "off"))
        if profile not in {"off", "conservative", "balanced", "custom"}:
            raise SemanticComputeContractError("invalid_profile", "profile is invalid")
        delay_ms = proposal.get("delay_ms", 5_000)
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 2_000 <= delay_ms <= 20_000:
            raise SemanticComputeContractError("impossible_budget", "delay is outside policy")
        security_mode = str(proposal.get("security_mode", "strict_e2ee"))
        if security_mode not in {"strict_e2ee", "trusted_compute"}:
            raise SemanticComputeContractError("invalid_security_mode", "security mode is invalid")
        grant = proposal.get("trusted_compute_grant", False)
        if not isinstance(grant, bool):
            raise SemanticComputeContractError("invalid_boolean", "trusted compute grant must be boolean")
        if security_mode == "strict_e2ee" and grant:
            raise SemanticComputeContractError("strict_e2ee_server_forbidden", "strict E2EE forbids server compute")
        task_types = proposal.get("task_types", ["visual_extract"])
        max_artifact_bytes = proposal.get("max_artifact_bytes", 1_048_576)
        deadline_ms = proposal.get("deadline_ms", min(delay_ms, 10_000))
        return {
            "profile": profile,
            "quality_level": str(proposal.get("quality_level", "standard")),
            "delay_ms": delay_ms,
            "security_mode": security_mode,
            "trusted_compute_grant": grant,
            # Role assignment is a separate Hub-scheduler decision. A browser
            # proposal can never elect itself or another peer here.
            "roles": {},
            "task_types": task_types,
            "max_artifact_bytes": max_artifact_bytes,
            "deadline_ms": deadline_ms,
        }


__all__ = ["NegotiationContext", "NegotiationDecision", "NegotiationLimits", "SemanticComputeNegotiation"]

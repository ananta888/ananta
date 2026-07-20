"""Redacted deterministic explanations and non-authoritative suggestions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class SemanticComputeExplanationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_REASONS: dict[str, str] = {
    "offer_accepted": "Der Hub hat ein begrenztes Compute-Angebot angenommen.",
    "counter_accepted": "Der Hub hat den Gegenvorschlag nach festen Limits übernommen.",
    "accept_accepted": "Der Hub hat den Vertrag bestätigt; Leases bleiben separat.",
    "activate_accepted": "Der Hub hat den Vertrag nach Consent-, Security- und Fallback-Prüfung aktiviert.",
    "revoked_by_user": "Die Einwilligung wurde widerrufen; aktive Autorität wird beendet.",
    "ordinary_fallback": "Der sichere Ordinary-Pfad bleibt aktiv.",
    "feature_disabled": "Die Hub-Funktion ist deaktiviert.",
    "permission_denied": "Die Session-Berechtigung erlaubt Compute nicht.",
    "consent_missing": "Eine ausdrückliche Compute-Einwilligung fehlt.",
    "security_unconfirmed": "Der Hub konnte den Sicherheitsmodus nicht bestätigen.",
    "fallback_unhealthy": "Der Hub aktiviert Compute ohne gesunden Rückfallpfad nicht.",
    "capability_missing": "Es liegt kein aktuelles, passendes Capability-Angebot vor.",
    "contract_expired": "Der Compute-Vertrag ist abgelaufen und nicht mehr autoritativ.",
    "stale_epoch": "Die angegebene Session-Epoche ist nicht mehr aktuell.",
    "negotiation_timeout": "Die begrenzte Verhandlung ist abgelaufen.",
}
_STATES = {"off", "offered", "countered", "accepted", "active", "revoked", "fallback"}
_SUGGESTION_FIELDS = {"profile", "delay_ms", "rationale"}
_PROFILES = {"off", "conservative", "balanced", "custom"}


@dataclass(frozen=True, slots=True)
class SemanticComputeExplanation:
    state: str
    reason_code: str
    message: str
    revision: int
    contract_digest: str
    profile: str
    delay_ms: int
    authoritative_source: str = "hub"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "message": self.message,
            "revision": self.revision,
            "contract_digest": self.contract_digest,
            "profile": self.profile,
            "delay_ms": self.delay_ms,
            "authoritative_source": self.authoritative_source,
        }


class SemanticComputeExplanationService:
    """Never accepts media, raw measurements or authority-bearing mutation fields."""

    def explain(
        self,
        decision: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
    ) -> SemanticComputeExplanation:
        allowed = {"state", "reason_code", "revision", "contract_digest", "profile", "delay_ms"}
        if set(decision) - allowed:
            raise SemanticComputeExplanationError("explanation_field_forbidden")
        state = str(decision.get("state") or "")
        reason = str(decision.get("reason_code") or "")
        revision = decision.get("revision")
        digest = str(decision.get("contract_digest") or "")
        profile = str(decision.get("profile") or "")
        delay = decision.get("delay_ms")
        if state not in _STATES or reason not in _REASONS:
            raise SemanticComputeExplanationError("explanation_code_unknown")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise SemanticComputeExplanationError("explanation_revision_invalid")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise SemanticComputeExplanationError("explanation_digest_invalid")
        if (
            profile not in _PROFILES
            or isinstance(delay, bool)
            or not isinstance(delay, int)
            or not 2_000 <= delay <= 20_000
        ):
            raise SemanticComputeExplanationError("explanation_value_invalid")
        if expected_revision is not None and revision != expected_revision:
            raise SemanticComputeExplanationError("explanation_stale")
        if expected_digest is not None and digest != expected_digest:
            raise SemanticComputeExplanationError("explanation_stale")
        return SemanticComputeExplanation(state, reason, _REASONS[reason], revision, digest, profile, delay)

    def suggestion(self, raw: Mapping[str, Any] | str) -> dict[str, Any]:
        if isinstance(raw, str):
            # Natural-language prompt/tool output stays opaque rationale. It is
            # never interpreted as permissions, consent, contracts or leases.
            payload: dict[str, Any] = {"rationale": raw}
        elif isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            raise SemanticComputeExplanationError("suggestion_invalid")
        if set(payload) - _SUGGESTION_FIELDS:
            raise SemanticComputeExplanationError("suggestion_authority_field_forbidden")
        profile = payload.get("profile")
        if profile is not None and profile not in _PROFILES:
            raise SemanticComputeExplanationError("suggestion_profile_invalid")
        delay = payload.get("delay_ms")
        if delay is not None and (
            isinstance(delay, bool) or not isinstance(delay, int) or not 2_000 <= delay <= 20_000
        ):
            raise SemanticComputeExplanationError("suggestion_delay_invalid")
        rationale = str(payload.get("rationale") or "")
        if len(rationale.encode("utf-8")) > 2_048:
            raise SemanticComputeExplanationError("suggestion_too_large")
        # JSON round trip gives a stable, content-only projection.
        values = json.loads(json.dumps({key: payload[key] for key in sorted(payload) if key != "rationale"}))
        return {
            "authoritative": False,
            "requires_separate_hub_mutation": True,
            "suggested_values": values,
            "rationale": rationale,
        }


__all__ = [
    "SemanticComputeExplanation", "SemanticComputeExplanationError", "SemanticComputeExplanationService"
]

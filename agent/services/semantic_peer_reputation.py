"""Expiring semantic-role reputation, separate from result validation."""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.semantic_result_validator import SemanticReportAdmission


@dataclass(frozen=True)
class SemanticRoleConsequence:
    peer_id: str
    role: str
    lease_id: str
    action: str
    ordinary_baseline_affected: bool = False


@dataclass
class _Reputation:
    score: float
    samples: int
    expires_at_ms: int


class SemanticPeerReputation:
    def __init__(self, *, session_ttl_ms: int = 300_000) -> None:
        self._session_ttl_ms = session_ttl_ms
        self._session: dict[tuple[str, str, str], _Reputation] = {}
        self._long_term_opt_in: dict[str, str] = {}
        self._long_term: dict[tuple[str, str], _Reputation] = {}

    def apply(
        self,
        admission: SemanticReportAdmission,
        *,
        peer_id: str,
        role: str,
        now_ms: int,
    ) -> SemanticRoleConsequence | None:
        self.expire(now_ms)
        if not admission.admissible or admission.session_id is None or admission.validator_lease_id is None:
            return None
        sample = 1.0 if admission.verdict == "pass" else 0.0
        key = (admission.session_id, peer_id, role)
        reputation = self._session.get(key, _Reputation(0.5, 0, now_ms + self._session_ttl_ms))
        reputation.score = (reputation.score * reputation.samples + sample) / (reputation.samples + 1)
        reputation.samples += 1
        reputation.expires_at_ms = now_ms + self._session_ttl_ms
        self._session[key] = reputation
        if peer_id in self._long_term_opt_in:
            long_key = (peer_id, role)
            persisted = self._long_term.get(long_key, _Reputation(0.5, 0, 2**63 - 1))
            persisted.score = (persisted.score * persisted.samples + sample) / (persisted.samples + 1)
            persisted.samples += 1
            self._long_term[long_key] = persisted
        action = "keep_semantic_role" if admission.verdict == "pass" else "revoke_affected_semantic_lease"
        return SemanticRoleConsequence(peer_id, role, admission.validator_lease_id, action, False)

    def session_score(self, session_id: str, peer_id: str, role: str, *, now_ms: int) -> float | None:
        self.expire(now_ms)
        value = self._session.get((session_id, peer_id, role))
        return None if value is None else value.score

    def enable_long_term(self, peer_id: str, *, consent_id: str) -> None:
        if not peer_id or not consent_id:
            raise ValueError("long_term_reputation_consent_required")
        self._long_term_opt_in[peer_id] = consent_id

    def delete_peer(self, peer_id: str) -> None:
        self._long_term_opt_in.pop(peer_id, None)
        for key in [key for key in self._long_term if key[0] == peer_id]:
            self._long_term.pop(key, None)
        for key in [key for key in self._session if key[1] == peer_id]:
            self._session.pop(key, None)

    def long_term_score(self, peer_id: str, role: str) -> float | None:
        value = self._long_term.get((peer_id, role))
        return None if value is None else value.score

    def expire(self, now_ms: int) -> int:
        expired = [key for key, value in self._session.items() if value.expires_at_ms <= now_ms]
        for key in expired:
            self._session.pop(key, None)
        return len(expired)

from agent.services.semantic_peer_reputation import SemanticPeerReputation
from agent.services.semantic_result_validator import SemanticReportAdmission


def admission(verdict: str = "pass", admissible: bool = True) -> SemanticReportAdmission:
    return SemanticReportAdmission(admissible, verdict, "test", "session", "validator", "lease", 1, False)


def test_reputation_is_session_bound_expiring_and_bad_output_only_revokes_affected_role() -> None:
    reputation = SemanticPeerReputation(session_ttl_ms=100)
    consequence = reputation.apply(admission("fail"), peer_id="peer", role="visual_executor", now_ms=1000)
    assert consequence is not None
    assert consequence.action == "revoke_affected_semantic_lease"
    assert not consequence.ordinary_baseline_affected
    assert reputation.session_score("session", "peer", "visual_executor", now_ms=1099) == 0.0
    assert reputation.session_score("session", "peer", "visual_executor", now_ms=1100) is None
    assert reputation.apply(admission(admissible=False), peer_id="peer", role="visual_executor", now_ms=1200) is None


def test_long_term_reputation_requires_opt_in_and_has_delete_path() -> None:
    reputation = SemanticPeerReputation()
    reputation.apply(admission(), peer_id="peer", role="validator", now_ms=1000)
    assert reputation.long_term_score("peer", "validator") is None
    reputation.enable_long_term("peer", consent_id="consent-1")
    reputation.apply(admission(), peer_id="peer", role="validator", now_ms=1001)
    assert reputation.long_term_score("peer", "validator") == 1.0
    reputation.delete_peer("peer")
    assert reputation.long_term_score("peer", "validator") is None

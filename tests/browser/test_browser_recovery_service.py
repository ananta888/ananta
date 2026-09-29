from __future__ import annotations

from agent.services.browser_recovery_service import BrowserRecoveryService


def test_failure_classification_and_retry_policy():
    svc = BrowserRecoveryService()
    assert svc.classify_failure("policy_domain_not_allowed") == "security_denied"
    d = svc.decide(failure_class="transient_navigation", attempt=1, max_repair_attempts=2, fallback_allowed=True)
    assert d.action == "retry"


def test_security_failures_hard_blocked():
    svc = BrowserRecoveryService()
    d = svc.decide(failure_class="security_denied", attempt=1, max_repair_attempts=3, fallback_allowed=True)
    assert d.action == "block"


def test_strict_browser_evidence_disables_fallback():
    svc = BrowserRecoveryService()
    d = svc.decide(
        failure_class="backend_unavailable",
        attempt=2,
        max_repair_attempts=1,
        fallback_allowed=True,
        strict_browser_evidence=True,
    )
    assert d.action == "fail"

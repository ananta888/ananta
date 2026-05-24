from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserRecoveryDecision:
    action: str  # block|retry|needs_review|fail
    reason: str


class BrowserRecoveryService:
    def classify_failure(self, reason_code: str) -> str:
        rc = str(reason_code or "").strip().lower()
        if "policy" in rc or "domain_not_allowed" in rc or "security" in rc:
            return "security_denied"
        if "timeout" in rc:
            return "timeout"
        if "not_found" in rc:
            return "element_not_found"
        if "unavailable" in rc:
            return "backend_unavailable"
        return "transient_navigation"

    def decide(self, *, failure_class: str, attempt: int, max_repair_attempts: int, fallback_allowed: bool) -> BrowserRecoveryDecision:
        if failure_class in {"security_denied", "policy_denied"}:
            return BrowserRecoveryDecision("block", "hard_policy_block")
        if attempt < max_repair_attempts and failure_class in {"transient_navigation", "timeout", "element_not_found"}:
            return BrowserRecoveryDecision("retry", "bounded_retry")
        if fallback_allowed and failure_class in {"backend_unavailable", "timeout"}:
            return BrowserRecoveryDecision("needs_review", "fallback_consideration")
        return BrowserRecoveryDecision("fail", "terminal_failure")


_SERVICE = BrowserRecoveryService()


def get_browser_recovery_service() -> BrowserRecoveryService:
    return _SERVICE

from enum import Enum


class GovernanceReasonCode(str, Enum):
    # General
    UNKNOWN = "unknown"
    MANUAL_INTERVENTION = "manual_intervention"

    # Verification Failures (GRM-021)
    EXECUTION_FAILURE = "execution_failure"
    EXTERNAL_GATE_FAILURE = "external_gate_failure"
    QUALITY_EVIDENCE_MISSING = "quality_evidence_missing"
    QUALITY_GATE_FAILURE = "quality_gate_failure"
    RETRY_EXHAUSTED = "retry_exhausted"

    # Blocking Reasons (GRM-021)
    DEPENDENCY_MISSING = "dependency_missing"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    POLICY_VIOLATION = "policy_violation"
    INSUFFICIENT_PRIVILEGES = "insufficient_privileges"
    WAITING_FOR_REVIEW = "waiting_for_review"

    # Escalation Reasons (GRM-021)
    HUMAN_TAKEOVER = "human_takeover"
    PROVIDER_ERROR = "provider_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"

"""Fail-closed quality gate for read-only controlling rollout evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_SOURCE_REF = re.compile(r"^SRC_[A-Za-z0-9_.:-]+$")
_RUN_REF = re.compile(r"^RUN_[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class BusinessControllingAcceptanceReport:
    schema_version: str
    synthetic_pilot_passed: bool
    deterministic_rules_passed: bool
    tenant_isolation_passed: bool
    malformed_input_passed: bool
    executable_content_denied: bool
    policy_bypass_denied: bool
    provenance_tampering_denied: bool
    false_positive_rate: float
    maximum_false_positive_rate: float
    p95_runtime_ms: float
    maximum_p95_runtime_ms: float
    rollback_verified: bool
    global_switch_verified: bool
    catalog_switch_verified: bool
    automatic_financial_action_count: int
    source_refs: tuple[str, ...] = ()
    run_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessControllingReleaseDecision:
    local_acceptance_passed: bool
    production_release_allowed: bool
    reason_codes: tuple[str, ...]


class BusinessControllingEvidenceVerifierPort(Protocol):
    def verify_source_refs(self, refs: tuple[str, ...]) -> bool: ...

    def verify_run_refs(self, refs: tuple[str, ...]) -> bool: ...


class BusinessControllingReleaseGate:
    def __init__(
        self,
        verifier: BusinessControllingEvidenceVerifierPort | None = None,
    ) -> None:
        self._verifier = verifier

    def assess(
        self,
        report: BusinessControllingAcceptanceReport,
    ) -> BusinessControllingReleaseDecision:
        reasons: list[str] = []
        if report.schema_version != "ananta.business-controlling-acceptance.v1":
            reasons.append("controlling_gate_schema_invalid")
        checks = {
            "synthetic_pilot": report.synthetic_pilot_passed,
            "deterministic_rules": report.deterministic_rules_passed,
            "tenant_isolation": report.tenant_isolation_passed,
            "malformed_input": report.malformed_input_passed,
            "executable_content": report.executable_content_denied,
            "policy_bypass": report.policy_bypass_denied,
            "provenance_tampering": report.provenance_tampering_denied,
            "rollback": report.rollback_verified,
            "global_switch": report.global_switch_verified,
            "catalog_switch": report.catalog_switch_verified,
        }
        reasons.extend(
            f"controlling_gate_{name}_failed"
            for name, passed in checks.items()
            if passed is not True
        )
        if (
            report.false_positive_rate < 0
            or report.maximum_false_positive_rate < 0
            or report.false_positive_rate > report.maximum_false_positive_rate
        ):
            reasons.append("controlling_gate_false_positive_budget_failed")
        if (
            report.p95_runtime_ms < 0
            or report.maximum_p95_runtime_ms <= 0
            or report.p95_runtime_ms > report.maximum_p95_runtime_ms
        ):
            reasons.append("controlling_gate_performance_budget_failed")
        if report.automatic_financial_action_count != 0:
            reasons.append("controlling_gate_automatic_financial_action_detected")
        local_passed = not reasons
        source_refs_valid = not any(
            _SOURCE_REF.fullmatch(value) is None for value in report.source_refs
        )
        run_refs_valid = not any(
            _RUN_REF.fullmatch(value) is None for value in report.run_refs
        )
        grounded = False
        if not source_refs_valid:
            reasons.append("controlling_gate_source_ref_invalid")
        if not run_refs_valid:
            reasons.append("controlling_gate_run_ref_invalid")
        if (
            report.source_refs
            and report.run_refs
            and source_refs_valid
            and run_refs_valid
            and self._verifier is not None
        ):
            try:
                grounded = bool(
                    self._verifier.verify_source_refs(report.source_refs)
                    and self._verifier.verify_run_refs(report.run_refs)
                )
            except Exception:
                grounded = False
        if not grounded:
            reasons.append("controlling_gate_production_evidence_unverified")
        return BusinessControllingReleaseDecision(
            local_acceptance_passed=local_passed,
            production_release_allowed=local_passed and grounded,
            reason_codes=tuple(reasons) or ("controlling_gate_passed",),
        )


__all__ = [
    "BusinessControllingAcceptanceReport",
    "BusinessControllingEvidenceVerifierPort",
    "BusinessControllingReleaseDecision",
    "BusinessControllingReleaseGate",
]

from __future__ import annotations

from agent.services.semantic_result_validator import SemanticResultValidator

DIGEST = "a" * 64


class Authority:
    signature = True
    audience = True
    lease = True

    def verify_signature(self, report, canonical_unsigned: bytes) -> bool:
        assert b'"signature"' not in canonical_unsigned
        return self.signature

    def valid_audience(self, audience: str, session_id: str) -> bool:
        return self.audience and audience == "hub" and session_id == "session"

    def valid_validator_lease(self, lease_id: str, validator_id: str, session_id: str, epoch: int, now_ms: int) -> bool:
        return self.lease and (lease_id, validator_id, session_id, epoch, now_ms) == (
            "lease", "validator", "session", 1, 1000,
        )


def report(**patch):
    value = {
        "schema": "ananta.semantic-validator-report.v1", "report_id": "report", "session_id": "session",
        "contract_id": "contract", "validator_lease_id": "lease", "validator_id": "validator",
        "validator_role": "validator", "audience": "hub", "epoch": 1, "sequence": 2,
        "input_digest": DIGEST, "output_digest": DIGEST,
        "criteria": {
            "schema_valid": True, "binding_valid": True, "quality_score": 0.9,
            "drift_score": 0.01, "deadline_met": True,
        },
        "verdict": "pass", "observed_at_ms": 900, "expires_at_ms": 2000,
        "signature": "signed-validator-report-0001",
    }
    value.update(patch)
    return value


def test_only_schema_signature_audience_and_lease_valid_report_is_admitted() -> None:
    authority = Authority()
    validator = SemanticResultValidator(authority)
    assert validator.admit(report(), now_ms=1000).admissible
    for field in ("signature", "audience", "lease"):
        setattr(authority, field, False)
        admission = validator.admit(report(), now_ms=1000)
        assert not admission.admissible
        assert not admission.ordinary_baseline_affected
        setattr(authority, field, True)
    assert not SemanticResultValidator(None).admit(report(), now_ms=1000).admissible


def test_conflicting_admitted_reports_trigger_recovery_and_raw_fields_are_rejected() -> None:
    validator = SemanticResultValidator(Authority())
    passed = validator.admit(report(), now_ms=1000)
    failed_report = report(
        report_id="failed",
        criteria={
            "schema_valid": False, "binding_valid": True, "quality_score": 0.9,
            "drift_score": 0.01, "deadline_met": True,
        },
        verdict="fail",
    )
    failed = validator.admit(failed_report, now_ms=1000)
    assert validator.reconcile([passed, failed]) == "semantic_recovery"
    leaked = report()
    leaked["pixels"] = [1, 2, 3]
    assert validator.admit(leaked, now_ms=1000).reason_code == "validator_schema_invalid"

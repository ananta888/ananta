from agent.services.semantic_visual_admission import (
    SemanticVisualAdmissionService,
    VisualAdmissionRequest,
    VisualReleaseEvidence,
)


class Gate:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed

    def current(self):
        return VisualReleaseEvidence(
            self.passed, True, "semantic-visual-release-gate/1.0.0", "a" * 64
        )


class Sfu:
    def __init__(self, ready: bool = True) -> None:
        self.available = ready

    def ready(self, *, session_id: str, epoch: int) -> bool:
        return self.available and session_id == "session" and epoch == 1


def request(**patch):
    values = {
        "session_id": "session", "epoch": 1, "now_ms": 1000,
        "capture_flag": True, "sfu_flag": True, "consent_active": True,
        "contract_id": "contract", "contract_expires_at_ms": 2000,
        "lease_id": "lease", "lease_expires_at_ms": 2000,
        "quality_report_id": "quality", "quality_report_expires_at_ms": 2000,
    }
    values.update(patch)
    return VisualAdmissionRequest(**values)


def test_hub_admission_requires_release_sfu_consent_contract_lease_and_quality() -> None:
    assert SemanticVisualAdmissionService(release_gate=Gate(), sfu_admission=Sfu()).admit(request()).semantic_active
    cases = [
        (None, Sfu(), "visual_release_gate_closed"),
        (Gate(False), Sfu(), "visual_release_gate_closed"),
        (Gate(), None, "visual_sfu_admission_unavailable"),
        (Gate(), Sfu(False), "visual_sfu_admission_unavailable"),
    ]
    for gate, sfu, reason in cases:
        decision = SemanticVisualAdmissionService(release_gate=gate, sfu_admission=sfu).admit(request())
        assert not decision.semantic_active
        assert decision.delivery_mode == "ordinary"
        assert decision.ordinary_baseline_available
        assert decision.reason_code == reason


def test_missing_authority_only_falls_back_affected_semantic_path() -> None:
    service = SemanticVisualAdmissionService(release_gate=Gate(), sfu_admission=Sfu())
    for patch in (
        {"capture_flag": False}, {"consent_active": False}, {"contract_id": None},
        {"lease_id": None}, {"quality_report_id": None},
    ):
        decision = service.admit(request(**patch))
        assert decision.delivery_mode == "ordinary"
        assert decision.ordinary_baseline_available

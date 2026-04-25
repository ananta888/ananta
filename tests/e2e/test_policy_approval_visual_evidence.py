from __future__ import annotations

from pathlib import Path

from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_policy_approval_visual_evidence_covers_denied_required_and_approved_states(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_policy_approval_visual_evidence(run_id="policy-approval-001")

    assert result.flow_entry["status"] == "passed"
    assert result.flow_entry["blocking"] is True
    assert len(result.flow_entry["trace_bundle_refs"]) == 2
    assert result.artifact_refs

    approval_required_text = _resolve_ref(result.snapshot_refs["approval_required"]).read_text(encoding="utf-8").lower()
    policy_denied_text = _resolve_ref(result.snapshot_refs["policy_denied"]).read_text(encoding="utf-8").lower()
    approved_safe_text = _resolve_ref(result.snapshot_refs["approved_safe"]).read_text(encoding="utf-8").lower()

    assert "approval_required" in approval_required_text
    assert "executed: no" in approval_required_text
    assert "status: denied" in policy_denied_text
    assert "executed: no" in policy_denied_text
    assert "status: success" in approved_safe_text
    assert "executed: yes" in approved_safe_text

    assert "tui_policy_denied" in result.snapshot_refs
    assert "web_approval_required" in result.snapshot_refs
    assert "web_approval_required" in result.screenshot_refs

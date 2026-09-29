"""Tests for artifact_enforcer.py (EW-T051)."""
import pytest
from worker.core.artifact_enforcer import (
    ArtifactEnforcer,
    ArtifactEnforcementResult,
    CAPABILITY_ARTIFACT_MAP,
    KNOWN_ARTIFACT_KINDS,
)


def _art(kind: str, artifact_id: str = "a1") -> dict:
    return {"kind": kind, "artifact_id": artifact_id}


class TestArtifactEnforcerCheck:
    def setup_method(self):
        self.enforcer = ArtifactEnforcer()

    # ── Compliant cases ──────────────────────────────────────────────────────

    def test_planning_with_plan_artifact_compliant(self):
        result = self.enforcer.check(
            ["planning"], [_art("plan_artifact")], summary="Plan a1"
        )
        assert result.compliant is True
        assert result.violations == []

    def test_patch_propose_with_patch_artifact_compliant(self):
        result = self.enforcer.check(
            ["patch_propose"], [_art("patch_artifact")], summary="Patch a1"
        )
        assert result.compliant is True

    def test_patch_propose_with_patch_candidate_compliant(self):
        result = self.enforcer.check(
            ["patch_propose"], [_art("patch_candidate")], summary="Patch a1"
        )
        assert result.compliant is True

    def test_shell_plan_with_command_plan_artifact_compliant(self):
        result = self.enforcer.check(
            ["shell_plan"], [_art("command_plan_artifact")], summary="a1"
        )
        assert result.compliant is True

    def test_shell_execute_with_command_result_artifact_compliant(self):
        result = self.enforcer.check(
            ["shell_execute"], [_art("command_result_artifact")], summary="a1"
        )
        assert result.compliant is True

    def test_test_run_with_test_result_artifact_compliant(self):
        result = self.enforcer.check(
            ["test_run"], [_art("test_result_artifact")], summary="a1"
        )
        assert result.compliant is True

    def test_verify_with_verification_artifact_compliant(self):
        result = self.enforcer.check(
            ["verify"], [_art("verification_artifact")], summary="a1"
        )
        assert result.compliant is True

    def test_no_capabilities_no_artifacts_compliant(self):
        result = self.enforcer.check([], [], summary="")
        assert result.compliant is True

    def test_non_artifact_capability_no_artifacts_compliant(self):
        result = self.enforcer.check(["code_read"], [], summary="read some file")
        assert result.compliant is True

    def test_multiple_capabilities_all_satisfied(self):
        result = self.enforcer.check(
            ["planning", "patch_propose"],
            [_art("plan_artifact", "a1"), _art("patch_artifact", "a2")],
            summary="a1 a2",
        )
        assert result.compliant is True

    # ── Violation: missing artifact for capability ───────────────────────────

    def test_planning_without_artifact_violates(self):
        result = self.enforcer.check(["planning"], [], summary="")
        assert result.compliant is False
        assert any("planning" in v for v in result.violations)

    def test_patch_propose_without_artifact_violates(self):
        result = self.enforcer.check(["patch_propose"], [], summary="")
        assert result.compliant is False

    def test_shell_execute_without_artifact_violates(self):
        result = self.enforcer.check(["shell_execute"], [], summary="")
        assert result.compliant is False

    def test_test_run_without_artifact_violates(self):
        result = self.enforcer.check(["test_run"], [], summary="")
        assert result.compliant is False

    # ── Violation: unknown artifact kind ────────────────────────────────────

    def test_unknown_artifact_kind_violates(self):
        result = self.enforcer.check(
            [], [_art("magic_result_artifact")], summary=""
        )
        assert result.compliant is False
        assert any("magic_result_artifact" in v for v in result.violations)

    def test_empty_kind_not_flagged(self):
        result = self.enforcer.check([], [{"artifact_id": "a1"}], summary="")
        assert result.compliant is True

    # ── Warning: summary doesn't reference artifact id ───────────────────────

    def test_summary_missing_artifact_ref_warns(self):
        result = self.enforcer.check(
            ["planning"],
            [_art("plan_artifact", "plan-001")],
            summary="I made a plan",
        )
        assert result.compliant is True
        assert any("artifact_id" in w for w in result.warnings)

    def test_summary_with_artifact_ref_no_warning(self):
        result = self.enforcer.check(
            ["planning"],
            [_art("plan_artifact", "plan-001")],
            summary="Plan plan-001 completed",
        )
        assert result.warnings == []

    def test_no_summary_no_warning(self):
        result = self.enforcer.check(
            ["planning"],
            [_art("plan_artifact", "plan-001")],
            summary="",
        )
        assert result.warnings == []

    # ── Free-text-only rejection ─────────────────────────────────────────────

    def test_free_text_only_with_required_capability_rejected(self):
        result = self.enforcer.check(
            ["planning", "patch_propose"], [], summary="I fixed it, trust me"
        )
        assert result.compliant is False
        assert any("free-text" in v for v in result.violations)

    def test_multiple_violations_all_recorded(self):
        result = self.enforcer.check(
            ["planning", "shell_execute"], [], summary=""
        )
        assert result.compliant is False
        assert len(result.violations) >= 2

    # ── Known artifact vocabulary completeness ──────────────────────────────

    def test_all_known_kinds_accepted(self):
        for kind in KNOWN_ARTIFACT_KINDS:
            r = self.enforcer.check([], [{"kind": kind, "artifact_id": "x"}], "")
            assert r.compliant is True, f"kind {kind!r} rejected unexpectedly"

    def test_capability_artifact_map_keys_are_capabilities(self):
        from worker.core.execution_envelope import KNOWN_CAPABILITY_CLASSES
        for cap in CAPABILITY_ARTIFACT_MAP:
            assert cap in KNOWN_CAPABILITY_CLASSES, f"{cap!r} not in vocabulary"

    def test_capability_artifact_map_values_are_known_kinds(self):
        for kinds in CAPABILITY_ARTIFACT_MAP.values():
            for k in kinds:
                assert k in KNOWN_ARTIFACT_KINDS, f"{k!r} not in KNOWN_ARTIFACT_KINDS"


class TestBuildSummaryWithRefs:
    def setup_method(self):
        self.enforcer = ArtifactEnforcer()

    def test_no_artifacts_returns_description(self):
        summary = self.enforcer.build_summary_with_refs("Completed task", [])
        assert summary == "Completed task"

    def test_artifact_id_appended(self):
        summary = self.enforcer.build_summary_with_refs(
            "Completed task", [{"artifact_id": "plan-001"}]
        )
        assert "plan-001" in summary

    def test_multiple_artifact_ids_all_appended(self):
        summary = self.enforcer.build_summary_with_refs(
            "Done", [{"artifact_id": "a1"}, {"artifact_id": "a2"}]
        )
        assert "a1" in summary and "a2" in summary

    def test_artifacts_without_ids_not_appended(self):
        summary = self.enforcer.build_summary_with_refs(
            "Done", [{"kind": "plan_artifact"}]
        )
        assert summary == "Done"

    def test_uses_id_field_as_fallback(self):
        summary = self.enforcer.build_summary_with_refs(
            "Done", [{"id": "fallback-id"}]
        )
        assert "fallback-id" in summary

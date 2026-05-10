"""Tests for scheduled_job.py (EW-T054, EW-T055, EW-T056)."""
import time
import pytest
from worker.core.scheduled_job import (
    ApprovalCheckResult,
    ApprovalMode,
    ContextPolicy,
    DeliveryTarget,
    HeadlessApprovalPolicy,
    JobRunArtifact,
    JobRunArtifactBuilder,
    JobStatus,
    ScheduledJobContract,
)


def _contract(**overrides) -> ScheduledJobContract:
    defaults = dict(
        job_id="job-001",
        task_template="Run daily lint check",
        capability_grant_ids=["planning", "code_read"],
        schedule_cron="0 2 * * *",
        context_policy=ContextPolicy(max_tokens=4096),
        approval_mode=ApprovalMode.pre_approved,
        max_runtime_seconds=300,
        delivery_target=DeliveryTarget.hub,
        pre_approved_ref_ids=["ref-001"],
    )
    defaults.update(overrides)
    return ScheduledJobContract(**defaults)


# ── EW-T054: ScheduledJobContract ────────────────────────────────────────────

class TestScheduledJobContract:
    def test_creates_valid_contract(self):
        c = _contract()
        assert c.job_id == "job-001"
        assert c.max_runtime_seconds == 300
        assert c.delivery_target == DeliveryTarget.hub

    def test_zero_max_runtime_rejected(self):
        with pytest.raises(ValueError, match="max_runtime_seconds"):
            _contract(max_runtime_seconds=0)

    def test_negative_max_runtime_rejected(self):
        with pytest.raises(ValueError):
            _contract(max_runtime_seconds=-1)

    def test_callback_url_target_requires_url(self):
        with pytest.raises(ValueError, match="delivery_url"):
            _contract(
                delivery_target=DeliveryTarget.callback_url,
                delivery_url="",
            )

    def test_callback_url_target_with_url_accepted(self):
        c = _contract(
            delivery_target=DeliveryTarget.callback_url,
            delivery_url="http://localhost:8080/callback",
        )
        assert c.delivery_url == "http://localhost:8080/callback"

    def test_artifact_store_target_no_url_needed(self):
        c = _contract(delivery_target=DeliveryTarget.artifact_store)
        assert c.delivery_target == DeliveryTarget.artifact_store

    def test_frozen_contract_immutable(self):
        c = _contract()
        with pytest.raises((TypeError, AttributeError)):
            c.job_id = "modified"  # type: ignore

    def test_context_policy_max_tokens_positive(self):
        with pytest.raises(ValueError, match="max_tokens"):
            ContextPolicy(max_tokens=0)

    def test_context_policy_defaults(self):
        cp = ContextPolicy()
        assert cp.cloud_allowed is False
        assert cp.include_session_history is False
        assert cp.max_tokens > 0

    def test_approval_modes_exist(self):
        for mode in (ApprovalMode.pre_approved, ApprovalMode.confirm_required, ApprovalMode.auto_deny):
            c = _contract(approval_mode=mode)
            assert c.approval_mode == mode


# ── EW-T055: HeadlessApprovalPolicy ──────────────────────────────────────────

class TestHeadlessApprovalPolicy:
    def setup_method(self):
        self.policy = HeadlessApprovalPolicy()

    def test_pre_approved_with_valid_ref_allowed(self):
        c = _contract(approval_mode=ApprovalMode.pre_approved, pre_approved_ref_ids=["ref-001"])
        result = self.policy.check(c, operation="shell_execute", ref_id="ref-001")
        assert result.allowed is True

    def test_pre_approved_empty_refs_blocked(self):
        c = _contract(approval_mode=ApprovalMode.pre_approved, pre_approved_ref_ids=[])
        result = self.policy.check(c, operation="shell_execute", ref_id="ref-001")
        assert result.allowed is False
        assert result.reason_code == "approval_missing"

    def test_pre_approved_unknown_ref_blocked(self):
        c = _contract(approval_mode=ApprovalMode.pre_approved, pre_approved_ref_ids=["ref-001"])
        result = self.policy.check(c, operation="shell_execute", ref_id="unknown-ref")
        assert result.allowed is False
        assert result.reason_code == "approval_ref_not_found"

    def test_pre_approved_no_ref_id_uses_list(self):
        c = _contract(approval_mode=ApprovalMode.pre_approved, pre_approved_ref_ids=["ref-001"])
        result = self.policy.check(c, operation="shell_execute", ref_id="")
        assert result.allowed is True

    def test_confirm_required_with_matching_ref_allowed(self):
        c = _contract(
            approval_mode=ApprovalMode.confirm_required,
            pre_approved_ref_ids=["ref-headless"],
        )
        result = self.policy.check(c, operation="patch_apply", ref_id="ref-headless")
        assert result.allowed is True

    def test_confirm_required_without_ref_blocked(self):
        c = _contract(
            approval_mode=ApprovalMode.confirm_required,
            pre_approved_ref_ids=[],
        )
        result = self.policy.check(c, operation="patch_apply", ref_id="")
        assert result.allowed is False
        assert result.reason_code == "approval_missing"

    def test_confirm_required_wrong_ref_blocked(self):
        c = _contract(
            approval_mode=ApprovalMode.confirm_required,
            pre_approved_ref_ids=["ref-correct"],
        )
        result = self.policy.check(c, operation="patch_apply", ref_id="ref-wrong")
        assert result.allowed is False
        assert result.reason_code == "approval_missing"

    def test_auto_deny_always_blocked(self):
        c = _contract(approval_mode=ApprovalMode.auto_deny)
        result = self.policy.check(c, operation="shell_execute", ref_id="ref-001")
        assert result.allowed is False
        assert result.reason_code == "approval_auto_denied"

    def test_check_can_run_headless_auto_deny_blocked(self):
        c = _contract(approval_mode=ApprovalMode.auto_deny)
        result = self.policy.check_can_run_headless(c, ["shell_execute"])
        assert result.allowed is False

    def test_check_can_run_headless_no_sensitive_ops_ok(self):
        c = _contract(approval_mode=ApprovalMode.auto_deny)
        result = self.policy.check_can_run_headless(c, [])
        assert result.allowed is True

    def test_check_can_run_headless_confirm_required_no_refs_blocked(self):
        c = _contract(
            approval_mode=ApprovalMode.confirm_required,
            pre_approved_ref_ids=[],
        )
        result = self.policy.check_can_run_headless(c, ["patch_apply"])
        assert result.allowed is False

    def test_check_can_run_headless_pre_approved_with_refs_ok(self):
        c = _contract(
            approval_mode=ApprovalMode.pre_approved,
            pre_approved_ref_ids=["ref-001"],
        )
        result = self.policy.check_can_run_headless(c, ["patch_apply"])
        assert result.allowed is True


# ── EW-T056: JobRunArtifact / JobRunArtifactBuilder ──────────────────────────

class TestJobRunArtifact:
    def test_as_dict_required_fields(self):
        artifact = JobRunArtifact(
            artifact_id="a1",
            job_id="job-001",
            task_id="t1",
            status=JobStatus.success,
            started_at=1000.0,
            ended_at=1010.0,
        )
        d = artifact.as_dict()
        for key in ("kind", "artifact_id", "job_id", "task_id", "status",
                    "started_at", "ended_at", "duration_seconds", "warnings",
                    "retry_recommended", "trace_bundle_ref"):
            assert key in d, f"missing {key!r}"

    def test_kind_is_job_run_artifact(self):
        artifact = JobRunArtifact(
            artifact_id="a1", job_id="j1", task_id="t1",
            status=JobStatus.success, started_at=0.0, ended_at=1.0,
        )
        assert artifact.as_dict()["kind"] == "job_run_artifact"

    def test_duration_calculated(self):
        artifact = JobRunArtifact(
            artifact_id="a1", job_id="j1", task_id="t1",
            status=JobStatus.success, started_at=1000.0, ended_at=1015.0,
        )
        assert artifact.duration_seconds == pytest.approx(15.0)

    def test_artifact_refs_extracted(self):
        artifact = JobRunArtifact(
            artifact_id="a1", job_id="j1", task_id="t1",
            status=JobStatus.success, started_at=0.0, ended_at=1.0,
            artifacts=[{"artifact_id": "patch-001"}, {"id": "plan-002"}],
        )
        d = artifact.as_dict()
        assert "patch-001" in d["artifact_refs"]
        assert "plan-002" in d["artifact_refs"]

    def test_all_statuses_serializable(self):
        for status in JobStatus:
            a = JobRunArtifact(
                artifact_id="x", job_id="j", task_id="t",
                status=status, started_at=0.0, ended_at=1.0,
            )
            d = a.as_dict()
            assert d["status"] == status.value


class TestJobRunArtifactBuilder:
    def test_build_success(self):
        builder = JobRunArtifactBuilder("job-001", "t1")
        artifact = builder.finish(JobStatus.success)
        assert artifact.status == JobStatus.success
        assert artifact.job_id == "job-001"
        assert artifact.task_id == "t1"

    def test_artifact_id_unique(self):
        a1 = JobRunArtifactBuilder("j", "t").finish(JobStatus.success)
        a2 = JobRunArtifactBuilder("j", "t").finish(JobStatus.success)
        assert a1.artifact_id != a2.artifact_id

    def test_add_artifact(self):
        builder = JobRunArtifactBuilder("j", "t")
        builder.add_artifact({"artifact_id": "p1", "kind": "patch_artifact"})
        artifact = builder.finish(JobStatus.success)
        assert len(artifact.artifacts) == 1

    def test_add_warning(self):
        builder = JobRunArtifactBuilder("j", "t")
        builder.add_warning("context truncated")
        artifact = builder.finish(JobStatus.success)
        assert "context truncated" in artifact.warnings

    def test_set_trace_ref(self):
        builder = JobRunArtifactBuilder("j", "t")
        builder.set_trace_ref("trace:abc")
        artifact = builder.finish(JobStatus.success)
        assert artifact.trace_bundle_ref == "trace:abc"

    def test_retry_recommended_when_failure_and_retry_limit(self):
        contract = _contract(retry_limit=3)
        builder = JobRunArtifactBuilder("job-001", "t")
        builder.set_retry_count(0)
        artifact = builder.finish(JobStatus.failure, contract=contract)
        assert artifact.retry_recommended is True

    def test_retry_not_recommended_at_limit(self):
        contract = _contract(retry_limit=2)
        builder = JobRunArtifactBuilder("job-001", "t")
        builder.set_retry_count(2)
        artifact = builder.finish(JobStatus.failure, contract=contract)
        assert artifact.retry_recommended is False

    def test_retry_not_recommended_on_success(self):
        contract = _contract(retry_limit=3)
        builder = JobRunArtifactBuilder("job-001", "t")
        builder.set_retry_count(0)
        artifact = builder.finish(JobStatus.success, contract=contract)
        assert artifact.retry_recommended is False

    def test_timeout_with_retry_limit_recommends_retry(self):
        contract = _contract(retry_limit=1)
        builder = JobRunArtifactBuilder("job-001", "t")
        artifact = builder.finish(JobStatus.timeout, contract=contract)
        assert artifact.retry_recommended is True

    def test_builder_timestamps_monotonic(self):
        builder = JobRunArtifactBuilder("j", "t")
        artifact = builder.finish(JobStatus.success)
        assert artifact.ended_at >= artifact.started_at

    def test_set_error(self):
        builder = JobRunArtifactBuilder("j", "t")
        builder.set_error("something went wrong")
        artifact = builder.finish(JobStatus.failure)
        assert artifact.error_detail == "something went wrong"

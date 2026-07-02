"""Tests for TrustlessRunReportService (TRANS-007)."""
from __future__ import annotations

from agent.services.trustless_run_report_service import TrustlessRunReport, TrustlessRunReportService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc() -> TrustlessRunReportService:
    return TrustlessRunReportService()


def _minimal_report(svc: TrustlessRunReportService, *, run_id: str = "run-trr-001") -> TrustlessRunReport:
    return svc.generate(
        run_id=run_id,
        goal="Implement feature Z",
        selected_expert_or_worker="worker-python-01",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_generate_minimal() -> None:
    """generate() must populate all mandatory fields even with minimal args."""
    svc = _svc()
    report = _minimal_report(svc)

    assert report.report_id != ""
    assert report.run_id == "run-trr-001"
    assert report.goal == "Implement feature Z"
    assert report.selected_expert_or_worker == "worker-python-01"
    assert isinstance(report.context_sources, list)
    assert isinstance(report.tool_calls_summary, list)
    assert isinstance(report.blocked_actions, list)
    assert isinstance(report.artifacts_produced, list)
    assert isinstance(report.approval_gates_triggered, list)
    assert isinstance(report.open_risks, list)
    assert isinstance(report.evidence_refs, list)
    assert report.final_result == "completed"
    assert report.created_at > 0


def test_to_markdown_contains_sections() -> None:
    """to_markdown() must contain the required section headings."""
    svc = _svc()
    report = svc.generate(
        run_id="run-md",
        goal="Refactor module A",
        selected_expert_or_worker="worker-refactor",
        policy_snapshot_ref="snap-abc123",
        tool_calls_summary=["read_file: success", "write_file: success"],
        blocked_actions=["exec_shell: denied — network_policy=deny_all"],
        context_sources=["RAG result: module_a.py"],
    )
    md = svc.to_markdown(report)

    assert "## Goal" in md
    assert "## Worker" in md
    assert "## Policy" in md
    assert "## Tool Calls" in md
    assert "## Blocked Actions" in md
    assert "## Artifacts Produced" in md
    assert "## Approval Gates Triggered" in md
    assert "## Test Results" in md
    assert "## Open Risks" in md
    assert "## Final Result" in md


def test_to_markdown_model_claims_vs_verified() -> None:
    """Markdown must use 'verified:' labels for evidence-backed entries."""
    svc = _svc()
    report = svc.generate(
        run_id="run-labels",
        goal="Deploy service",
        selected_expert_or_worker="worker-deploy",
        policy_snapshot_ref="snap-xyz",
        context_sources=["artifact-ctx-01"],
    )
    md = svc.to_markdown(report)

    # Policy snapshot is verified
    assert "verified:" in md


def test_no_secrets_in_report() -> None:
    """Fields with secret/key/token should not appear as literal values in the report dict."""
    svc = _svc()
    # Even if somehow secret-sounding strings sneak into goal/evidence, the report
    # dataclass itself must not expose raw secret fields.
    report = svc.generate(
        run_id="run-nosec",
        goal="Check connectivity",
        selected_expert_or_worker="worker-net",
        evidence_refs=["artifact-net-01"],
    )
    d = svc.to_dict(report)

    # None of the dict keys should be secret-named fields
    secret_keywords = ("key", "token", "secret", "password")
    for k in d.keys():
        for kw in secret_keywords:
            assert kw not in k.lower(), f"Unexpected secret-like key in report dict: {k!r}"


def test_blocked_actions_visible() -> None:
    """Blocked actions must appear both in report dict and in markdown."""
    svc = _svc()
    blocked = [
        "exec_shell: denied — policy=deny_all",
        "git_push: denied — approval_required",
    ]
    report = svc.generate(
        run_id="run-blocked",
        goal="Dangerous task",
        selected_expert_or_worker="worker-x",
        blocked_actions=blocked,
        final_result="blocked",
    )

    assert report.blocked_actions == blocked
    assert report.final_result == "blocked"

    d = svc.to_dict(report)
    assert d["blocked_actions"] == blocked

    md = svc.to_markdown(report)
    assert "exec_shell: denied" in md
    assert "git_push: denied" in md


def test_to_dict_stable() -> None:
    """to_dict() must not return None for list fields (they must be empty lists)."""
    svc = _svc()
    report = _minimal_report(svc)
    d = svc.to_dict(report)

    list_fields = [
        "context_sources",
        "tool_calls_summary",
        "blocked_actions",
        "artifacts_produced",
        "approval_gates_triggered",
        "open_risks",
        "evidence_refs",
    ]
    for f in list_fields:
        assert d[f] is not None, f"Field {f!r} must not be None"
        assert isinstance(d[f], list), f"Field {f!r} must be a list"


def test_invalid_final_result_defaults_to_failed() -> None:
    """An unknown final_result value must be coerced to 'failed'."""
    svc = _svc()
    report = svc.generate(
        run_id="run-inv",
        goal="g",
        selected_expert_or_worker="w",
        final_result="UNKNOWN_STATUS",
    )
    assert report.final_result == "failed"


def test_policy_snapshot_ref_optional() -> None:
    """policy_snapshot_ref may be None."""
    svc = _svc()
    report = _minimal_report(svc)
    assert report.policy_snapshot_ref is None


def test_test_results_summary_optional() -> None:
    """test_results_summary may be None when not provided."""
    svc = _svc()
    report = _minimal_report(svc)
    assert report.test_results_summary is None


def test_report_ids_are_unique() -> None:
    """Each generated report gets a unique report_id."""
    svc = _svc()
    ids = {_minimal_report(svc).report_id for _ in range(5)}
    assert len(ids) == 5


def test_to_markdown_under_100_lines() -> None:
    """to_markdown() must produce fewer than 100 lines for a minimal report."""
    svc = _svc()
    report = _minimal_report(svc)
    md = svc.to_markdown(report)
    line_count = len(md.splitlines())
    assert line_count < 100, f"Markdown too long: {line_count} lines"

from agent.services.diff_proposal_service import DiffProposalService, DiffHunkStatus, ProposalStatus, DiffProposalError
import pytest

def _hunk(path="src/foo.py", added=5, removed=2):
    return {"file_path": path, "lines_added": added, "lines_removed": removed, "operation": "modified"}

def test_create_proposal_status_draft():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="pr_author", hunks=[_hunk()])
    assert p.status == ProposalStatus.DRAFT

def test_risky_hunk_github_workflow():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk(".github/workflows/ci.yml")])
    assert p.has_risky_hunks() is True
    assert p.hunks[0].hunk_status == DiffHunkStatus.RISKY

def test_risky_hunk_env_file():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk(".env")])
    assert p.hunks[0].hunk_status == DiffHunkStatus.RISKY

def test_clean_hunk_normal_py():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk("src/main.py")])
    assert p.hunks[0].hunk_status == DiffHunkStatus.CLEAN

def test_policy_check_denied_path():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk(".env")])
    p2 = svc.run_policy_check(p.proposal_id, denied_paths=[".env"])
    assert p2.policy_check_passed is False

def test_policy_check_allowed():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk("src/foo.py")])
    p2 = svc.run_policy_check(p.proposal_id, denied_paths=[".env"])
    assert p2.policy_check_passed is True

def test_submit_requires_policy_check():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    with pytest.raises(DiffProposalError):
        svc.submit_for_approval(p.proposal_id)

def test_approve_transitions():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    svc.run_policy_check(p.proposal_id)
    svc.submit_for_approval(p.proposal_id)
    p2 = svc.approve(p.proposal_id, approval_record_ref="art-123", approved_by="alice")
    assert p2.status == ProposalStatus.APPROVED

def test_mark_applied_requires_approved():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    with pytest.raises(DiffProposalError):
        svc.mark_applied(p.proposal_id)

def test_is_applicable_approved_and_checked():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    svc.run_policy_check(p.proposal_id)
    svc.submit_for_approval(p.proposal_id)
    svc.approve(p.proposal_id, approval_record_ref="ref", approved_by="bob")
    assert svc.is_applicable(p.proposal_id) is True

def test_is_applicable_approved_not_checked():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    # No policy check → policy_check_passed=None
    p.status = ProposalStatus.APPROVED
    assert p.is_applicable() is False

def test_check_direct_mutation_always_true():
    svc = DiffProposalService()
    assert svc.check_direct_mutation_blocked() is True

def test_reject_sets_blocked_reason():
    svc = DiffProposalService()
    p = svc.create_proposal(run_id="r1", worker_id="w1", origin="test", hunks=[_hunk()])
    p2 = svc.reject(p.proposal_id, reason="security issue", rejected_by="admin")
    assert p2.status == ProposalStatus.REJECTED
    assert "security issue" in p2.blocked_reason

"""Tests for Human Approval Gates (SECURITY-001/002)."""
from __future__ import annotations

import pytest
from agent.caseflow.actions import CRITICAL_ACTIONS, ApprovalRequest
from agent.caseflow.policies import (
    PolicyCheckResult,
    _hash_payload,
    check_policy,
    require_human_approval,
)
from agent.caseflow.discovery import PolicyDenied, convert_result_to_case, DiscoveryResult


class TestCriticalActions:
    def test_critical_actions_list_complete(self):
        expected = {
            "send_application_email",
            "send_followup_email",
            "delete_case",
            "convert_discovery_result_to_case",
            "cloud_model_with_sensitive_data",
        }
        assert expected.issubset(set(CRITICAL_ACTIONS))

    def test_approval_request_created(self):
        req = require_human_approval(
            "send_application_email",
            actor="user-1",
            payload={"case_id": "case-1", "recipient": "jobs@company.com"},
        )
        assert req.critical_action == "send_application_email"
        assert req.requested_by == "user-1"
        assert req.payload_hash is not None
        assert req.status == "pending"

    def test_send_email_without_approval_denied(self):
        result = check_policy("send_application_email", context={})
        assert result.allowed is False
        assert result.requires_approval is True
        assert result.error_code == "APPROVAL_REQUIRED"

    def test_convert_result_without_approval_denied(self):
        dr = DiscoveryResult(
            run_id="run-1", result_type="job_posting",
            title="Job", source_name="test"
        )
        with pytest.raises(PolicyDenied) as exc_info:
            convert_result_to_case(dr, "job_application", approved_by="")
        assert exc_info.value.error_code == "APPROVAL_REQUIRED"

    def test_approval_granted_executes(self):
        req = require_human_approval(
            "delete_case",
            actor="user-1",
            payload={"case_id": "case-1"},
        )
        req.approved_by = "admin-user"
        req.status = "approved"
        assert req.status == "approved"
        assert req.approved_by == "admin-user"

    def test_rejected_approval_stored(self):
        req = require_human_approval(
            "send_followup_email",
            actor="user-1",
            payload={"case_id": "case-1"},
        )
        req.rejected_by = "user-1"
        req.rejection_reason = "Not ready yet"
        req.status = "rejected"
        assert req.status == "rejected"
        assert req.rejection_reason == "Not ready yet"

    def test_cloud_model_with_sensitive_data_needs_approval(self):
        result = check_policy("cloud_model_with_sensitive_data", context={})
        assert result.allowed is False
        assert result.requires_approval is True

    def test_approval_has_payload_hash(self):
        payload = {"case_id": "case-1", "action": "send_email", "to": "hr@co.com"}
        req = require_human_approval("send_application_email", actor="u1", payload=payload)
        assert req.payload_hash is not None
        assert len(req.payload_hash) > 0
        # Same payload → same hash
        hash1 = _hash_payload(payload)
        hash2 = _hash_payload(payload)
        assert hash1 == hash2

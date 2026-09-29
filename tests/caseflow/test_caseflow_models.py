"""Tests for CaseFlow Core Domain Models (CASECORE-001)."""
from __future__ import annotations

import pytest
from agent.caseflow.models import CaseFlowCase, CaseTypeDefinition
from agent.caseflow.domain import register_case_type, get_case_type, list_case_types


class TestCaseFlowCase:
    def test_create_case_minimal(self):
        case = CaseFlowCase(case_type="generic", title="Test Case")
        assert case.case_type == "generic"
        assert case.title == "Test Case"
        assert case.status == "new"
        assert case.id is not None

    def test_case_type_is_free_string(self):
        for ct in ["job_application", "lead", "advisory", "foobar_type"]:
            c = CaseFlowCase(case_type=ct, title="T")
            assert c.case_type == ct

    def test_no_job_fields_in_core(self):
        case = CaseFlowCase(case_type="generic", title="T")
        assert not hasattr(case, "company_name")
        assert not hasattr(case, "salary")
        assert not hasattr(case, "job_title")
        assert not hasattr(case, "cover_letter")

    def test_domain_payload_accepts_any_dict(self):
        payload = {"company_name": "ACME", "salary": 80000, "tags": ["python", "remote"]}
        case = CaseFlowCase(case_type="job_application", title="T", domain_payload=payload)
        assert case.domain_payload["company_name"] == "ACME"
        assert case.domain_payload["salary"] == 80000

    def test_case_serialization(self):
        case = CaseFlowCase(case_type="generic", title="Serialization Test")
        data = case.model_dump()
        assert data["case_type"] == "generic"
        assert data["title"] == "Serialization Test"
        assert "created_at" in data
        assert "domain_payload" in data

    def test_case_default_status_new(self):
        case = CaseFlowCase(case_type="generic", title="T")
        assert case.status == "new"

    def test_register_case_type_definition(self):
        defn = CaseTypeDefinition(
            case_type="my_custom_type",
            statuses=["draft", "active", "closed"],
            initial_status="draft",
            terminal_statuses=["closed"],
        )
        register_case_type(defn)
        retrieved = get_case_type("my_custom_type")
        assert retrieved is not None
        assert retrieved.case_type == "my_custom_type"
        assert "active" in retrieved.statuses

    def test_get_unknown_case_type_returns_none(self):
        result = get_case_type("nonexistent_type_xyz")
        assert result is None

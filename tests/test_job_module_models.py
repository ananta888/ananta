"""Tests for Job Module Domain Models (JOBCORE-001/005)."""
from __future__ import annotations

import pytest
from agent.caseflow.models import CaseFlowCase
from agent.job_module.models import (
    EmploymentType,
    JobApplicationPayload,
    JOB_APPLICATION_INITIAL,
    JOB_APPLICATION_STATUSES,
    RemotePolicy,
)
from agent.job_module.document_bundle import (
    ApplicationDocumentBundle,
    DocumentStatus,
)


class TestJobApplicationPayload:
    def test_job_application_payload_minimal(self):
        payload = JobApplicationPayload(company_name="ACME", role_title="Dev")
        assert payload.company_name == "ACME"
        assert payload.role_title == "Dev"
        assert payload.remote_policy == RemotePolicy.unknown
        assert payload.tech_stack == []

    def test_job_application_is_case_with_type(self):
        payload = JobApplicationPayload(company_name="ACME", role_title="Dev")
        case = CaseFlowCase(
            case_type="job_application",
            title="ACME - Dev",
            domain_payload=payload.model_dump(),
        )
        assert case.case_type == "job_application"
        assert case.domain_payload["company_name"] == "ACME"

    def test_status_found_is_initial(self):
        assert JOB_APPLICATION_INITIAL == "found"

    def test_payload_mapping_to_domain_payload(self):
        payload = JobApplicationPayload(
            company_name="TechCorp",
            role_title="Senior Python Dev",
            remote_policy=RemotePolicy.remote,
            tech_stack=["python", "docker"],
        )
        case = CaseFlowCase(
            case_type="job_application",
            title="TechCorp - Senior Python Dev",
            domain_payload=payload.model_dump(),
        )
        reconstructed = JobApplicationPayload.model_validate(case.domain_payload)
        assert reconstructed.company_name == "TechCorp"
        assert reconstructed.remote_policy == RemotePolicy.remote
        assert "python" in reconstructed.tech_stack

    def test_empty_optional_fields_allowed(self):
        payload = JobApplicationPayload()
        assert payload.company_name == ""
        assert payload.job_url is None
        assert payload.contact_email is None
        assert payload.salary_min is None


class TestDocumentBundle:
    def test_document_bundle_missing_required(self):
        bundle = ApplicationDocumentBundle(case_id="case-1")
        missing = bundle.missing_required_docs()
        assert "cv" in missing
        assert "cover_letter" in missing
        assert "job_posting" in missing

    def test_document_bundle_cannot_send_without_approved(self):
        bundle = ApplicationDocumentBundle(case_id="case-1")
        assert bundle.can_send() is False

    def test_document_bundle_can_send_when_approved(self):
        bundle = ApplicationDocumentBundle(case_id="case-1")
        bundle.documents["cv"] = DocumentStatus(doc_type="cv", status="approved")
        bundle.documents["cover_letter"] = DocumentStatus(doc_type="cover_letter", status="approved")
        assert bundle.can_send() is True

    def test_document_bundle_completion_percent(self):
        bundle = ApplicationDocumentBundle(case_id="case-1")
        # No documents ready: 0%
        assert bundle.compute_completion() == 0.0
        # One of three required docs approved
        bundle.documents["cv"] = DocumentStatus(doc_type="cv", status="approved")
        comp = bundle.compute_completion()
        assert abs(comp - 33.33) < 1

    def test_document_bundle_completion_full(self):
        bundle = ApplicationDocumentBundle(case_id="case-1")
        for doc in ["cv", "cover_letter", "job_posting"]:
            bundle.documents[doc] = DocumentStatus(doc_type=doc, status="approved")
        assert bundle.compute_completion() == 100.0

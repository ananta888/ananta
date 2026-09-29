"""Tests for CaseFlow Discovery Layer (DISCOVERY-001/002/004)."""
from __future__ import annotations

import pytest
from agent.caseflow.discovery import (
    DiscoveryResult,
    DiscoveryRun,
    PolicyDenied,
    SearchProfile,
    convert_result_to_case,
)
from agent.job_module.source_adapters import DummyJobAdapter


class TestSearchProfile:
    def test_search_profile_creation(self):
        p = SearchProfile(
            name="Software Jobs",
            query_terms=["python", "developer"],
            locations=["Berlin", "Remote"],
        )
        assert p.name == "Software Jobs"
        assert "python" in p.query_terms
        assert p.enabled is True
        assert p.id is not None

    def test_search_profile_can_be_disabled(self):
        p = SearchProfile(name="Disabled Profile", enabled=False)
        assert p.enabled is False


class TestDummyAdapter:
    def test_dummy_adapter_returns_empty_list(self):
        adapter = DummyJobAdapter()
        profile = SearchProfile(name="Test Profile")
        results = adapter.search(profile)
        assert results == []

    def test_dummy_adapter_source_id(self):
        adapter = DummyJobAdapter()
        assert adapter.source_id() == "dummy_job_source"


class TestDiscoveryRun:
    def test_discovery_run_stores_metadata(self):
        run = DiscoveryRun(profile_id="profile-1", trace_id="trace-123")
        assert run.profile_id == "profile-1"
        assert run.status == "running"
        assert run.trace_id == "trace-123"
        assert run.id is not None


class TestConvertResultToCase:
    def _make_result(self) -> DiscoveryResult:
        return DiscoveryResult(
            run_id="run-1",
            result_type="job_posting",
            title="Python Developer",
            source_name="dummy",
        )

    def test_convert_result_requires_approved_by(self):
        result = self._make_result()
        with pytest.raises(PolicyDenied) as exc_info:
            convert_result_to_case(result, "job_application", approved_by="")
        assert exc_info.value.error_code == "APPROVAL_REQUIRED"

    def test_convert_result_success(self):
        result = self._make_result()
        case = convert_result_to_case(result, "job_application", approved_by="user-1")
        assert case.case_type == "job_application"
        assert case.title == "Python Developer"
        assert case.source == "discovery"
        assert result.converted_to_case_id == case.id

    def test_convert_result_duplicate_prevented(self):
        result = self._make_result()
        case = convert_result_to_case(result, "job_application", approved_by="user-1")
        with pytest.raises(PolicyDenied) as exc_info:
            convert_result_to_case(result, "job_application", approved_by="user-1")
        assert exc_info.value.error_code == "DUPLICATE_CONVERSION"

    def test_convert_result_duplicate_allowed_with_flag(self):
        result = self._make_result()
        case1 = convert_result_to_case(result, "job_application", approved_by="user-1")
        # Reset so we can convert again
        result.converted_to_case_id = case1.id
        case2 = convert_result_to_case(
            result, "job_application", approved_by="user-1",
            options={"allow_duplicate": True}
        )
        assert case2.case_type == "job_application"

from __future__ import annotations

import pytest

from agent.services.business_controlling_import_service import (
    BusinessControllingImportError,
    BusinessControllingImportService,
)
from agent.services.business_controlling_import_workflow import BusinessControllingImportWorkflow
from agent.services.business_controlling_tabular_extractor import (
    BusinessControllingTabularExtractor,
    TabularExtractionRequest,
)


class _Admission:
    def is_admitted(self, **_scope: str) -> bool:
        return True


class _Artifacts:
    def __init__(self, payload: bytes = b"amount,currency\n12.30,EUR\n") -> None:
        self.payload = payload

    def read_bytes(self, **_scope: str) -> bytes:
        return self.payload


class _Profiles:
    def __init__(self) -> None:
        self.profile = None
        self.mapping = None

    def append_profile(self, **values):
        self.profile = values
        return values["profile"]

    def append_mapping(self, **values):
        self.mapping = values
        return values["confirmation"]


def _workflow(payload: bytes = b"amount,currency\n12.30,EUR\n") -> tuple[BusinessControllingImportWorkflow, _Profiles]:
    profiles = _Profiles()
    return (
        BusinessControllingImportWorkflow(
            extractor=BusinessControllingTabularExtractor(_Artifacts(payload)),
            profiler=BusinessControllingImportService(_Admission()),
            profiles=profiles,
        ),
        profiles,
    )


def test_hub_workflow_persists_profile_before_confirmed_mapping() -> None:
    workflow, profiles = _workflow()
    request = TabularExtractionRequest("tenant-a", "project-a", "srev-a", "a" * 64, "csv")
    profile = workflow.profile(request)
    assert profiles.profile == {"tenant_id": "tenant-a", "project_id": "project-a", "profile": profile}
    confirmation = workflow.confirm_mapping(
        tenant_id="tenant-a",
        project_id="project-a",
        profile=profile,
        mapping={"amount": "amount", "currency": "currency"},
        confirmed_by="operator-a",
    )
    assert profiles.mapping["confirmation"] == confirmation


def test_worker_cannot_orchestrate_import_or_confirmation() -> None:
    workflow, _profiles = _workflow()
    request = TabularExtractionRequest("tenant-a", "project-a", "srev-a", "a" * 64, "csv")
    with pytest.raises(PermissionError, match="hub_authority_required"):
        workflow.profile(request, authority="worker")

    profile = workflow.profile(request)
    with pytest.raises(PermissionError, match="hub_authority_required"):
        workflow.confirm_mapping(
            tenant_id="tenant-a",
            project_id="project-a",
            profile=profile,
            mapping={"amount": "amount"},
            confirmed_by="operator-a",
            authority="worker",
        )


def test_executable_csv_content_is_denied_by_composed_workflow() -> None:
    workflow, _profiles = _workflow(b"amount,currency\n=1+1,EUR\n")
    request = TabularExtractionRequest("tenant-a", "project-a", "srev-a", "a" * 64, "csv")
    with pytest.raises(BusinessControllingImportError, match="executable_content_denied"):
        workflow.profile(request)

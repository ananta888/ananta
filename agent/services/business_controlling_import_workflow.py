"""Hub application workflow for admitted tabular profiling and mapping confirmation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent.services.business_controlling_import_service import (
    BusinessControllingImportService,
    MappingConfirmation,
    TabularProfile,
)
from agent.services.business_controlling_tabular_extractor import (
    BusinessControllingTabularExtractor,
    TabularExtractionRequest,
)


class ControllingProfileStorePort(Protocol):
    def append_profile(
        self, *, tenant_id: str, project_id: str, profile: TabularProfile
    ) -> TabularProfile: ...

    def append_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        confirmation: MappingConfirmation,
    ) -> MappingConfirmation: ...


class BusinessControllingImportWorkflow:
    """Coordinates Hub-owned ports without exposing storage or file paths."""

    def __init__(
        self,
        *,
        extractor: BusinessControllingTabularExtractor,
        profiler: BusinessControllingImportService,
        profiles: ControllingProfileStorePort,
    ) -> None:
        self._extractor = extractor
        self._profiler = profiler
        self._profiles = profiles

    def profile(self, request: TabularExtractionRequest, *, authority: str = "hub") -> TabularProfile:
        if authority != "hub":
            raise PermissionError("controlling_import_hub_authority_required")
        extracted = self._extractor.extract(request)
        profile = self._profiler.profile(extracted)
        return self._profiles.append_profile(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            profile=profile,
        )

    def confirm_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        profile: TabularProfile,
        mapping: Mapping[str, str],
        confirmed_by: str,
        authority: str = "hub",
    ) -> MappingConfirmation:
        if authority != "hub":
            raise PermissionError("controlling_import_hub_authority_required")
        confirmation = self._profiler.confirm_mapping(profile, mapping, confirmed_by=confirmed_by)
        return self._profiles.append_mapping(
            tenant_id=tenant_id,
            project_id=project_id,
            confirmation=confirmation,
        )


__all__ = ["BusinessControllingImportWorkflow", "ControllingProfileStorePort"]

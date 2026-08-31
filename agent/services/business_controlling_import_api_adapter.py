"""HTTP-facing adapter for the admitted tabular import workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent.services.business_controlling_import_service import TabularProfile
from agent.services.business_controlling_import_workflow import (
    BusinessControllingImportWorkflow,
)
from agent.services.business_controlling_tabular_extractor import (
    TabularExtractionRequest,
)


class ControllingProfileReaderPort(Protocol):
    def get_profile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        profile_digest: str,
    ) -> TabularProfile | None: ...


class BusinessControllingImportApiAdapter:
    """Translate closed API payloads into the existing Hub import workflow."""

    def __init__(
        self,
        *,
        workflow: BusinessControllingImportWorkflow,
        profiles: ControllingProfileReaderPort,
    ) -> None:
        self._workflow = workflow
        self._profiles = profiles

    def profile_import(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        del actor_id
        allowed = {
            "tenant_id",
            "project_id",
            "source_revision_id",
            "revision_digest",
            "source_format",
            "sheet_name",
        }
        if not set(request_payload).issubset(allowed):
            raise ValueError("controlling_import_shape_invalid")
        profile = self._workflow.profile(
            TabularExtractionRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                source_revision_id=str(
                    request_payload.get("source_revision_id") or ""
                ),
                revision_digest=str(request_payload.get("revision_digest") or ""),
                source_format=str(request_payload.get("source_format") or ""),
                sheet_name=(
                    str(request_payload["sheet_name"])
                    if request_payload.get("sheet_name") is not None
                    else None
                ),
            )
        )
        return _profile_projection(profile)

    def confirm_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        allowed = {
            "tenant_id",
            "project_id",
            "profile_digest",
            "column_mapping",
        }
        if (
            not set(request_payload).issubset(allowed)
            or not {"project_id", "profile_digest", "column_mapping"}.issubset(
                request_payload
            )
        ):
            raise ValueError("controlling_mapping_shape_invalid")
        mapping = request_payload.get("column_mapping")
        if not isinstance(mapping, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in mapping.items()
        ):
            raise ValueError("controlling_mapping_shape_invalid")
        profile_digest = str(request_payload.get("profile_digest") or "")
        profile = self._profiles.get_profile(
            tenant_id=tenant_id,
            project_id=project_id,
            profile_digest=profile_digest,
        )
        if profile is None:
            raise ValueError("controlling_profile_not_found")
        confirmation = self._workflow.confirm_mapping(
            tenant_id=tenant_id,
            project_id=project_id,
            profile=profile,
            mapping=mapping,
            confirmed_by=actor_id,
        )
        return {
            "profile_digest": confirmation.profile_digest,
            "confirmation_digest": confirmation.confirmation_digest,
            "column_mapping": dict(confirmation.column_mapping),
        }


def _profile_projection(profile: TabularProfile) -> dict[str, object]:
    return {
        "profile_digest": profile.profile_digest,
        "source_revision_id": profile.source_revision_id,
        "revision_digest": profile.revision_digest,
        "row_count": profile.row_count,
        "duplicate_row_count": profile.duplicate_row_count,
        "columns": [
            {
                "header": column.header,
                "inferred_type": column.inferred_type,
                "null_count": column.null_count,
                "invalid_count": column.invalid_count,
                "invalid_locators": list(column.invalid_locators),
            }
            for column in profile.columns
        ],
    }


__all__ = [
    "BusinessControllingImportApiAdapter",
    "ControllingProfileReaderPort",
]

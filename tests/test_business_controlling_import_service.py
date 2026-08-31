from __future__ import annotations

import pytest

from agent.services.business_controlling_import_service import (
    BusinessControllingImportError,
    BusinessControllingImportService,
    TabularProfileRequest,
    WorkbookRiskMetadata,
)


class _Admission:
    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted

    def is_admitted(self, **_scope: str) -> bool:
        return self.admitted


def _request(**overrides: object) -> TabularProfileRequest:
    values = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "source_revision_id": "srev-a",
        "revision_digest": "a" * 64,
        "source_format": "csv",
        "headers": ("invoice", "amount", "currency", "period"),
        "rows": (
            ("INV-1", "12.30", "EUR", "2026-01-01"),
            ("INV-1", "12.30", "EUR", "2026-01-01"),
            ("INV-2", "invalid-secret-value", "EURO", "bad-date"),
        ),
        "risk": WorkbookRiskMetadata(),
    }
    values.update(overrides)
    return TabularProfileRequest(**values)  # type: ignore[arg-type]


def test_profile_requires_hub_admitted_source_and_returns_aggregates_only() -> None:
    with pytest.raises(BusinessControllingImportError, match="controlling_source_not_admitted"):
        BusinessControllingImportService(_Admission(False)).profile(_request())

    profile = BusinessControllingImportService(_Admission()).profile(_request())
    assert profile.row_count == 3
    assert profile.duplicate_row_count == 1
    assert {column.header: column.invalid_count for column in profile.columns} == {
        "invoice": 0,
        "amount": 1,
        "currency": 1,
        "period": 1,
    }
    assert "invalid-secret-value" not in repr(profile)


@pytest.mark.parametrize(
    "risk",
    [
        WorkbookRiskMetadata(has_macros=True),
        WorkbookRiskMetadata(has_external_links=True),
        WorkbookRiskMetadata(has_formula_cells=True),
        WorkbookRiskMetadata(has_unsupported_objects=True),
    ],
)
def test_xlsx_executable_or_external_content_is_never_executed(risk: WorkbookRiskMetadata) -> None:
    with pytest.raises(BusinessControllingImportError, match="controlling_workbook_executable_content_denied"):
        BusinessControllingImportService(_Admission()).profile(_request(source_format="xlsx", risk=risk))


def test_profile_is_deterministic_and_rejects_ragged_rows() -> None:
    service = BusinessControllingImportService(_Admission())
    assert service.profile(_request()).profile_digest == service.profile(_request()).profile_digest
    with pytest.raises(BusinessControllingImportError, match="controlling_rows_invalid"):
        service.profile(_request(rows=(("only-one-cell",),)))


def test_mapping_requires_explicit_valid_confirmation() -> None:
    service = BusinessControllingImportService(_Admission())
    profile = service.profile(_request())
    confirmation = service.confirm_mapping(
        profile,
        {"amount": "amount", "period": "period"},
        confirmed_by="operator-a",
    )
    assert confirmation.profile_digest == profile.profile_digest
    assert confirmation.confirmation_digest == service.confirm_mapping(
        profile,
        {"period": "period", "amount": "amount"},
        confirmed_by="operator-a",
    ).confirmation_digest
    with pytest.raises(BusinessControllingImportError, match="controlling_mapping_confirmation_invalid"):
        service.confirm_mapping(profile, {"unknown": "amount"}, confirmed_by="operator-a")

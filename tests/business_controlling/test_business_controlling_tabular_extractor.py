from __future__ import annotations

import io
import zipfile

import pytest
from openpyxl import Workbook

from agent.services.business_controlling_import_service import BusinessControllingImportError
from agent.services.business_controlling_tabular_extractor import (
    BusinessControllingTabularExtractor,
    TabularExtractionRequest,
)


class _Artifacts:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.scopes: list[dict[str, str]] = []

    def read_bytes(self, **scope: str) -> bytes:
        self.scopes.append(scope)
        return self.payload


def _request(source_format: str, *, sheet_name: str | None = None) -> TabularExtractionRequest:
    return TabularExtractionRequest(
        tenant_id="tenant-a",
        project_id="project-a",
        source_revision_id="srev-a",
        revision_digest="a" * 64,
        source_format=source_format,
        sheet_name=sheet_name,
    )


def _workbook_bytes(*, formula: bool = False, second_sheet: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(("invoice", "amount"))
    sheet.append(("INV-1", "=1+1" if formula else "12.30"))
    if second_sheet:
        workbook.create_sheet("Other").append(("ignored",))
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_csv_is_read_from_scoped_artifact_and_formula_injection_is_flagged() -> None:
    artifacts = _Artifacts(b"invoice,amount\nINV-1,12.30\nINV-2,=1+1\n")
    extracted = BusinessControllingTabularExtractor(artifacts).extract(_request("csv"))
    assert extracted.headers == ("invoice", "amount")
    assert extracted.risk.has_formula_cells is True
    assert artifacts.scopes == [
        {"tenant_id": "tenant-a", "project_id": "project-a", "source_revision_id": "srev-a"}
    ]


def test_xlsx_requires_explicit_visible_sheet_and_never_evaluates_formula() -> None:
    extractor = BusinessControllingTabularExtractor(_Artifacts(_workbook_bytes(formula=True, second_sheet=True)))
    with pytest.raises(BusinessControllingImportError, match="sheet_selection_required"):
        extractor.extract(_request("xlsx"))
    extracted = extractor.extract(_request("xlsx", sheet_name="Data"))
    assert extracted.risk.has_formula_cells is True


def test_xlsx_archive_traversal_and_embedded_objects_fail_closed() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../escape", "bad")
    with pytest.raises(BusinessControllingImportError, match="path_traversal_denied"):
        BusinessControllingTabularExtractor(_Artifacts(payload.getvalue())).extract(_request("xlsx"))

    embedded = io.BytesIO(_workbook_bytes())
    rewritten = io.BytesIO()
    with zipfile.ZipFile(embedded) as source, zipfile.ZipFile(rewritten, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, source.read(entry.filename))
        target.writestr("xl/embeddings/object.bin", b"opaque")
    extracted = BusinessControllingTabularExtractor(_Artifacts(rewritten.getvalue())).extract(_request("xlsx"))
    assert extracted.risk.has_unsupported_objects is True

"""Authorized, content-minimizing profiling for business tabular sources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, Sequence


class BusinessControllingImportError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ControllingSourceAdmissionPort(Protocol):
    def is_admitted(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        revision_digest: str,
    ) -> bool: ...


@dataclass(frozen=True)
class WorkbookRiskMetadata:
    has_macros: bool = False
    has_external_links: bool = False
    has_formula_cells: bool = False
    has_unsupported_objects: bool = False


@dataclass(frozen=True)
class TabularProfileRequest:
    tenant_id: str
    project_id: str
    source_revision_id: str
    revision_digest: str
    source_format: str
    headers: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    risk: WorkbookRiskMetadata = WorkbookRiskMetadata()


@dataclass(frozen=True)
class ColumnProfile:
    header: str
    inferred_type: str
    null_count: int
    invalid_count: int


@dataclass(frozen=True)
class TabularProfile:
    source_revision_id: str
    revision_digest: str
    row_count: int
    duplicate_row_count: int
    columns: tuple[ColumnProfile, ...]
    profile_digest: str


@dataclass(frozen=True)
class MappingConfirmation:
    profile_digest: str
    column_mapping: tuple[tuple[str, str], ...]
    confirmed_by: str
    confirmation_digest: str


class BusinessControllingImportService:
    MAX_ROWS = 100_000
    MAX_COLUMNS = 256

    def __init__(self, admission: ControllingSourceAdmissionPort) -> None:
        self._admission = admission

    def profile(self, request: TabularProfileRequest) -> TabularProfile:
        self._validate_request(request)
        if not self._admission.is_admitted(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            source_revision_id=request.source_revision_id,
            revision_digest=request.revision_digest,
        ):
            raise BusinessControllingImportError("controlling_source_not_admitted")
        if any(
            (
                request.risk.has_macros,
                request.risk.has_external_links,
                request.risk.has_formula_cells,
                request.risk.has_unsupported_objects,
            )
        ):
            raise BusinessControllingImportError("controlling_workbook_executable_content_denied")

        columns = tuple(
            self._profile_column(header, tuple(row[index] for row in request.rows))
            for index, header in enumerate(request.headers)
        )
        duplicate_count = len(request.rows) - len({_row_fingerprint(row) for row in request.rows})
        projection = {
            "source_revision_id": request.source_revision_id,
            "revision_digest": request.revision_digest,
            "row_count": len(request.rows),
            "duplicate_row_count": duplicate_count,
            "columns": [column.__dict__ for column in columns],
        }
        profile_digest = hashlib.sha256(
            json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        return TabularProfile(
            source_revision_id=request.source_revision_id,
            revision_digest=request.revision_digest,
            row_count=len(request.rows),
            duplicate_row_count=duplicate_count,
            columns=columns,
            profile_digest=profile_digest,
        )

    @staticmethod
    def confirm_mapping(
        profile: TabularProfile,
        mapping: Mapping[str, str],
        *,
        confirmed_by: str,
    ) -> MappingConfirmation:
        headers = {column.header for column in profile.columns}
        if (
            not confirmed_by
            or not mapping
            or not set(mapping).issubset(headers)
            or len(set(mapping.values())) != len(mapping)
            or any(not source or not target for source, target in mapping.items())
        ):
            raise BusinessControllingImportError("controlling_mapping_confirmation_invalid")
        normalized = tuple(sorted((str(source), str(target)) for source, target in mapping.items()))
        projection = {
            "profile_digest": profile.profile_digest,
            "column_mapping": normalized,
            "confirmed_by": confirmed_by,
        }
        confirmation_digest = hashlib.sha256(
            json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        return MappingConfirmation(profile.profile_digest, normalized, confirmed_by, confirmation_digest)

    @classmethod
    def _validate_request(cls, request: TabularProfileRequest) -> None:
        if request.source_format not in {"csv", "xlsx"}:
            raise BusinessControllingImportError("controlling_source_format_denied")
        if (
            not request.headers
            or len(request.headers) > cls.MAX_COLUMNS
            or len(set(request.headers)) != len(request.headers)
        ):
            raise BusinessControllingImportError("controlling_headers_invalid")
        if len(request.rows) > cls.MAX_ROWS or any(len(row) != len(request.headers) for row in request.rows):
            raise BusinessControllingImportError("controlling_rows_invalid")

    @staticmethod
    def _profile_column(header: str, values: Sequence[object]) -> ColumnProfile:
        non_null = tuple(value for value in values if value is not None and value != "")
        inferred_type = _infer_type(header, non_null)
        invalid_count = sum(not _valid_for_type(value, inferred_type) for value in non_null)
        return ColumnProfile(
            header=header,
            inferred_type=inferred_type,
            null_count=len(values) - len(non_null),
            invalid_count=invalid_count,
        )


def _infer_type(header: str, values: Sequence[object]) -> str:
    normalized = header.casefold()
    if "date" in normalized or "period" in normalized:
        return "date"
    if "amount" in normalized or "total" in normalized:
        return "decimal"
    if "currency" in normalized:
        return "currency"
    if values and all(isinstance(value, bool) for value in values):
        return "boolean"
    return "text"


def _valid_for_type(value: object, inferred_type: str) -> bool:
    if inferred_type == "decimal":
        try:
            Decimal(str(value))
        except (InvalidOperation, ValueError):
            return False
    elif inferred_type == "date":
        text = str(value)
        return len(text) == 10 and text[4] == "-" and text[7] == "-"
    elif inferred_type == "currency":
        return isinstance(value, str) and len(value) == 3 and value.isalpha() and value.isupper()
    return True


def _row_fingerprint(row: Sequence[object]) -> str:
    return hashlib.sha256(
        json.dumps(tuple(row), sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True).encode()
    ).hexdigest()


__all__ = [
    "BusinessControllingImportError",
    "BusinessControllingImportService",
    "ColumnProfile",
    "ControllingSourceAdmissionPort",
    "MappingConfirmation",
    "TabularProfile",
    "TabularProfileRequest",
    "WorkbookRiskMetadata",
]

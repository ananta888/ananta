"""Fail-closed archive and media inspection before office parsing."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath


class SpreadsheetArtifactRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SpreadsheetArtifactInspection:
    format: str
    media_type: str
    size_bytes: int
    archive_entries: int
    uncompressed_bytes: int
    unsupported_parts: tuple[str, ...] = ()


class SpreadsheetArtifactInspector:
    _MEDIA_TYPES = {
        "xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
        "ods": frozenset({"application/vnd.oasis.opendocument.spreadsheet"}),
        "csv": frozenset({"text/csv", "application/csv"}),
    }
    _FORBIDDEN_PARTS = (
        "/vbaproject.bin",
        "/embeddings/",
        "/externallinks/",
        "/connections.xml",
        "/scripts/",
        "/object ",
        "/objectreplacements/",
    )

    def __init__(
        self,
        *,
        max_compressed_bytes: int = 16 * 1024 * 1024,
        max_uncompressed_bytes: int = 128 * 1024 * 1024,
        max_archive_entries: int = 10_000,
        max_ratio: int = 100,
        max_csv_rows: int = 100_000,
        max_csv_columns: int = 1_024,
        max_csv_field_chars: int = 65_536,
    ) -> None:
        self.max_compressed_bytes = int(max_compressed_bytes)
        self.max_uncompressed_bytes = int(max_uncompressed_bytes)
        self.max_archive_entries = int(max_archive_entries)
        self.max_ratio = int(max_ratio)
        self.max_csv_rows = int(max_csv_rows)
        self.max_csv_columns = int(max_csv_columns)
        self.max_csv_field_chars = int(max_csv_field_chars)

    def inspect(self, *, filename: str, media_type: str, content: bytes) -> SpreadsheetArtifactInspection:
        if not isinstance(content, bytes) or not 1 <= len(content) <= self.max_compressed_bytes:
            raise SpreadsheetArtifactRejected("spreadsheet_upload_size_invalid")
        name = str(filename or "")
        if name != PurePosixPath(name).name or "\\" in name or "\x00" in name:
            raise SpreadsheetArtifactRejected("spreadsheet_filename_invalid")
        suffix = PurePosixPath(name).suffix.lower().lstrip(".")
        if suffix not in self._MEDIA_TYPES or media_type not in self._MEDIA_TYPES[suffix]:
            raise SpreadsheetArtifactRejected("spreadsheet_media_type_mismatch")
        if suffix == "csv":
            self._inspect_csv(content)
            return SpreadsheetArtifactInspection(suffix, media_type, len(content), 0, len(content), ())
        return self._inspect_archive(format=suffix, media_type=media_type, content=content)

    def _inspect_archive(
        self,
        *,
        format: str,
        media_type: str,
        content: bytes,
    ) -> SpreadsheetArtifactInspection:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if not 1 <= len(entries) <= self.max_archive_entries:
                    raise SpreadsheetArtifactRejected("spreadsheet_archive_entry_limit_exceeded")
                total = 0
                names = set()
                unsupported_parts: list[str] = []
                for entry in entries:
                    normalized = entry.filename.replace("\\", "/")
                    path = PurePosixPath(normalized)
                    if (
                        not normalized
                        or normalized.startswith("/")
                        or ".." in path.parts
                        or normalized in names
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise SpreadsheetArtifactRejected("spreadsheet_archive_path_invalid")
                    names.add(normalized)
                    lowered = f"/{normalized.lower()}"
                    if any(part in lowered for part in self._FORBIDDEN_PARTS):
                        raise SpreadsheetArtifactRejected("spreadsheet_active_content_forbidden")
                    if any(part in lowered for part in ("/drawings/", "/charts/", "/pivot")):
                        unsupported_parts.append(normalized)
                    total += int(entry.file_size)
                    if total > self.max_uncompressed_bytes:
                        raise SpreadsheetArtifactRejected("spreadsheet_archive_size_limit_exceeded")
                    if entry.file_size > self.max_ratio * max(1, entry.compress_size):
                        raise SpreadsheetArtifactRejected("spreadsheet_archive_ratio_limit_exceeded")
                required = "[Content_Types].xml" if format == "xlsx" else "mimetype"
                if required not in names:
                    raise SpreadsheetArtifactRejected("spreadsheet_archive_signature_invalid")
        except zipfile.BadZipFile as exc:
            raise SpreadsheetArtifactRejected("spreadsheet_archive_invalid") from exc
        return SpreadsheetArtifactInspection(
            format,
            media_type,
            len(content),
            len(entries),
            total,
            tuple(sorted(unsupported_parts)[:100]),
        )

    def _inspect_csv(self, content: bytes) -> None:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SpreadsheetArtifactRejected("spreadsheet_csv_encoding_invalid") from exc
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            for row_index, row in enumerate(reader, start=1):
                if row_index > self.max_csv_rows:
                    raise SpreadsheetArtifactRejected("spreadsheet_csv_row_limit_exceeded")
                if len(row) > self.max_csv_columns:
                    raise SpreadsheetArtifactRejected("spreadsheet_csv_column_limit_exceeded")
                if any(len(value) > self.max_csv_field_chars for value in row):
                    raise SpreadsheetArtifactRejected("spreadsheet_csv_field_limit_exceeded")
                if any(_csv_formula_candidate(value) for value in row):
                    raise SpreadsheetArtifactRejected("spreadsheet_csv_formula_injection_forbidden")
        except csv.Error as exc:
            raise SpreadsheetArtifactRejected("spreadsheet_csv_invalid") from exc


__all__ = [
    "SpreadsheetArtifactInspection",
    "SpreadsheetArtifactInspector",
    "SpreadsheetArtifactRejected",
]


def _csv_formula_candidate(value: str) -> bool:
    normalized = value.lstrip()
    if not normalized or normalized[0] not in "=+-@":
        return False
    if normalized[0] in "+-":
        try:
            float(normalized)
        except ValueError:
            return True
        return False
    return True

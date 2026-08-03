"""Content-free immutable bindings for Organization Source Catalog records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SOURCE_ID = re.compile(r"^SRC_[0-9]{4}$")
_RECORD_FILES = frozenset({"index.jsonl", "details.jsonl", "relations.jsonl"})
_PUBLICATION_FIELDS = frozenset(
    {
        "schema",
        "organization_id",
        "connection_id",
        "source_revision_id",
        "revision_digest",
        "source_manifest_digest",
        "admission_receipt_id",
        "admission_digest",
        "knowledge_index_id",
        "index_run_id",
        "index_source_scope",
        "index_manifest_digest",
        "policy_snapshot_digest",
        "active_generation",
        "query_count",
        "query_digests",
        "query_limit",
        "source_count",
        "record_bindings",
        "binding_digest",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "source_id",
        "record_file",
        "record_id",
        "path",
        "line_start",
        "line_end",
        "content_hash",
        "record_binding_digest",
    }
)


class OrganizationSourceCatalogBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class OrganizationSourceCatalogBindingService:
    """Build and verify a closed, content-free index/record binding."""

    @staticmethod
    def source_provenance_digest(
        *,
        organization_id: str,
        authority: Any,
        source_id: str,
        record_binding: Mapping[str, Any],
    ) -> str:
        """Bind a public SourceRef to its exact admitted index record."""

        return canonical_sha256(
            {
                "schema": "organization_source_catalog_provenance.v1",
                "source_id": source_id,
                "tenant_id": authority.tenant_id,
                "project_id": authority.project_id,
                "organization_id": organization_id,
                "scope": f"organization:{organization_id}",
                "connection_id": authority.connection_id,
                "source_revision_id": authority.source_revision_id,
                "source_version": authority.revision_digest,
                "source_manifest_digest": authority.source_manifest_digest,
                "admission_digest": authority.admission_digest,
                "knowledge_index_id": authority.knowledge_index_id,
                "index_run_id": authority.index_run_id,
                "index_manifest_digest": authority.index_manifest_digest,
                "active_generation": authority.active_generation,
                "record_file": record_binding.get("record_file"),
                "record_id": record_binding.get("record_id"),
                "path": record_binding.get("path"),
                "line_start": record_binding.get("line_start"),
                "line_end": record_binding.get("line_end"),
                "content_hash": record_binding.get("content_hash"),
            }
        )

    def build(
        self,
        *,
        organization_id: str,
        authority: Any,
        query_digests: Sequence[str],
        query_limit: int,
        record_bindings: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        lineage = {
            "organization_id": str(organization_id or "").strip(),
            "connection_id": str(authority.connection_id),
            "source_revision_id": str(authority.source_revision_id),
            "revision_digest": str(authority.revision_digest),
            "source_manifest_digest": str(authority.source_manifest_digest),
            "admission_receipt_id": str(authority.admission_receipt_id),
            "admission_digest": str(authority.admission_digest),
            "knowledge_index_id": str(authority.knowledge_index_id),
            "index_run_id": str(authority.index_run_id),
            "index_source_scope": str(authority.index_source_scope),
            "index_manifest_digest": str(authority.index_manifest_digest),
            "policy_snapshot_digest": str(authority.policy_snapshot_digest),
            "active_generation": int(authority.active_generation),
        }
        for raw in record_bindings:
            record = {
                "source_id": str(raw.get("source_id") or "").strip(),
                "record_file": str(raw.get("record_file") or "").strip(),
                "record_id": str(raw.get("record_id") or "").strip() or None,
                "path": str(raw.get("path") or "").strip() or None,
                "line_start": raw.get("line_start"),
                "line_end": raw.get("line_end"),
                "content_hash": str(raw.get("content_hash") or "").strip().lower(),
            }
            record["record_binding_digest"] = canonical_sha256(
                {**lineage, **record}
            )
            records.append(record)
        records.sort(key=lambda item: str(item["source_id"]))
        payload = {
            "schema": "organization_source_catalog_publication.v1",
            **lineage,
            "query_count": len(query_digests),
            "query_digests": sorted(str(value).lower() for value in query_digests),
            "query_limit": int(query_limit),
            "source_count": len(records),
            "record_bindings": records,
        }
        payload["binding_digest"] = canonical_sha256(payload)
        return self.validate(payload)

    def validate(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(raw or {})
        if set(payload) != _PUBLICATION_FIELDS:
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_fields_invalid"
            )
        if payload.get("schema") != "organization_source_catalog_publication.v1":
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_schema_invalid"
            )
        required_text = (
            "organization_id",
            "connection_id",
            "source_revision_id",
            "admission_receipt_id",
            "knowledge_index_id",
            "index_run_id",
            "index_source_scope",
        )
        if any(
            not self._is_canonical_text(payload.get(field))
            for field in required_text
        ):
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_binding_invalid"
            )
        for field in (
            "revision_digest",
            "source_manifest_digest",
            "admission_digest",
            "index_manifest_digest",
            "policy_snapshot_digest",
            "binding_digest",
        ):
            if not self._is_canonical_sha256(payload.get(field)):
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_publication_digest_invalid"
                )
        query_count = self._bounded_int(payload.get("query_count"), 1, 8)
        query_limit = self._bounded_int(payload.get("query_limit"), 1, 50)
        source_count = self._bounded_int(payload.get("source_count"), 1, 400)
        generation = self._bounded_int(payload.get("active_generation"), 1, 2**63 - 1)
        query_digests = payload.get("query_digests")
        bindings = payload.get("record_bindings")
        if (
            not isinstance(query_digests, list)
            or len(query_digests) != query_count
            or query_digests != sorted(set(query_digests))
            or any(not self._is_canonical_sha256(value) for value in query_digests)
            or not isinstance(bindings, list)
            or len(bindings) != source_count
        ):
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_counts_invalid"
            )
        normalized_records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        lineage = {
            field: payload[field]
            for field in (
                "organization_id",
                "connection_id",
                "source_revision_id",
                "revision_digest",
                "source_manifest_digest",
                "admission_receipt_id",
                "admission_digest",
                "knowledge_index_id",
                "index_run_id",
                "index_source_scope",
                "index_manifest_digest",
                "policy_snapshot_digest",
                "active_generation",
            )
        }
        for raw_record in bindings:
            if not isinstance(raw_record, Mapping) or set(raw_record) != _RECORD_FIELDS:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_binding_fields_invalid"
                )
            record = dict(raw_record)
            source_id = str(record.get("source_id") or "")
            if _SOURCE_ID.fullmatch(source_id) is None or source_id in seen_ids:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_source_id_invalid"
                )
            seen_ids.add(source_id)
            if str(record.get("record_file") or "") not in _RECORD_FILES:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_file_invalid"
                )
            record_id = record.get("record_id")
            path = record.get("path")
            if (
                record_id is not None
                and not self._is_canonical_text(record_id, maximum=1024)
            ) or (
                path is not None
                and not self._is_canonical_text(path, maximum=4096)
            ):
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_locator_invalid"
                )
            if record_id is None and path is None:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_locator_invalid"
                )
            if not self._is_canonical_sha256(
                record.get("content_hash")
            ) or not self._is_canonical_sha256(
                record.get("record_binding_digest")
            ):
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_publication_digest_invalid"
                )
            start = self._optional_line(record.get("line_start"))
            end = self._optional_line(record.get("line_end"))
            if start is not None and end is not None and end < start:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_line_invalid"
                )
            unsigned = {
                field: record[field]
                for field in _RECORD_FIELDS
                if field != "record_binding_digest"
            }
            if canonical_sha256({**lineage, **unsigned}) != record["record_binding_digest"]:
                raise OrganizationSourceCatalogBindingError(
                    "organization_source_catalog_record_binding_mismatch"
                )
            normalized_records.append(record)
        expected_ids = [
            f"SRC_{ordinal:04d}" for ordinal in range(1, source_count + 1)
        ]
        if [str(record["source_id"]) for record in normalized_records] != expected_ids:
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_record_source_id_invalid"
            )
        if normalized_records != sorted(
            normalized_records, key=lambda item: str(item["source_id"])
        ):
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_record_order_invalid"
            )
        unsigned_publication = {
            field: payload[field]
            for field in _PUBLICATION_FIELDS
            if field != "binding_digest"
        }
        if canonical_sha256(unsigned_publication) != payload["binding_digest"]:
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_digest_mismatch"
            )
        payload.update(
            {
                "query_count": query_count,
                "query_limit": query_limit,
                "source_count": source_count,
                "active_generation": generation,
                "record_bindings": normalized_records,
            }
        )
        return payload

    @staticmethod
    def _is_sha256(value: object) -> bool:
        normalized = str(value or "").strip().lower()
        return len(normalized) == 64 and all(
            character in "0123456789abcdef" for character in normalized
        )

    @classmethod
    def _is_canonical_sha256(cls, value: object) -> bool:
        return isinstance(value, str) and value == value.lower() and cls._is_sha256(value)

    @staticmethod
    def _is_canonical_text(value: object, *, maximum: int = 4096) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and value == value.strip()
            and len(value) <= maximum
            and "\x00" not in value
        )

    @staticmethod
    def _bounded_int(value: object, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_integer_invalid"
            )
        normalized = value
        if not minimum <= normalized <= maximum:
            raise OrganizationSourceCatalogBindingError(
                "organization_source_catalog_publication_integer_invalid"
            )
        return normalized

    @staticmethod
    def _optional_line(value: object) -> int | None:
        if value is None:
            return None
        return OrganizationSourceCatalogBindingService._bounded_int(
            value, 1, 2**31 - 1
        )


__all__ = [
    "OrganizationSourceCatalogBindingError",
    "OrganizationSourceCatalogBindingService",
    "canonical_sha256",
]

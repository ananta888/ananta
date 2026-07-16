"""Bounded, redacted and cursor-stable dataset preview projection."""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.ml_intern_dataset_catalog_service import DatasetCatalogError


class DatasetPreviewError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DatasetPreviewCatalogPort(Protocol):
    def get_dataset(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> dict[str, Any]: ...

    def partition_descriptor(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
    ) -> dict[str, Any]: ...

    def open_partition(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
    ) -> Any: ...


@dataclass(frozen=True)
class DatasetPreviewPolicy:
    default_page_size: int = 25
    max_page_size: int = 100
    max_text_chars: int = 500
    max_fields: int = 32
    max_list_items: int = 32
    max_depth: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.default_page_size <= self.max_page_size <= 500:
            raise ValueError("dataset preview page limits are invalid")
        if not 32 <= self.max_text_chars <= 10_000:
            raise ValueError("dataset preview text limit is invalid")
        if min(self.max_fields, self.max_list_items, self.max_depth) <= 0:
            raise ValueError("dataset preview structural limits must be positive")


_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key|source[_-]?path|absolute[_-]?path)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:api[-_]?key|secret|token|password|credential)\s*[:=]\s*[^\s,;}]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN(?: RSA)? PRIVATE KEY-----"),
)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)
_LOCAL_PATH = re.compile(r"(?:(?:/home|/root|/tmp|/var|/etc)/[^\s\"']+|[A-Za-z]:\\[^\s\"']+)")


class MlInternDatasetPreviewService:
    """Projects one bounded page without loading the complete JSONL dataset."""

    def __init__(
        self,
        catalog: DatasetPreviewCatalogPort,
        *,
        policy: DatasetPreviewPolicy | None = None,
    ) -> None:
        self._catalog = catalog
        self._policy = policy or DatasetPreviewPolicy()

    def get_page(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        page_size = self._page_size(limit)
        try:
            descriptor = self._catalog.partition_descriptor(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
                partition=partition,
            )
        except DatasetCatalogError as exc:
            raise DatasetPreviewError(exc.reason_code, str(exc)) from exc
        offset = self._decode_cursor(
            cursor,
            dataset_id=dataset_id,
            partition=partition,
            sha256=str(descriptor.get("sha256") or ""),
        )
        records: list[dict[str, Any]] = []
        malformed = 0
        has_more = False
        next_offset = offset
        try:
            with self._catalog.open_partition(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
                partition=partition,
            ) as handle:
                logical_index = 0
                for line_number, raw_line in enumerate(handle, start=1):
                    if not raw_line.strip():
                        continue
                    if logical_index < offset:
                        logical_index += 1
                        continue
                    if len(records) >= page_size:
                        has_more = True
                        break
                    logical_index += 1
                    next_offset = logical_index
                    try:
                        record = json.loads(raw_line)
                    except json.JSONDecodeError:
                        malformed += 1
                        records.append({"record_index": logical_index - 1, "state": "malformed_record"})
                        continue
                    if not isinstance(record, dict):
                        malformed += 1
                        records.append({"record_index": logical_index - 1, "state": "non_object_record"})
                        continue
                    records.append(
                        {
                            "record_index": logical_index - 1,
                            "state": "ready",
                            "record": self._redact(record, depth=0),
                        }
                    )
        except DatasetCatalogError as exc:
            raise DatasetPreviewError(exc.reason_code, str(exc)) from exc

        validation_status = str(descriptor.get("validation_status") or "pending")
        if malformed:
            state = "malformed"
        elif not records and int(descriptor.get("record_count") or 0) == 0:
            state = "empty"
        elif validation_status != "passed":
            state = "not_validated"
        else:
            state = "ready"
        next_cursor = (
            self._encode_cursor(
                dataset_id=dataset_id,
                partition=partition,
                sha256=str(descriptor.get("sha256") or ""),
                offset=next_offset,
            )
            if has_more
            else None
        )
        return {
            "schema": "mlintern_dataset_record_page.v1",
            "dataset_id": dataset_id,
            "partition": partition,
            "state": state,
            "validation_status": validation_status,
            "offset": offset,
            "limit": page_size,
            "returned_count": len(records),
            "malformed_count": malformed,
            "records": records,
            "next_cursor": next_cursor,
        }

    def get_statistics(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
    ) -> dict[str, Any]:
        try:
            summary = self._catalog.get_dataset(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
            )
        except DatasetCatalogError as exc:
            raise DatasetPreviewError(exc.reason_code, str(exc)) from exc
        distribution = {"instruction": 0, "chat": 0, "unknown": 0, "malformed": 0}
        try:
            with self._catalog.open_partition(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
                partition="train",
            ) as handle:
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    try:
                        record = json.loads(raw_line)
                    except json.JSONDecodeError:
                        distribution["malformed"] += 1
                        continue
                    if isinstance(record, dict) and "messages" in record:
                        distribution["chat"] += 1
                    elif isinstance(record, dict) and "instruction" in record:
                        distribution["instruction"] += 1
                    else:
                        distribution["unknown"] += 1
        except DatasetCatalogError as exc:
            raise DatasetPreviewError(exc.reason_code, str(exc)) from exc
        validation = dict(summary.get("validation") or {})
        return {
            "schema": "mlintern_dataset_statistics.v1",
            "dataset_id": dataset_id,
            "state": "empty" if int(summary.get("record_count") or 0) == 0 else (
                "ready" if validation.get("status") == "passed" else "not_validated"
            ),
            "record_count": int(summary.get("record_count") or 0),
            "input_record_count": int(summary.get("input_record_count") or 0),
            "rejected_record_count": int(summary.get("rejected_record_count") or 0),
            "duplicate_count": int(summary.get("duplicate_count") or 0),
            "format_distribution": distribution,
            "split_sizes": {
                name: int((entry or {}).get("record_count") or 0)
                for name, entry in (summary.get("partitions") or {}).items()
            },
            "validation": validation,
        }

    def _page_size(self, value: int | None) -> int:
        if value is None:
            return self._policy.default_page_size
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise DatasetPreviewError("invalid_page_limit", "preview page limit must be an integer") from exc
        if not 1 <= parsed <= self._policy.max_page_size:
            raise DatasetPreviewError("invalid_page_limit", "preview page limit is out of range")
        return parsed

    def _redact(self, value: Any, *, depth: int, key: str = "") -> Any:
        if _SENSITIVE_KEY.search(key):
            return "[REDACTED]"
        if depth >= self._policy.max_depth:
            return "[TRUNCATED_DEPTH]"
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for index, (child_key, child_value) in enumerate(value.items()):
                if index >= self._policy.max_fields:
                    output["_truncated_fields"] = len(value) - self._policy.max_fields
                    break
                normalized_key = str(child_key)[:128]
                output[normalized_key] = self._redact(child_value, depth=depth + 1, key=normalized_key)
            return output
        if isinstance(value, list):
            output = [self._redact(item, depth=depth + 1, key=key) for item in value[: self._policy.max_list_items]]
            if len(value) > self._policy.max_list_items:
                output.append(f"[TRUNCATED_ITEMS:{len(value) - self._policy.max_list_items}]")
            return output
        if isinstance(value, str):
            text = value
            for pattern in _SECRET_VALUE_PATTERNS:
                text = pattern.sub("[REDACTED]", text)
            text = _EMAIL.sub("[REDACTED_EMAIL]", text)
            text = _LOCAL_PATH.sub("[REDACTED_PATH]", text)
            if len(text) > self._policy.max_text_chars:
                text = text[: self._policy.max_text_chars] + "…"
            return text
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else "[INVALID_NUMBER]"
        return str(value)[: self._policy.max_text_chars]

    @staticmethod
    def _encode_cursor(*, dataset_id: str, partition: str, sha256: str, offset: int) -> str:
        payload = json.dumps(
            {"v": 1, "dataset_id": dataset_id, "partition": partition, "sha256": sha256, "offset": offset},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str | None,
        *,
        dataset_id: str,
        partition: str,
        sha256: str,
    ) -> int:
        if not cursor:
            return 0
        raw = str(cursor)
        if len(raw) > 1024:
            raise DatasetPreviewError("invalid_cursor", "preview cursor is invalid")
        try:
            padding = "=" * (-len(raw) % 4)
            payload = json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise DatasetPreviewError("invalid_cursor", "preview cursor is invalid") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise DatasetPreviewError("invalid_cursor", "preview cursor version is invalid")
        if payload.get("dataset_id") != dataset_id or payload.get("partition") != partition:
            raise DatasetPreviewError("cursor_scope_mismatch", "preview cursor belongs to a different resource")
        if payload.get("sha256") != sha256:
            raise DatasetPreviewError("stale_cursor", "dataset changed after the preview cursor was issued")
        offset = payload.get("offset")
        if not isinstance(offset, int) or offset < 0:
            raise DatasetPreviewError("invalid_cursor", "preview cursor offset is invalid")
        return offset

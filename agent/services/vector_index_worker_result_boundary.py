"""Security boundary for untrusted vector-index Worker results."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VECTOR_INDEX_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "attempt_id",
        "idempotency_key",
        "operation",
        "status",
        "reason_code",
        "diagnostics",
        "result",
        "error",
    }
)
VECTOR_INDEX_RESULT_REASON_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_VECTOR_INDEX_WORKER_RESULT_BYTES = 65_536

_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bauthorization\s*[:=]", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE),
    re.compile(r"\bbasic\s+[A-Za-z0-9+/=]{8,}", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|pwd|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|secret)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "auth_token",
        "bearer_token",
        "credential",
        "credentials",
        "password",
        "passwd",
        "pwd",
        "refresh_token",
        "secret",
        "token",
    }
)
_DOCUMENT_CONTENT_KEYS = frozenset(
    {
        "body",
        "chunk",
        "chunk_text",
        "chunks",
        "completion",
        "content",
        "document",
        "document_content",
        "document_text",
        "documents",
        "embedding",
        "embedding_text",
        "embeddings",
        "output",
        "page_content",
        "prompt",
        "raw_text",
        "source_text",
        "stderr",
        "stdout",
        "text",
        "vector",
        "vectors",
    }
)
_VERIFICATION_KEYS = frozenset(
    {
        "vector_index_previous_attempt",
        "vector_index_task_result",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class VectorIndexWorkerResultLimits:
    """Resource limits for untrusted Worker results persisted by the Hub."""

    max_depth: int = 8
    max_mapping_entries: int = 256
    max_list_items: int = 256
    max_key_bytes: int = 128
    max_string_bytes: int = 2048
    max_total_bytes: int = MAX_VECTOR_INDEX_WORKER_RESULT_BYTES


@dataclass(slots=True)
class _VectorIndexWorkerResultBudget:
    mapping_entries: int = 0
    list_items: int = 0


class VectorIndexWorkerResultBoundary:
    """Normalize one Worker result without retaining secrets or document text."""

    def __init__(
        self,
        limits: VectorIndexWorkerResultLimits | None = None,
    ) -> None:
        self._limits = limits or VectorIndexWorkerResultLimits()

    def normalize_result(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_document(
            raw,
            root_allowed_content_keys=VECTOR_INDEX_RESULT_FIELDS,
            recognize_result_wrappers=False,
            reject_document_content=True,
        )
        if set(normalized) != VECTOR_INDEX_RESULT_FIELDS:
            raise ValueError("vector_index_result_fields_invalid")
        return normalized

    def normalize_verification(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._normalize_document(
            raw,
            root_allowed_content_keys=frozenset(),
            recognize_result_wrappers=True,
            reject_document_content=True,
        )

    def normalize_status_values(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._normalize_document(
            raw,
            root_allowed_content_keys=frozenset(),
            recognize_result_wrappers=False,
            reject_document_content=False,
        )

    def _normalize_document(
        self,
        raw: Mapping[str, Any],
        *,
        root_allowed_content_keys: frozenset[str],
        recognize_result_wrappers: bool,
        reject_document_content: bool,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("vector_index_result_mapping_invalid")
        normalized = self._normalize_value(
            raw,
            depth=0,
            budget=_VectorIndexWorkerResultBudget(),
            root_allowed_content_keys=root_allowed_content_keys,
            recognize_result_wrappers=recognize_result_wrappers,
            reject_document_content=reject_document_content,
        )
        if not isinstance(normalized, dict):
            raise ValueError("vector_index_result_mapping_invalid")
        if len(_canonical_json(normalized)) > self._limits.max_total_bytes:
            raise ValueError("vector_index_result_size_limit_exceeded")
        return normalized

    def _normalize_value(
        self,
        value: Any,
        *,
        depth: int,
        budget: _VectorIndexWorkerResultBudget,
        root_allowed_content_keys: frozenset[str],
        recognize_result_wrappers: bool,
        reject_document_content: bool,
    ) -> Any:
        if depth > self._limits.max_depth:
            raise ValueError("vector_index_result_depth_limit_exceeded")
        if isinstance(value, Mapping):
            return self._normalize_mapping(
                value,
                depth=depth,
                budget=budget,
                root_allowed_content_keys=root_allowed_content_keys,
                recognize_result_wrappers=recognize_result_wrappers,
                reject_document_content=reject_document_content,
            )
        if isinstance(value, (list, tuple)):
            budget.list_items += len(value)
            if budget.list_items > self._limits.max_list_items:
                raise ValueError("vector_index_result_list_limit_exceeded")
            return [
                self._normalize_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    root_allowed_content_keys=frozenset(),
                    recognize_result_wrappers=False,
                    reject_document_content=reject_document_content,
                )
                for item in value
            ]
        if isinstance(value, str):
            if len(value.encode("utf-8")) > self._limits.max_string_bytes:
                raise ValueError("vector_index_result_string_limit_exceeded")
            if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
                raise ValueError("vector_index_result_sensitive_value_forbidden")
            return value
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            if not -(2**63) <= value <= (2**63 - 1):
                raise ValueError("vector_index_result_number_invalid")
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("vector_index_result_number_invalid")
            return value
        raise ValueError("vector_index_result_value_invalid")

    def _normalize_mapping(
        self,
        value: Mapping[str, Any],
        *,
        depth: int,
        budget: _VectorIndexWorkerResultBudget,
        root_allowed_content_keys: frozenset[str],
        recognize_result_wrappers: bool,
        reject_document_content: bool,
    ) -> dict[str, Any]:
        budget.mapping_entries += len(value)
        if budget.mapping_entries > self._limits.max_mapping_entries:
            raise ValueError("vector_index_result_entry_limit_exceeded")
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("vector_index_result_key_invalid")
            if len(raw_key.encode("utf-8")) > self._limits.max_key_bytes:
                raise ValueError("vector_index_result_key_limit_exceeded")
            key = re.sub(r"[^a-z0-9]+", "_", raw_key.lower()).strip("_")
            if self._is_sensitive_key(key):
                raise ValueError("vector_index_result_sensitive_key_forbidden")
            if (
                reject_document_content
                and key in _DOCUMENT_CONTENT_KEYS
                and not (depth == 0 and raw_key in root_allowed_content_keys)
            ):
                raise ValueError("vector_index_result_document_content_forbidden")
            if recognize_result_wrappers and raw_key in _VERIFICATION_KEYS:
                if not isinstance(item, Mapping):
                    raise ValueError("vector_index_result_verification_invalid")
                item = self.normalize_result(item)
                child_rejects_document_content = False
            else:
                child_rejects_document_content = reject_document_content
            normalized[raw_key] = self._normalize_value(
                item,
                depth=depth + 1,
                budget=budget,
                root_allowed_content_keys=frozenset(),
                recognize_result_wrappers=False,
                reject_document_content=child_rejects_document_content,
            )
        return normalized

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        if key in _SENSITIVE_KEYS:
            return True
        parts = frozenset(part for part in key.split("_") if part)
        if parts & {
            "authorization",
            "credential",
            "credentials",
            "password",
            "passwd",
            "pwd",
            "secret",
        }:
            return True
        return key.endswith(("_api_key", "_auth_token", "_bearer_token"))


__all__ = [
    "VECTOR_INDEX_RESULT_FIELDS",
    "VECTOR_INDEX_RESULT_REASON_CODE",
    "VectorIndexWorkerResultBoundary",
    "VectorIndexWorkerResultLimits",
]

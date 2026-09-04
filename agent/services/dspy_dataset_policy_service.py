"""Hub-owned admission checks for immutable optimization datasets."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dspy_optimization import DatasetManifestV1, canonical_digest

_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer|api[_-]?key\s*[=:]|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
_PII = re.compile(r"(?i)(?:\b\d{3}-\d{2}-\d{4}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)")


class DspyDatasetPolicyService:
    def __init__(
        self,
        *,
        allowed_source_refs: frozenset[str],
        allowed_license_ids: frozenset[str] = frozenset({"MIT", "internal"}),
        max_record_bytes: int = 64_000,
        max_total_bytes: int = 32_000_000,
        max_depth: int = 12,
    ) -> None:
        if not 1_024 <= max_record_bytes <= 1_000_000 or not max_record_bytes <= max_total_bytes <= 100_000_000:
            raise ValueError("dspy_dataset_size_policy_invalid")
        if not 2 <= max_depth <= 24:
            raise ValueError("dspy_dataset_depth_policy_invalid")
        self._allowed_sources = allowed_source_refs
        self._allowed_licenses = allowed_license_ids
        self._max_record_bytes = max_record_bytes
        self._max_total_bytes = max_total_bytes
        self._max_depth = max_depth

    def admit(self, manifest: DatasetManifestV1, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        reasons: list[str] = []
        if manifest.license_id not in self._allowed_licenses:
            reasons.append("dspy_dataset_license_denied")
        if not set(manifest.source_refs).issubset(self._allowed_sources):
            reasons.append("dspy_dataset_source_unverified")
        record_ids = {str(value) for split in manifest.split_record_ids.values() for value in split}
        actual_ids: set[str] = set()
        total_bytes = 0
        normalized: list[dict[str, Any]] = []
        for raw in records:
            record = dict(raw)
            record_id = str(record.get("record_id") or "")
            if not record_id or record_id in actual_ids:
                reasons.append("dspy_dataset_record_id_invalid")
            actual_ids.add(record_id)
            _bounded_json(record, max_depth=self._max_depth)
            rendered = _render(record)
            size = len(rendered.encode())
            total_bytes += size
            if size > self._max_record_bytes:
                reasons.append("dspy_dataset_record_too_large")
            if _SECRET.search(rendered):
                reasons.append("dspy_dataset_secret_detected")
            if manifest.sensitivity in {"public", "internal"} and _PII.search(rendered):
                reasons.append("dspy_dataset_pii_classification_invalid")
            normalized.append(record)
        if record_ids != actual_ids:
            reasons.append("dspy_dataset_split_membership_mismatch")
        if total_bytes > self._max_total_bytes:
            reasons.append("dspy_dataset_total_size_exceeded")
        if canonical_digest(normalized) != manifest.content_digest:
            reasons.append("dspy_dataset_content_digest_mismatch")
        reasons = sorted(set(reasons))
        return {
            "admitted": not reasons,
            "reason_codes": reasons,
            "manifest_digest": manifest.digest,
            "record_count": len(records),
            "total_bytes": total_bytes,
            "external_download_performed": False,
            "human_intervention_required": False,
        }


def _bounded_json(value: Any, *, max_depth: int, depth: int = 0) -> None:
    if depth > max_depth:
        raise ValueError("dspy_dataset_depth_exceeded")
    if isinstance(value, Mapping):
        if len(value) > 128 or any(len(str(key)) > 256 for key in value):
            raise ValueError("dspy_dataset_shape_invalid")
        for item in value.values():
            _bounded_json(item, max_depth=max_depth, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > 10_000:
            raise ValueError("dspy_dataset_shape_invalid")
        for item in value:
            _bounded_json(item, max_depth=max_depth, depth=depth + 1)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("dspy_dataset_non_json_value")


def _render(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


__all__ = ["DspyDatasetPolicyService"]

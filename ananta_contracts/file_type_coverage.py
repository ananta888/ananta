"""Deterministic per-file coverage evidence for CodeCompass index runs."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from .file_type_support import FileTypeDescriptor, FileTypeSupportRegistry


class CoverageOutcome(str, Enum):
    INDEXED = "indexed"
    EXCLUDED = "excluded"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FileCoverageRecord:
    path: str
    detected_type: str
    support_level: str
    parser_strategy: str
    indexed: bool
    outcome: CoverageOutcome
    exclusion_reason: str | None
    diagnostics: tuple[str, ...]
    byte_size: int
    duration_seconds: float = 0.0
    symbol_count: int = 0
    edge_count: int = 0
    fallback_reason: str | None = None
    content_sha256: str | None = None
    extractor_id: str | None = None
    extractor_version: str | None = None

    def __post_init__(self) -> None:
        normalized = str(self.path or "").replace("\\", "/").strip()
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"coverage_path_must_be_repository_relative:{self.path}")
        if self.byte_size < 0:
            raise ValueError("coverage_byte_size_must_be_non_negative")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("coverage_duration_must_be_finite_and_non_negative")
        if self.symbol_count < 0 or self.edge_count < 0:
            raise ValueError("coverage_record_counts_must_be_non_negative")
        if self.content_sha256 is not None and (
            len(self.content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.content_sha256)
        ):
            raise ValueError("coverage_content_sha256_invalid")
        if self.indexed != (self.outcome is CoverageOutcome.INDEXED):
            raise ValueError("coverage_indexed_outcome_mismatch")
        if self.outcome is CoverageOutcome.EXCLUDED and not self.exclusion_reason:
            raise ValueError("coverage_exclusion_reason_required")
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "diagnostics", tuple(sorted(set(self.diagnostics))))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "detected_type": self.detected_type,
            "support_level": self.support_level,
            "parser_strategy": self.parser_strategy,
            "indexed": self.indexed,
            "outcome": self.outcome.value,
            "exclusion_reason": self.exclusion_reason,
            "diagnostics": list(self.diagnostics),
            "byte_size": self.byte_size,
            "duration_seconds": self.duration_seconds,
            "symbol_count": self.symbol_count,
            "edge_count": self.edge_count,
            "fallback_reason": self.fallback_reason,
            "content_sha256": self.content_sha256,
            "extractor_id": self.extractor_id,
            "extractor_version": self.extractor_version,
        }


class FileTypeCoverageReport:
    """Accumulates observable outcomes without deciding access or orchestration."""

    def __init__(
        self,
        registry: FileTypeSupportRegistry,
        *,
        pipeline: str,
        runtime_availability: Mapping[str, bool] | None = None,
    ) -> None:
        if pipeline not in registry.pipelines:
            raise ValueError(f"unknown_coverage_pipeline:{pipeline}")
        self.registry = registry
        self.pipeline = pipeline
        self.runtime_availability = dict(runtime_availability or {})
        matrix = registry.support_matrix(runtime_availability=self.runtime_availability)
        self._matrix_by_type = {
            str(row["format_id"]): row
            for row in matrix["rows"]
            if row["pipeline"] == pipeline
        }
        self._records: dict[str, FileCoverageRecord] = {}

    def add(
        self,
        *,
        path: str,
        descriptor: FileTypeDescriptor | None,
        outcome: CoverageOutcome | str,
        byte_size: int,
        exclusion_reason: str | None = None,
        diagnostics: Iterable[str] = (),
        detected_type: str | None = None,
        parser_strategy: str | None = None,
        duration_seconds: float = 0.0,
        symbol_count: int = 0,
        edge_count: int = 0,
        fallback_reason: str | None = None,
        content_sha256: str | None = None,
        extractor_id: str | None = None,
        extractor_version: str | None = None,
    ) -> FileCoverageRecord:
        normalized_outcome = CoverageOutcome(str(getattr(outcome, "value", outcome)))
        format_id = str(detected_type or (descriptor.format_id if descriptor else "unknown_text"))
        record = FileCoverageRecord(
            path=path,
            detected_type=format_id,
            support_level=self._support_level(format_id, descriptor),
            parser_strategy=str(
                parser_strategy
                or (descriptor.parser_strategy if descriptor else "unknown_text_probe")
            ),
            indexed=normalized_outcome is CoverageOutcome.INDEXED,
            outcome=normalized_outcome,
            exclusion_reason=str(exclusion_reason).strip() if exclusion_reason else None,
            diagnostics=tuple(str(item).strip() for item in diagnostics if str(item).strip()),
            byte_size=max(0, int(byte_size)),
            duration_seconds=float(duration_seconds),
            symbol_count=int(symbol_count),
            edge_count=int(edge_count),
            fallback_reason=(str(fallback_reason).strip() or None) if fallback_reason else None,
            content_sha256=(str(content_sha256).strip().lower() or None) if content_sha256 else None,
            extractor_id=(str(extractor_id).strip() or None) if extractor_id else None,
            extractor_version=(str(extractor_version).strip() or None) if extractor_version else None,
        )
        if record.path in self._records:
            raise ValueError(f"duplicate_file_coverage_record:{record.path}")
        self._records[record.path] = record
        return record

    @property
    def records(self) -> tuple[FileCoverageRecord, ...]:
        return tuple(self._records[path] for path in sorted(self._records))

    def as_dict(self) -> dict[str, Any]:
        records = self.records
        total = len(records)
        outcome_counts = Counter(record.outcome.value for record in records)
        type_counts = Counter(record.detected_type for record in records)
        support_counts = Counter(record.support_level for record in records)
        parser_counts = Counter(record.parser_strategy for record in records)
        exclusion_counts = Counter(
            record.exclusion_reason for record in records if record.exclusion_reason
        )
        diagnostic_counts = Counter(
            code for record in records for code in record.diagnostics
        )
        fallback_counts = Counter(
            record.fallback_reason for record in records if record.fallback_reason
        )
        return {
            "schema": "codecompass.file-type-coverage.v1",
            "registry_version": self.registry.registry_version,
            "registry_digest": self.registry.digest,
            "pipeline": self.pipeline,
            "files": [record.as_dict() for record in records],
            "aggregate": {
                "file_count": total,
                "byte_size": sum(record.byte_size for record in records),
                "duration_seconds": round(sum(record.duration_seconds for record in records), 6),
                "symbol_count": sum(record.symbol_count for record in records),
                "edge_count": sum(record.edge_count for record in records),
                "indexed_count": outcome_counts[CoverageOutcome.INDEXED.value],
                "indexed_share": _share(outcome_counts[CoverageOutcome.INDEXED.value], total),
                "by_outcome": _counter_payload(outcome_counts, total),
                "by_detected_type": _counter_payload(type_counts, total),
                "by_support_level": _counter_payload(support_counts, total),
                "by_parser": _counter_payload(parser_counts, total),
                "by_exclusion_reason": _counter_payload(exclusion_counts, total),
                "by_diagnostic": _counter_payload(diagnostic_counts, total),
                "by_fallback": _counter_payload(fallback_counts, total),
                "unknown_text_count": type_counts["unknown_text"],
            },
        }

    def manifest_coverage(self, *, truncated: bool = False) -> dict[str, Any]:
        counts = Counter(record.outcome.value for record in self.records)
        diagnostics = Counter(code for record in self.records for code in record.diagnostics)
        return {
            "manifest_candidate_count": len(self.records),
            "indexed": counts[CoverageOutcome.INDEXED.value],
            "excluded": counts[CoverageOutcome.EXCLUDED.value],
            "unsupported": counts[CoverageOutcome.UNSUPPORTED.value],
            "failed": counts[CoverageOutcome.FAILED.value],
            "truncated": bool(truncated),
            "diagnostic_counts": dict(sorted(diagnostics.items())),
        }

    def metrics_snapshot(self) -> list[dict[str, Any]]:
        """Return bounded aggregates suitable for cross-container metric ingestion."""

        by_type: dict[str, list[FileCoverageRecord]] = {}
        for record in self.records:
            by_type.setdefault(record.detected_type, []).append(record)
        snapshot = []
        for format_id, records in sorted(by_type.items()):
            outcomes = Counter(record.outcome.value for record in records)
            diagnostics = Counter(code for record in records for code in record.diagnostics)
            fallbacks = Counter(
                record.fallback_reason for record in records if record.fallback_reason
            )
            durations = {
                outcome: round(
                    sum(
                        record.duration_seconds
                        for record in records
                        if record.outcome.value == outcome
                    ),
                    6,
                )
                for outcome in sorted(outcomes)
            }
            snapshot.append(
                {
                    "format_id": format_id,
                    "file_count": len(records),
                    "byte_size": sum(record.byte_size for record in records),
                    "outcomes": dict(sorted(outcomes.items())),
                    "diagnostics": dict(sorted(diagnostics.items())),
                    "fallbacks": dict(sorted(fallbacks.items())),
                    "duration_seconds_by_outcome": durations,
                    "symbol_count": sum(record.symbol_count for record in records),
                    "edge_count": sum(record.edge_count for record in records),
                }
            )
        return snapshot

    def snapshot_manifest(
        self,
        *,
        required_path_rules: Iterable["RequiredPathRule | Mapping[str, Any] | str"] = (),
        profile: Mapping[str, Any] | None = None,
        source_revision: str | None = None,
    ) -> dict[str, Any]:
        """Build the immutable content projection for one repository snapshot.

        Operational fields such as duration, timestamps, absolute output paths and
        mtimes are deliberately excluded.  As a result, equal repository bytes and
        classification policy produce the same ``snapshot_revision`` in every
        container, while a content, registry, extractor or profile change invalidates
        the revision.
        """

        normalized_profile = _canonical_mapping(profile or {})
        profile_digest = _stable_digest(normalized_profile)
        files = [
            {
                "path": record.path,
                "content_sha256": record.content_sha256,
                "content_state": "hashed" if record.content_sha256 else "not_read_by_policy",
                "byte_size": record.byte_size,
                "detected_type": record.detected_type,
                "support_level": record.support_level,
                "parser_strategy": record.parser_strategy,
                "extractor_id": record.extractor_id,
                "extractor_version": record.extractor_version,
                "outcome": record.outcome.value,
                "exclusion_reason": record.exclusion_reason,
                "diagnostics": list(record.diagnostics),
                "fallback_reason": record.fallback_reason,
            }
            for record in self.records
        ]
        normalized_rules = tuple(RequiredPathRule.from_value(value) for value in required_path_rules)
        required_gate = evaluate_required_paths(files=files, rules=normalized_rules)
        outcome_counts = Counter(record.outcome.value for record in self.records)
        cap_truncated = sum(
            record.exclusion_reason == "max_files_fair_share" for record in self.records
        )
        oversized = sum(
            record.exclusion_reason == "file_size_limit" for record in self.records
        )
        budget_exceeded = sum(
            record.exclusion_reason not in {None, "file_size_limit", "max_files_fair_share"}
            and (
                "limit" in str(record.exclusion_reason)
                or any("limit" in code for code in record.diagnostics)
            )
            for record in self.records
        )
        budget_visibility = {
            "inventory_count": len(files),
            "accounted_count": sum(outcome_counts.values()),
            "cap_truncated": cap_truncated,
            "oversized": oversized,
            "budget_exceeded": budget_exceeded,
            "by_outcome": dict(sorted(outcome_counts.items())),
        }
        projection = {
            "registry_version": self.registry.registry_version,
            "registry_digest": self.registry.digest,
            "pipeline": self.pipeline,
            "profile": normalized_profile,
            "profile_digest": profile_digest,
            "files": files,
            "required_paths": required_gate,
            "budget_visibility": budget_visibility,
            "silently_skipped": None,
        }
        return {
            "schema": "codecompass.snapshot_manifest.v1",
            "snapshot_revision": _stable_digest(projection),
            "source_revision": str(source_revision or "").strip() or None,
            **projection,
        }

    def _support_level(self, format_id: str, descriptor: FileTypeDescriptor | None) -> str:
        row = self._matrix_by_type.get(format_id)
        if not row:
            return "discovery"
        capabilities = row["capabilities"]
        if (
            descriptor is not None
            and descriptor.family != "code"
            and self.pipeline == "rag_helper"
            and (
                capabilities["symbols"]["effective"]
                or capabilities["relationships"]["effective"]
                or (
                    capabilities["indexed"]["effective"]
                    and capabilities["indexed"]["implementation"] == "parser"
                )
            )
        ):
            return "domain_parser"
        if capabilities["relationships"]["effective"]:
            return "semantic_graph"
        if capabilities["symbols"]["effective"]:
            return "symbol_index"
        if capabilities["indexed"]["effective"]:
            implementation = capabilities["indexed"]["implementation"]
            return "domain_parser" if implementation == "parser" else "text_index"
        if descriptor is not None and descriptor.enabled:
            return "discovery"
        return "unsupported"


@dataclass(frozen=True, slots=True)
class RequiredPathRule:
    """A deterministic exact/glob requirement for a snapshot inventory."""

    pattern: str
    minimum_indexed: int = 1
    maximum_indexed: int | None = None

    def __post_init__(self) -> None:
        normalized = str(self.pattern or "").replace("\\", "/").lstrip("/").strip()
        if not normalized or ".." in PurePosixPath(normalized).parts:
            raise ValueError("required_path_pattern_invalid")
        if self.minimum_indexed < 0:
            raise ValueError("required_path_minimum_invalid")
        if self.maximum_indexed is not None and self.maximum_indexed < self.minimum_indexed:
            raise ValueError("required_path_maximum_invalid")
        object.__setattr__(self, "pattern", normalized)

    @classmethod
    def from_value(cls, value: "RequiredPathRule | Mapping[str, Any] | str") -> "RequiredPathRule":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(pattern=value)
        if isinstance(value, Mapping):
            return cls(
                pattern=str(value.get("pattern") or value.get("path") or value.get("glob") or ""),
                minimum_indexed=int(value.get("minimum_indexed", value.get("min_matches", 1))),
                maximum_indexed=(
                    int(value["maximum_indexed"])
                    if value.get("maximum_indexed") is not None
                    else int(value["max_matches"])
                    if value.get("max_matches") is not None
                    else None
                ),
            )
        raise ValueError("required_path_rule_invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "minimum_indexed": self.minimum_indexed,
            "maximum_indexed": self.maximum_indexed,
        }


def evaluate_required_paths(
    *,
    files: Iterable[Mapping[str, Any]],
    rules: Iterable[RequiredPathRule],
) -> dict[str, Any]:
    """Evaluate requirements against classified files, never the host filesystem."""

    rows = [dict(item) for item in files]
    results: list[dict[str, Any]] = []
    for rule in sorted(rules, key=lambda item: item.pattern):
        matching = sorted(
            str(row.get("path") or "")
            for row in rows
            if fnmatch.fnmatchcase(str(row.get("path") or ""), rule.pattern)
        )
        indexed = sorted(
            str(row.get("path") or "")
            for row in rows
            if fnmatch.fnmatchcase(str(row.get("path") or ""), rule.pattern)
            and str(row.get("outcome") or "") == CoverageOutcome.INDEXED.value
        )
        passed = len(indexed) >= rule.minimum_indexed and (
            rule.maximum_indexed is None or len(indexed) <= rule.maximum_indexed
        )
        if not matching:
            reason_code = "required_path_missing"
        elif len(indexed) < rule.minimum_indexed:
            reason_code = "required_path_not_indexed"
        elif rule.maximum_indexed is not None and len(indexed) > rule.maximum_indexed:
            reason_code = "required_path_maximum_exceeded"
        else:
            reason_code = "required_path_satisfied"
        results.append(
            {
                **rule.as_dict(),
                "matched_count": len(matching),
                "indexed_count": len(indexed),
                "matched_paths": matching,
                "indexed_paths": indexed,
                "passed": passed,
                "reason_code": reason_code,
            }
        )
    failed = [row["pattern"] for row in results if not row["passed"]]
    return {
        "passed": not failed,
        "rule_count": len(results),
        "failed_patterns": failed,
        "rules": results,
    }


def _canonical_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject non-JSON profile values before they influence a snapshot revision."""

    rendered = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    parsed = json.loads(rendered)
    if not isinstance(parsed, dict):  # pragma: no cover - guarded by the Mapping input
        raise ValueError("snapshot_profile_invalid")
    return parsed


def _stable_digest(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _share(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _counter_payload(counter: Counter[str], total: int) -> dict[str, dict[str, int | float]]:
    return {
        str(key): {"count": int(count), "share": _share(int(count), total)}
        for key, count in sorted(counter.items(), key=lambda item: str(item[0]))
    }

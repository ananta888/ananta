"""Bounded, content-free privacy sentinel scanning for SFU broadcast surfaces."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import quote_from_bytes

SENTINEL_SCHEMA = "ananta.sfu-broadcast-privacy-sentinels.v1"
MANIFEST_SCHEMA = "ananta.sfu-broadcast-privacy-scan-manifest.v1"
REPORT_SCHEMA = "ananta.sfu-broadcast-privacy-scan-report.v1"
POLICY_VERSION = "1.0"
SENTINEL_CATEGORIES = frozenset(
    {
        "media",
        "transcript",
        "semantic",
        "evidence",
        "key",
        "token",
        "credential",
        "private_ip",
        "sdp_ice",
    }
)
REQUIRED_SURFACES = frozenset(
    {"hub", "sfu", "turn", "browser", "metrics", "trace", "crashdump", "test_artifact"}
)
ALLOWED_FORMATS = frozenset(
    {"text_log", "json_log", "openmetrics", "otel_json", "browser_json", "binary_dump", "artifact_json"}
)
HARD_FILE_SIZE_MAX = 16 * 1024 * 1024
HARD_BYTES_SCANNED_MAX = 256 * 1024 * 1024
HARD_SOURCE_COUNT_MAX = 256
READ_CHUNK_BYTES = 64 * 1024


class SfuBroadcastPrivacyScanConfigurationError(ValueError):
    """A content-free configuration failure safe to expose in reports."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ScanLimits:
    file_size_max: int
    bytes_scanned_max: int


@dataclass(frozen=True, slots=True)
class ScanSource:
    surface: str
    path: PurePosixPath
    format: str
    required: bool


@dataclass(frozen=True, slots=True)
class ScanManifest:
    limits: ScanLimits
    required_surfaces: frozenset[str]
    sources: tuple[ScanSource, ...]
    canonical_sha256: str

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "ScanManifest":
        _require_exact_keys(
            document,
            frozenset({"schema", "policy_version", "limits", "required_surfaces", "sources"}),
            "sfu_privacy_manifest_invalid",
        )
        if document.get("schema") != MANIFEST_SCHEMA or document.get("policy_version") != POLICY_VERSION:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_manifest_version_invalid")
        limits_value = _as_mapping(document.get("limits"), "sfu_privacy_scan_limits_invalid")
        _require_exact_keys(
            limits_value,
            frozenset({"file_size_max", "bytes_scanned_max"}),
            "sfu_privacy_scan_limits_invalid",
        )
        file_size_max = _bounded_int(
            limits_value.get("file_size_max"),
            maximum=HARD_FILE_SIZE_MAX,
            reason_code="sfu_privacy_scan_limits_invalid",
        )
        bytes_scanned_max = _bounded_int(
            limits_value.get("bytes_scanned_max"),
            maximum=HARD_BYTES_SCANNED_MAX,
            reason_code="sfu_privacy_scan_limits_invalid",
        )
        required_surfaces_value = document.get("required_surfaces")
        if not isinstance(required_surfaces_value, Sequence) or isinstance(required_surfaces_value, (str, bytes)):
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_surface_coverage_invalid")
        required_surfaces = frozenset(required_surfaces_value)
        if required_surfaces != REQUIRED_SURFACES or len(required_surfaces_value) != len(REQUIRED_SURFACES):
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_surface_coverage_invalid")

        sources_value = document.get("sources")
        if not isinstance(sources_value, Sequence) or isinstance(sources_value, (str, bytes)):
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sources_invalid")
        if not 1 <= len(sources_value) <= HARD_SOURCE_COUNT_MAX:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_source_count_invalid")
        sources = tuple(_parse_source(value) for value in sources_value)
        source_keys = [(source.surface, str(source.path)) for source in sources]
        if len(source_keys) != len(set(source_keys)):
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_source_duplicate")
        covered = {source.surface for source in sources if source.required}
        if covered != REQUIRED_SURFACES:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_surface_coverage_invalid")
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return cls(
            limits=ScanLimits(file_size_max=file_size_max, bytes_scanned_max=bytes_scanned_max),
            required_surfaces=required_surfaces,
            sources=sources,
            canonical_sha256=hashlib.sha256(canonical).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class SentinelSet:
    values: Mapping[str, bytes]
    needles: tuple[tuple[bytes, tuple[str, ...]], ...]

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "SentinelSet":
        _require_exact_keys(
            document,
            frozenset({"schema", "sentinels"}),
            "sfu_privacy_sentinel_document_invalid",
        )
        if document.get("schema") != SENTINEL_SCHEMA:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sentinel_version_invalid")
        sentinels = _as_mapping(document.get("sentinels"), "sfu_privacy_sentinel_document_invalid")
        if set(sentinels) != SENTINEL_CATEGORIES:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sentinel_coverage_invalid")
        values: dict[str, bytes] = {}
        for category in sorted(SENTINEL_CATEGORIES):
            value = sentinels.get(category)
            if not isinstance(value, str):
                raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sentinel_value_invalid")
            encoded = value.encode("utf-8")
            if not 7 <= len(encoded) <= 512 or b"\0" in encoded:
                raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sentinel_value_invalid")
            values[category] = encoded
        raw_values = list(values.values())
        for index, first in enumerate(raw_values):
            for second in raw_values[index + 1 :]:
                if first in second or second in first:
                    raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_sentinel_ambiguous")

        categories_by_needle: dict[bytes, set[str]] = {}
        for category, raw in values.items():
            text = raw.decode("utf-8")
            variants = {
                raw,
                base64.b64encode(raw),
                quote_from_bytes(raw, safe="").encode("ascii"),
                json.dumps(text, ensure_ascii=True)[1:-1].encode("ascii"),
            }
            for variant in variants:
                if variant:
                    categories_by_needle.setdefault(variant, set()).add(category)
        needles = tuple(
            (needle, tuple(sorted(categories)))
            for needle, categories in sorted(categories_by_needle.items(), key=lambda item: (-len(item[0]), item[0]))
        )
        return cls(values=values, needles=needles)


def scan_sfu_broadcast_privacy_surfaces(
    *,
    root: Path,
    manifest_document: Mapping[str, Any],
    sentinel_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Scan only explicitly listed sources and return no matched content."""

    manifest = ScanManifest.from_mapping(manifest_document)
    sentinels = SentinelSet.from_mapping(sentinel_document)
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_scan_root_invalid")

    bytes_scanned = 0
    files_scanned = 0
    findings: list[dict[str, str]] = []
    source_results: list[dict[str, Any]] = []
    report_reasons: set[str] = set()
    for source in sorted(manifest.sources, key=lambda item: (item.surface, str(item.path))):
        public_path = _public_path(source.path, sentinels)
        source_reasons: set[str] = set()
        source_bytes = 0
        categories: set[str] = set()
        candidate = resolved_root.joinpath(*source.path.parts)
        try:
            if candidate.is_symlink():
                source_reasons.add("sfu_privacy_source_unsupported")
            else:
                resolved_source = candidate.resolve(strict=True)
                resolved_source.relative_to(resolved_root)
                if not resolved_source.is_file():
                    source_reasons.add("sfu_privacy_source_unsupported")
                else:
                    size = resolved_source.stat().st_size
                    remaining_global = manifest.limits.bytes_scanned_max - bytes_scanned
                    if remaining_global <= 0:
                        source_reasons.add("sfu_privacy_scan_bytes_exceeded")
                    else:
                        read_limit = min(size, manifest.limits.file_size_max, remaining_global)
                        source_bytes, categories = _scan_file(
                            resolved_source,
                            maximum_bytes=read_limit,
                            needles=sentinels.needles,
                        )
                        files_scanned += 1
                        bytes_scanned += source_bytes
                        if size > manifest.limits.file_size_max:
                            source_reasons.add("sfu_privacy_source_truncated")
                        if size > remaining_global:
                            source_reasons.add("sfu_privacy_scan_bytes_exceeded")
                        if categories:
                            source_reasons.update(
                                f"sfu_privacy_{category}_sentinel_detected" for category in categories
                            )
        except FileNotFoundError:
            if source.required:
                source_reasons.add("sfu_privacy_required_source_unreadable")
        except (OSError, RuntimeError, ValueError):
            source_reasons.add("sfu_privacy_scan_internal_error")

        for category in sorted(categories):
            findings.append(
                {
                    "path": public_path,
                    "surface": source.surface,
                    "category": category,
                    "reason_code": f"sfu_privacy_{category}_sentinel_detected",
                }
            )
        report_reasons.update(source_reasons)
        source_results.append(
            {
                "path": public_path,
                "surface": source.surface,
                "format": source.format,
                "required": source.required,
                "status": "blocked" if source_reasons else "clean",
                "bytes_scanned": source_bytes,
                "reason_codes": sorted(source_reasons),
            }
        )

    reason_codes = sorted(report_reasons) if report_reasons else ["sfu_privacy_scan_clean"]
    return {
        "schema": REPORT_SCHEMA,
        "policy_version": POLICY_VERSION,
        "decision": "block" if report_reasons else "allow",
        "reason_codes": reason_codes,
        "manifest_sha256": manifest.canonical_sha256,
        "sentinel_categories": sorted(SENTINEL_CATEGORIES),
        "required_surfaces": sorted(REQUIRED_SURFACES),
        "limits": {
            "file_size_max": manifest.limits.file_size_max,
            "bytes_scanned_max": manifest.limits.bytes_scanned_max,
        },
        "measurements": {
            "source_count": len(manifest.sources),
            "files_scanned": files_scanned,
            "bytes_scanned": bytes_scanned,
            "finding_count": len(findings),
        },
        "findings": sorted(findings, key=lambda item: (item["path"], item["category"])),
        "sources": source_results,
    }


def configuration_failure_report(reason_code: str) -> dict[str, Any]:
    """Create a deterministic fail-closed report without exception or input text."""

    public_reason = (
        reason_code
        if isinstance(reason_code, str) and reason_code.startswith("sfu_privacy_")
        else "sfu_privacy_scan_internal_error"
    )
    return {
        "schema": REPORT_SCHEMA,
        "policy_version": POLICY_VERSION,
        "decision": "block",
        "reason_codes": [public_reason],
        "manifest_sha256": None,
        "sentinel_categories": sorted(SENTINEL_CATEGORIES),
        "required_surfaces": sorted(REQUIRED_SURFACES),
        "limits": None,
        "measurements": {
            "source_count": 0,
            "files_scanned": 0,
            "bytes_scanned": 0,
            "finding_count": 0,
        },
        "findings": [],
        "sources": [],
    }


def _scan_file(
    path: Path,
    *,
    maximum_bytes: int,
    needles: tuple[tuple[bytes, tuple[str, ...]], ...],
) -> tuple[int, set[str]]:
    if maximum_bytes < 0:
        raise ValueError("invalid scan bound")
    longest = max((len(needle) for needle, _categories in needles), default=1)
    overlap = b""
    scanned = 0
    categories: set[str] = set()
    with path.open("rb") as stream:
        while scanned < maximum_bytes:
            chunk = stream.read(min(READ_CHUNK_BYTES, maximum_bytes - scanned))
            if not chunk:
                break
            scanned += len(chunk)
            window = overlap + chunk
            for needle, needle_categories in needles:
                if needle in window:
                    categories.update(needle_categories)
            overlap = window[-(longest - 1) :] if longest > 1 else b""
    return scanned, categories


def _parse_source(value: object) -> ScanSource:
    source = _as_mapping(value, "sfu_privacy_source_invalid")
    _require_exact_keys(
        source,
        frozenset({"surface", "path", "format", "required"}),
        "sfu_privacy_source_invalid",
    )
    surface = source.get("surface")
    raw_path = source.get("path")
    source_format = source.get("format")
    required = source.get("required")
    if surface not in REQUIRED_SURFACES or source_format not in ALLOWED_FORMATS or not isinstance(required, bool):
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_source_invalid")
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_source_path_invalid")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or str(path) != raw_path or any(part in {"", ".", ".."} for part in path.parts):
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_source_path_invalid")
    return ScanSource(surface=surface, path=path, format=source_format, required=required)


def _public_path(path: PurePosixPath, sentinels: SentinelSet) -> str:
    encoded = str(path).encode("utf-8")
    if any(needle in encoded for needle, _categories in sentinels.needles):
        digest = hashlib.sha256(encoded).hexdigest()
        return f"redacted-path-sha256:{digest}"
    return str(path)


def _as_mapping(value: object, reason_code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SfuBroadcastPrivacyScanConfigurationError(reason_code)
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], reason_code: str) -> None:
    if set(value) != set(expected):
        raise SfuBroadcastPrivacyScanConfigurationError(reason_code)


def _bounded_int(value: object, *, maximum: int, reason_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SfuBroadcastPrivacyScanConfigurationError(reason_code)
    return value


__all__ = [
    "ALLOWED_FORMATS",
    "MANIFEST_SCHEMA",
    "POLICY_VERSION",
    "REPORT_SCHEMA",
    "REQUIRED_SURFACES",
    "SENTINEL_CATEGORIES",
    "SENTINEL_SCHEMA",
    "ScanManifest",
    "SentinelSet",
    "SfuBroadcastPrivacyScanConfigurationError",
    "configuration_failure_report",
    "scan_sfu_broadcast_privacy_surfaces",
]

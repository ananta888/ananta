"""Project canonical file-type truth into the existing CodeCompass manifest.

The service is intentionally a pure projection.  It neither scans files nor
owns jobs, queues, persistence, or authorization; the Hub remains responsible
for those concerns and the executing pipeline supplies observed file outcomes.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.file_type_support_service import probe_file_type_runtime_requirements
from ananta_contracts.file_type_classifier import FileTypeClassifier
from ananta_contracts.file_type_support import (
    SCHEMA_VERSION,
    CapabilityDimension,
    CapabilityImplementation,
    FileTypeDescriptor,
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)

_EFFECTIVE_MODE = {
    CapabilityDimension.INDEXED: {
        CapabilityImplementation.TEXT_FALLBACK: "plain_text",
        CapabilityImplementation.HEURISTIC: "structured",
        CapabilityImplementation.PARSER: "structured",
    },
    CapabilityDimension.SYMBOLS: {
        CapabilityImplementation.TEXT_FALLBACK: "heuristic",
        CapabilityImplementation.HEURISTIC: "heuristic",
        CapabilityImplementation.PARSER: "parser_backed",
    },
    CapabilityDimension.RELATIONSHIPS: {
        CapabilityImplementation.TEXT_FALLBACK: "structural",
        CapabilityImplementation.HEURISTIC: "referential",
        CapabilityImplementation.PARSER: "semantic",
    },
}


@dataclass(frozen=True, slots=True)
class _ObservedFile:
    path: str
    format_id: str
    outcome: str
    diagnostics: tuple[str, ...]
    fallback_reason: str | None


class FileTypeManifestService:
    """Build additive manifest fields from registry truth and observed results."""

    def __init__(
        self,
        *,
        registry: FileTypeSupportRegistry,
        runtime_availability: Mapping[str, bool] | None = None,
    ) -> None:
        self.registry = registry
        self.classifier = FileTypeClassifier(registry)
        self.runtime_availability = (
            dict(runtime_availability)
            if runtime_availability is not None
            else probe_file_type_runtime_requirements(registry)
        )
        matrix = registry.support_matrix(runtime_availability=self.runtime_availability)
        self._rag_helper_matrix = {
            str(row["format_id"]): row
            for row in matrix["rows"]
            if row["pipeline"] == "rag_helper"
        }

    def enrich_rag_helper_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        raw_files = manifest.get("files")
        files = raw_files if isinstance(raw_files, list) else []
        observed_items: list[_ObservedFile] = []
        enriched_files: list[Any] = []
        for index, item in enumerate(files):
            observed = (
                self._observe_file(item)
                if index < 100_000 and isinstance(item, Mapping)
                else None
            )
            if observed is not None:
                observed_items.append(observed)
                enriched_files.append({**dict(item), "detected_type": observed.format_id})
            else:
                enriched_files.append(item)
        observed = tuple(observed_items)
        result = dict(manifest)
        result["files"] = enriched_files
        result["file_type_registry"] = {
            "schema_version": SCHEMA_VERSION,
            "registry_version": self.registry.registry_version,
            "snapshot_hash": self.registry.digest,
        }
        result["coverage"] = self._coverage(observed)
        result["file_type_capabilities"] = self._capabilities(observed)
        return result

    def _observe_file(self, raw: Mapping[str, Any]) -> _ObservedFile | None:
        path = str(raw.get("file") or "").replace("\\", "/")[:4096]
        if not path:
            return None
        classification = self.classifier.classify(path, is_text=True)
        format_id = classification.format_id if classification is not None else "unknown_text"
        stats = raw.get("stats") if isinstance(raw.get("stats"), Mapping) else {}
        diagnostics = {
            str(value).strip()
            for value in list(stats.get("diagnostic_codes") or [])[:256]
            if str(value).strip()
        }
        if raw.get("error"):
            outcome = "failed"
            diagnostics.add("file_read_failed")
        elif raw.get("skipped"):
            outcome = "excluded"
            reason = str(raw.get("skip_reason") or "").strip()
            if reason:
                diagnostics.add(reason)
        else:
            outcome = "indexed"
        fallback_reason = str(raw.get("fallback_reason") or "").strip() or None
        if raw.get("fallback") and fallback_reason is None:
            fallback_reason = "parser_fallback"
        return _ObservedFile(
            path=path,
            format_id=format_id,
            outcome=outcome,
            diagnostics=tuple(sorted(diagnostics)),
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _coverage(observed: Sequence[_ObservedFile]) -> dict[str, Any]:
        outcomes = Counter(item.outcome for item in observed)
        diagnostics = Counter(code for item in observed for code in item.diagnostics)
        return {
            "manifest_candidate_count": len(observed),
            "indexed": outcomes["indexed"],
            "excluded": outcomes["excluded"],
            "unsupported": outcomes["unsupported"],
            "failed": outcomes["failed"],
            "truncated": any("limit" in code for code in diagnostics),
            "diagnostic_counts": dict(sorted(diagnostics.items())[:256]),
        }

    def _capabilities(self, observed: Sequence[_ObservedFile]) -> list[dict[str, Any]]:
        files_by_type: dict[str, list[_ObservedFile]] = defaultdict(list)
        for item in observed:
            files_by_type[item.format_id].append(item)
        rows: list[dict[str, Any]] = []
        for format_id, files in sorted(files_by_type.items()):
            descriptor = self.registry.descriptor(format_id)
            matrix_row = self._rag_helper_matrix.get(format_id)
            if descriptor is None or matrix_row is None:
                continue
            fallback_reasons = sorted({item.fallback_reason for item in files if item.fallback_reason})
            claims = {
                dimension.value: self._capability_claim(matrix_row, dimension)
                for dimension in CapabilityDimension
            }
            if claims["indexed"]["effective"] == "none":
                claims["symbols"]["effective"] = "none"
                claims["relationships"]["effective"] = "none"
            elif claims["symbols"]["effective"] == "none":
                claims["relationships"]["effective"] = "none"
            rows.append(
                {
                    "detected_type": format_id,
                    "pipeline": "rag_helper",
                    **claims,
                    "parser_id": descriptor.parser_strategy or None,
                    "parser_version": _parser_version(descriptor),
                    "fallback_reason": (
                        fallback_reasons[0]
                        if len(fallback_reasons) == 1
                        else "multiple" if fallback_reasons else None
                    ),
                    "diagnostic_codes": sorted(
                        {code for item in files for code in item.diagnostics}
                    )[:256],
                    "file_count": len(files),
                }
            )
        return rows

    @staticmethod
    def _capability_claim(
        matrix_row: Mapping[str, Any],
        dimension: CapabilityDimension,
    ) -> dict[str, Any]:
        raw = dict(matrix_row["capabilities"][dimension.value])
        implementation = CapabilityImplementation(str(raw["implementation"]))
        effective = "none"
        if raw.get("effective"):
            effective = _EFFECTIVE_MODE[dimension].get(implementation, "none")
        return {
            "configured": bool(raw.get("configured")),
            "runtime_available": raw.get("runtime_available") is True,
            "verified": bool(raw.get("verified")),
            "effective": effective,
        }


def _parser_version(descriptor: FileTypeDescriptor) -> str | None:
    matches = re.findall(r"(?:^|[-_])v(\d+(?:\.\d+)*)\b", descriptor.parser_strategy)
    return matches[-1] if matches else None


_service: FileTypeManifestService | None = None


def get_file_type_manifest_service() -> FileTypeManifestService:
    global _service
    if _service is None:
        root = Path(__file__).resolve().parents[2]
        _service = FileTypeManifestService(registry=load_file_type_support_registry(root))
    return _service


__all__ = ["FileTypeManifestService", "get_file_type_manifest_service"]

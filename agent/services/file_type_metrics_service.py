from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent import metrics
from ananta_contracts.file_type_classifier import FileTypeClassifier
from ananta_contracts.file_type_support import FileTypeSupportRegistry, load_file_type_support_registry

_KNOWN_OUTCOMES = frozenset({"indexed", "excluded", "unsupported", "failed"})
_KNOWN_DIAGNOSTICS = frozenset(
    {
        "alias_limit_exceeded",
        "binary_or_unknown_type",
        "entity_declaration_blocked",
        "file_read_failed",
        "file_size_limit_exceeded",
        "parser_fallback",
        "parser_failed",
        "parser_limit_exceeded",
        "parser_timeout",
        "path_policy_blocked",
        "secret_value_redacted",
        "security_blocked",
        "selection_limit",
        "setup_index_not_configured",
        "unknown_text_type",
    }
)


@dataclass(frozen=True)
class FileTypeMetricPorts:
    files: Any
    fallbacks: Any
    diagnostics: Any
    durations: Any
    bytes: Any
    symbols: Any
    edges: Any

    @classmethod
    def prometheus(cls) -> "FileTypeMetricPorts":
        return cls(
            files=metrics.CODECOMPASS_FILE_TYPE_FILES_TOTAL,
            fallbacks=metrics.CODECOMPASS_FILE_TYPE_FALLBACKS_TOTAL,
            diagnostics=metrics.CODECOMPASS_FILE_TYPE_DIAGNOSTICS_TOTAL,
            durations=metrics.CODECOMPASS_FILE_TYPE_DURATION_SECONDS,
            bytes=metrics.CODECOMPASS_FILE_TYPE_BYTES,
            symbols=metrics.CODECOMPASS_FILE_TYPE_SYMBOLS,
            edges=metrics.CODECOMPASS_FILE_TYPE_EDGES,
        )


class FileTypeMetricsService:
    """Validates bounded registry labels before touching metric backends."""

    def __init__(
        self,
        *,
        registry: FileTypeSupportRegistry,
        ports: FileTypeMetricPorts | None = None,
    ) -> None:
        self.registry = registry
        self.ports = ports or FileTypeMetricPorts.prometheus()
        self._format_ids = {descriptor.format_id for descriptor in registry.descriptors} | {
            "excluded_before_classification",
            "unclassified_binary",
            "unknown_text",
            "unsafe_or_missing",
            "other",
        }
        self._pipelines = set(registry.pipelines)
        self._classifier = FileTypeClassifier(registry)

    def observe_snapshot(self, *, pipeline: str, snapshot: Sequence[Mapping[str, Any]]) -> None:
        normalized_pipeline = pipeline if pipeline in self._pipelines else "setup_index"
        for raw in list(snapshot)[:128]:
            format_id = self._format_id(raw.get("format_id"))
            outcomes = dict(raw.get("outcomes") or {})
            for outcome, count in outcomes.items():
                normalized_outcome = str(outcome) if str(outcome) in _KNOWN_OUTCOMES else "failed"
                self.ports.files.labels(
                    pipeline=normalized_pipeline,
                    format_id=format_id,
                    outcome=normalized_outcome,
                ).inc(min(10_000_000, max(0, int(count or 0))))
            byte_size = min(10_000_000_000, max(0, int(raw.get("byte_size") or 0)))
            self.ports.bytes.labels(pipeline=normalized_pipeline, format_id=format_id).observe(byte_size)
            for outcome, duration_seconds in sorted(
                dict(raw.get("duration_seconds_by_outcome") or {}).items()
            ):
                normalized_outcome = str(outcome) if str(outcome) in _KNOWN_OUTCOMES else "failed"
                self.ports.durations.labels(
                    pipeline=normalized_pipeline,
                    format_id=format_id,
                    outcome=normalized_outcome,
                ).observe(_bounded_float(duration_seconds, upper=3_600.0))
            self.ports.symbols.labels(
                pipeline=normalized_pipeline,
                format_id=format_id,
            ).observe(_bounded_int(raw.get("symbol_count"), upper=10_000_000))
            self.ports.edges.labels(
                pipeline=normalized_pipeline,
                format_id=format_id,
            ).observe(_bounded_int(raw.get("edge_count"), upper=10_000_000))
            for reason, count in sorted(dict(raw.get("fallbacks") or {}).items()):
                self.ports.fallbacks.labels(
                    pipeline=normalized_pipeline,
                    format_id=format_id,
                    reason_code=self._diagnostic_code(reason),
                ).inc(min(10_000_000, max(0, int(count or 0))))
            for code, count in sorted(dict(raw.get("diagnostics") or {}).items()):
                normalized_code = self._diagnostic_code(code)
                self.ports.diagnostics.labels(
                    pipeline=normalized_pipeline,
                    format_id=format_id,
                    diagnostic_code=normalized_code,
                ).inc(min(10_000_000, max(0, int(count or 0))))

    def observe_parser_result(
        self,
        *,
        pipeline: str,
        format_id: str,
        outcome: str,
        duration_seconds: float,
        byte_size: int,
        symbol_count: int,
        edge_count: int,
        fallback_reason: str | None = None,
        diagnostics: Sequence[str] = (),
    ) -> None:
        normalized_pipeline = pipeline if pipeline in self._pipelines else "other"
        normalized_format = self._format_id(format_id)
        normalized_outcome = outcome if outcome in _KNOWN_OUTCOMES else "failed"
        labels = {
            "pipeline": normalized_pipeline,
            "format_id": normalized_format,
        }
        self.ports.files.labels(**labels, outcome=normalized_outcome).inc()
        duration = _bounded_float(duration_seconds, upper=3_600.0)
        self.ports.durations.labels(**labels, outcome=normalized_outcome).observe(duration)
        self.ports.bytes.labels(**labels).observe(_bounded_int(byte_size, upper=10_000_000_000))
        self.ports.symbols.labels(**labels).observe(_bounded_int(symbol_count, upper=10_000_000))
        self.ports.edges.labels(**labels).observe(_bounded_int(edge_count, upper=10_000_000))
        if fallback_reason:
            self.ports.fallbacks.labels(
                **labels,
                reason_code=self._diagnostic_code(fallback_reason),
            ).inc()
        for code in diagnostics:
            self.ports.diagnostics.labels(
                **labels,
                diagnostic_code=self._diagnostic_code(code),
            ).inc()

    def observe_path_result(
        self,
        *,
        pipeline: str,
        path: str,
        outcome: str,
        duration_seconds: float,
        byte_size: int,
        symbol_count: int,
        edge_count: int,
        fallback_reason: str | None = None,
        diagnostics: Sequence[str] = (),
    ) -> None:
        classification = self._classifier.classify(str(path or ""), is_text=True)
        self.observe_parser_result(
            pipeline=pipeline,
            format_id=(classification.format_id if classification is not None else "other"),
            outcome=outcome,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            symbol_count=symbol_count,
            edge_count=edge_count,
            fallback_reason=fallback_reason,
            diagnostics=diagnostics,
        )

    def observe_rag_helper_manifest(self, manifest: Mapping[str, Any]) -> None:
        """Project bounded per-file parser telemetry from a rag-helper manifest.

        The manifest is treated as untrusted operational input.  Classification
        comes from the canonical registry and no path is opened or executed.
        """

        raw_files = manifest.get("files")
        if not isinstance(raw_files, list):
            return
        for raw in raw_files[:10_000]:
            if not isinstance(raw, Mapping):
                continue
            path = str(raw.get("file") or "").replace("\\", "/")[:4096]
            classification = self._classifier.classify(path, is_text=True) if path else None
            format_id = classification.format_id if classification is not None else "other"
            stats = raw.get("stats") if isinstance(raw.get("stats"), Mapping) else {}
            diagnostics = [
                str(code)
                for code in list(stats.get("diagnostic_codes") or [])[:100]
                if str(code).strip()
            ]
            if raw.get("error"):
                outcome = "failed"
                diagnostics.append("file_read_failed")
            elif raw.get("skipped"):
                outcome = "excluded"
            else:
                outcome = "indexed"
            fallback_reason = str(raw.get("fallback_reason") or "").strip() or None
            if raw.get("fallback") and fallback_reason is None:
                fallback_reason = "parser_fallback"
            self.observe_parser_result(
                pipeline="rag_helper",
                format_id=format_id,
                outcome=outcome,
                duration_seconds=_bounded_float(raw.get("duration_ms"), upper=3_600_000.0) / 1000.0,
                byte_size=_bounded_int(raw.get("size"), upper=10_000_000_000),
                symbol_count=_bounded_int(
                    stats.get("detail_count", raw.get("output_record_count", 0)),
                    upper=10_000_000,
                ),
                edge_count=_bounded_int(stats.get("relation_count"), upper=10_000_000),
                fallback_reason=fallback_reason,
                diagnostics=tuple(diagnostics),
            )

    def _format_id(self, value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in self._format_ids else "other"

    @staticmethod
    def _diagnostic_code(value: object) -> str:
        normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").split(":", 1)[0].lower()).strip("_")
        return normalized if normalized in _KNOWN_DIAGNOSTICS else "other"


_service: FileTypeMetricsService | None = None


def get_file_type_metrics_service() -> FileTypeMetricsService:
    global _service
    if _service is None:
        root = Path(__file__).resolve().parents[2]
        _service = FileTypeMetricsService(registry=load_file_type_support_registry(root))
    return _service


def _bounded_int(value: object, *, upper: int) -> int:
    try:
        return min(upper, max(0, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _bounded_float(value: object, *, upper: float) -> float:
    try:
        normalized = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(normalized):
        return 0.0
    return min(upper, max(0.0, normalized))

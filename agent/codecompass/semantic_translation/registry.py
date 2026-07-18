from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from agent.codecompass.file_type_telemetry import (
    FileTypeTelemetryPort,
    emit_file_type_telemetry,
    observe_file_type_parser_result,
)
from agent.codecompass.parser_limits import ParserGuardViolation, ParserLimits
from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter, SemanticLanguageAdapter
from agent.codecompass.semantic_translation.models import diagnostic
from agent.codecompass.semantic_translation.python_adapter import PythonSemanticAdapter
from agent.codecompass.semantic_translation.static_symbol_adapters import default_static_symbol_adapters
from agent.codecompass.semantic_translation.symbol_adapters import default_symbol_adapters
from agent.codecompass.semantic_translation.typescript_adapter import TypeScriptSemanticAdapter


@dataclass(frozen=True)
class SemanticAdapterDescriptor:
    language: str
    extensions: tuple[str, ...]
    parser_strategy: str
    known_limits: tuple[str, ...]
    semantic_kinds: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "extensions": list(self.extensions),
            "parser_strategy": self.parser_strategy,
            "known_limits": list(self.known_limits),
            "semantic_kinds": list(self.semantic_kinds),
        }


class SemanticParseExecutionPort(Protocol):
    """Narrow safe-execution seam for consumers that only parse source."""

    def parse(self, path: str, content: str) -> dict: ...

    def parse_for_language(
        self,
        language: str,
        path: str,
        content: str,
    ) -> dict: ...


class SemanticGraphExecutionPort(Protocol):
    """Narrow safe-execution seam for consumers that only need graph records."""

    def emit_graph_records(self, path: str, content: str) -> dict: ...

    def emit_graph_records_for_language(
        self,
        language: str,
        path: str,
        content: str,
    ) -> dict: ...


class SemanticExecutionPort(
    SemanticParseExecutionPort,
    SemanticGraphExecutionPort,
    Protocol,
):
    """Composite compatibility port for consumers that need both operations."""


class SemanticAdapterRegistry:
    """Deterministic source of truth for executable semantic adapters.

    Focused adapters still own parsing and graph emission. The registry owns
    discovery plus the shared limits, failure and telemetry envelope around
    those calls, keeping execution policy out of individual parser modules.
    """

    def __init__(
        self,
        adapters: Iterable[SemanticLanguageAdapter] | None = None,
        *,
        limits: ParserLimits | None = None,
        telemetry: FileTypeTelemetryPort | None = None,
    ) -> None:
        configured = tuple(adapters) if adapters is not None else (
            PythonSemanticAdapter(),
            JavaSemanticAdapter(),
            TypeScriptSemanticAdapter(),
            *default_symbol_adapters(),
            *default_static_symbol_adapters(),
        )
        self._adapters = tuple(sorted(configured, key=lambda item: item.language))
        self._limits = limits or ParserLimits.from_environment()
        self._telemetry = telemetry or observe_file_type_parser_result
        self._by_extension: dict[str, SemanticLanguageAdapter] = {}
        self._by_language: dict[str, SemanticLanguageAdapter] = {}
        languages: set[str] = set()
        for adapter in self._adapters:
            language = str(adapter.language).strip().lower()
            if not language or language in languages:
                raise ValueError(f"duplicate_semantic_adapter_language:{language}")
            languages.add(language)
            for extension in adapter.supported_extensions:
                normalized = _normalize_extension(extension)
                if normalized in self._by_extension:
                    other = self._by_extension[normalized]
                    raise ValueError(
                        f"duplicate_semantic_adapter_extension:{normalized}:{other.language}:{language}"
                    )
                self._by_extension[normalized] = adapter
            self._by_language[language] = adapter

    def adapters(self) -> tuple[SemanticLanguageAdapter, ...]:
        return self._adapters

    def descriptors(self) -> tuple[SemanticAdapterDescriptor, ...]:
        return tuple(
            SemanticAdapterDescriptor(
                language=adapter.language,
                extensions=tuple(sorted(_normalize_extension(value) for value in adapter.supported_extensions)),
                parser_strategy=adapter.parser_strategy,
                known_limits=tuple(adapter.known_limits),
                semantic_kinds=tuple(getattr(adapter, "semantic_kinds", ())),
            )
            for adapter in self._adapters
        )

    def support_matrix(self) -> list[dict[str, object]]:
        return [descriptor.as_dict() for descriptor in self.descriptors()]

    def find(self, path: str, content: str = "") -> SemanticLanguageAdapter | None:
        extension = Path(path).suffix.lower()
        direct = self._by_extension.get(extension)
        if direct is not None:
            return direct
        for adapter in self._adapters:
            try:
                if adapter.detect(path, content):
                    return adapter
            except Exception:
                continue
        return None

    def find_language(self, language: str) -> SemanticLanguageAdapter | None:
        """Resolve an explicitly requested adapter without bypassing the registry."""

        return self._by_language.get(str(language or "").strip().lower())

    def parse(self, path: str, content: str) -> dict:
        return self._parse(path=path, content=content, language=None)

    def parse_for_language(
        self,
        language: str,
        path: str,
        content: str,
    ) -> dict:
        """Parse with a named adapter through the shared safety envelope."""

        return self._parse(path=path, content=content, language=language)

    def _parse(
        self,
        *,
        path: str,
        content: str,
        language: str | None,
    ) -> dict:
        started = time.perf_counter()
        violation = self._preflight(path, content)
        if violation is not None:
            return self._observed_parse_result(
                path=path,
                content=content,
                started=started,
                result=self._empty_parse(path, content, violation.as_diagnostic(path=path)),
                outcome="excluded",
            )
        adapter = self.find_language(language) if language is not None else self.find(path, content)
        if adapter is None:
            requested = f" for language {language!r}" if language is not None else " for this file type"
            return self._observed_parse_result(
                path=path,
                content=content,
                started=started,
                result=self._empty_parse(
                    path,
                    content,
                    diagnostic(
                        "semantic_adapter_unsupported",
                        f"No semantic adapter is registered{requested}.",
                        path=path,
                    ),
                ),
                outcome="unsupported",
            )
        budget = self._limits.budget()
        try:
            parsed = adapter.parse(path, content)
            if not isinstance(parsed, Mapping):
                raise TypeError("semantic_adapter_parse_result_must_be_mapping")
            budget.check_time()
            budget.check_record_count(self._parse_record_count(parsed))
        except ParserGuardViolation as exc:
            return self._observed_parse_result(
                path=path,
                content=content,
                started=started,
                result=self._empty_parse(path, content, exc.as_diagnostic(path=path)),
                outcome="failed" if exc.diagnostic_code == "parser_timeout" else "excluded",
            )
        except Exception as exc:
            return self._observed_parse_result(
                path=path,
                content=content,
                started=started,
                result=self._empty_parse(path, content, self._failure_diagnostic(path, exc)),
                outcome="failed",
            )
        return self._observed_parse_result(
            path=path,
            content=content,
            started=started,
            result=dict(parsed),
            outcome="indexed",
            fallback_reason=self._result_fallback_reason(parsed, adapter),
        )

    def emit_graph_records(self, path: str, content: str) -> dict:
        return self._emit_graph_records(path=path, content=content, language=None)

    def emit_graph_records_for_language(
        self,
        language: str,
        path: str,
        content: str,
    ) -> dict:
        """Execute a named adapter through the same limits and telemetry guard."""

        return self._emit_graph_records(path=path, content=content, language=language)

    def _emit_graph_records(
        self,
        *,
        path: str,
        content: str,
        language: str | None,
    ) -> dict:
        started = time.perf_counter()
        violation = self._preflight(path, content)
        if violation is not None:
            return self._observed_graph_result(
                path=path,
                content=content,
                started=started,
                result={
                    "nodes": [],
                    "edges": [],
                    "diagnostics": [violation.as_diagnostic(path=path)],
                },
                outcome="failed" if violation.diagnostic_code == "parser_timeout" else "excluded",
            )
        adapter = self.find_language(language) if language is not None else self.find(path, content)
        if adapter is None:
            requested = f" for language {language!r}" if language is not None else " for this file type"
            return self._observed_graph_result(
                path=path,
                content=content,
                started=started,
                result={
                    "nodes": [],
                    "edges": [],
                    "diagnostics": [
                        diagnostic(
                            "semantic_adapter_unsupported",
                            f"No semantic adapter is registered{requested}.",
                            path=path,
                        )
                    ],
                },
                outcome="unsupported",
            )
        budget = self._limits.budget()
        try:
            emitted = adapter.emit_graph_records(path, content)
            if not isinstance(emitted, Mapping):
                raise TypeError("semantic_adapter_graph_result_must_be_mapping")
            nodes = emitted.get("nodes") or []
            edges = emitted.get("edges") or []
            diagnostics = emitted.get("diagnostics") or []
            if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(diagnostics, list):
                raise TypeError("semantic_adapter_graph_collections_must_be_lists")
        except Exception as exc:
            return self._observed_graph_result(
                path=path,
                content=content,
                started=started,
                result={
                    "nodes": [],
                    "edges": [],
                    "diagnostics": [self._failure_diagnostic(path, exc)],
                },
                outcome="failed",
            )
        try:
            budget.check_time()
            budget.check_record_count(len(nodes) + len(edges))
        except ParserGuardViolation as exc:
            return self._observed_graph_result(
                path=path,
                content=content,
                started=started,
                result={"nodes": [], "edges": [], "diagnostics": [exc.as_diagnostic(path=path)]},
                outcome="failed" if exc.diagnostic_code == "parser_timeout" else "excluded",
            )
        return self._observed_graph_result(
            path=path,
            content=content,
            started=started,
            result=dict(emitted),
            outcome="indexed",
            fallback_reason=self._result_fallback_reason(emitted, adapter),
        )

    def _observed_parse_result(
        self,
        *,
        path: str,
        content: str,
        started: float,
        result: dict,
        outcome: str,
        fallback_reason: str | None = None,
    ) -> dict:
        diagnostics = self._diagnostic_codes(result)
        emit_file_type_telemetry(
            self._telemetry,
            pipeline="semantic_translation",
            path=path,
            outcome=outcome,
            duration_seconds=time.perf_counter() - started,
            byte_size=len(content.encode("utf-8", errors="replace")),
            symbol_count=self._parse_record_count(result),
            edge_count=0,
            fallback_reason=fallback_reason,
            diagnostics=diagnostics,
        )
        return result

    def _observed_graph_result(
        self,
        *,
        path: str,
        content: str,
        started: float,
        result: dict,
        outcome: str,
        fallback_reason: str | None = None,
    ) -> dict:
        diagnostics = self._diagnostic_codes(result)
        emit_file_type_telemetry(
            self._telemetry,
            pipeline="semantic_translation",
            path=path,
            outcome=outcome,
            duration_seconds=time.perf_counter() - started,
            byte_size=len(content.encode("utf-8", errors="replace")),
            symbol_count=len(result.get("nodes") or []),
            edge_count=len(result.get("edges") or []),
            fallback_reason=fallback_reason,
            diagnostics=diagnostics,
        )
        return result

    def _preflight(self, path: str, content: str) -> ParserGuardViolation | None:
        try:
            self._limits.preflight(path=path, content=content)
        except ParserGuardViolation as exc:
            return exc
        return None

    @staticmethod
    def _empty_parse(path: str, content: str, diagnostic_record: dict) -> dict:
        return {
            "path": path,
            "content": content,
            "types": [],
            "functions": [],
            "imports": [],
            "symbols": [],
            "diagnostics": [diagnostic_record],
        }

    @staticmethod
    def _failure_diagnostic(path: str, exc: Exception) -> dict:
        return diagnostic(
            "parser_failed",
            f"Semantic adapter failed safely ({type(exc).__name__}).",
            path=path,
        )

    @staticmethod
    def _diagnostic_codes(result: Mapping[str, object]) -> tuple[str, ...]:
        raw_diagnostics = result.get("diagnostics")
        if not isinstance(raw_diagnostics, (list, tuple)):
            return ()
        return tuple(
            str(item.get("code") or "")
            for item in raw_diagnostics
            if isinstance(item, Mapping) and str(item.get("code") or "").strip()
        )

    @staticmethod
    def _parse_record_count(result: Mapping[str, object]) -> int:
        imports = result.get("imports")
        import_count = len(imports) if isinstance(imports, list) else 0
        symbols = result.get("symbols")
        if isinstance(symbols, list):
            # Static adapters expose types/functions as projections of the
            # canonical symbol list. Counting every view would charge the same
            # declaration twice against limits and telemetry.
            return len(symbols) + import_count
        return import_count + sum(
            len(value)
            for key in ("types", "functions")
            if isinstance((value := result.get(key)), list)
        )

    @staticmethod
    def _adapter_fallback_reason(adapter: SemanticLanguageAdapter) -> str | None:
        strategy = str(adapter.parser_strategy).lower()
        return (
            "parser_fallback"
            if any(token in strategy for token in ("fallback", "heuristic", "regex", "static"))
            else None
        )

    @classmethod
    def _result_fallback_reason(
        cls,
        result: Mapping[str, object],
        adapter: SemanticLanguageAdapter,
    ) -> str | None:
        if str(result.get("fallback_reason") or "").strip():
            return "parser_fallback"
        return cls._adapter_fallback_reason(adapter)


def _normalize_extension(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("empty_semantic_adapter_extension")
    return normalized if normalized.startswith(".") else f".{normalized}"


_DEFAULT_REGISTRY: SemanticAdapterRegistry | None = None


def get_semantic_adapter_registry() -> SemanticAdapterRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SemanticAdapterRegistry()
    return _DEFAULT_REGISTRY

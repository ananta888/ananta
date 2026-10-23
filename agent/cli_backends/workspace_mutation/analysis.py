"""Observed source inspection prerequisite for workspace mutation.

These local hashes are technical observations, never Hub evidence identities.
The mutable gate accumulates successful runtime reads and a model's explicit
analysis; model claims alone cannot unlock writes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

_SOURCE_SUFFIXES: Final = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".sh", ".bash", ".java", ".c", ".h", ".cpp",
    ".cs", ".rb", ".php", ".vue", ".svelte", ".sql",
})


def is_source_reference(path: str) -> bool:
    """True when a path can count as an observed analysis source.

    The gate observes bytes of source files only; documents, reports and
    manifests cannot authorize a mutation. One predicate keeps the gate,
    the context materialization and the read hints from drifting apart.
    """
    return PurePosixPath(str(path or "")).suffix in _SOURCE_SUFFIXES


class SourceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    path: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class CodeAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    sources: tuple[SourceReference, ...] = Field(min_length=1)
    findings: str = Field(min_length=1)
    change_reasoning: str = Field(min_length=1)
    test_reasoning: str = Field(min_length=1)


class AnalysisState(TypedDict):
    required: bool
    status: str
    codecompass_observed: bool
    observed_sources: list[dict[str, str | int]]
    analysis: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class ObservedSource:
    path: str
    file_sha256: str
    line_start: int
    line_end: int


def collect_read_observations(reads) -> list[tuple[str, str, int, int]]:
    """Observation candidates from successful read results.

    A read range counts regardless of excerpt truncation or emptiness: the gate
    re-verifies the bytes and bounds itself, and an excerpt clipped at the
    output cap must stay citable instead of silently un-observing its range.
    """
    observed: list[tuple[str, str, int, int]] = []
    for read in reads:
        data = read.get("data") or {}
        if read.get("status") != "ok":
            continue
        for source in read.get("evidence") or []:
            if source.get("kind") != "file_excerpt":
                continue
            observed.append(
                (
                    str(source.get("path") or ""),
                    str(data.get("file_sha256") or ""),
                    int(data.get("line_start") or 0),
                    int(data.get("line_end") or 0),
                )
            )
    return observed


class CodeAnalysisGate:
    """Accumulate runtime observations before permitting the first mutation."""

    def __init__(self, *, required: bool, workspace: Path) -> None:
        self.required = required
        self.workspace = workspace.resolve()
        self.codecompass_observed = False
        self.sources: list[ObservedSource] = []
        self.analysis: CodeAnalysis | None = None

    @property
    def pending(self) -> bool:
        return self.required and self.analysis is None

    def observe_codecompass(self, *, successful: bool) -> None:
        self.codecompass_observed |= successful

    def observe_range(self, *, path: str, file_sha256: str, line_start: int, line_end: int) -> None:
        if not self.codecompass_observed or line_start < 1 or line_end < line_start:
            return
        target = (self.workspace / path).resolve()
        if not target.is_relative_to(self.workspace) or not is_source_reference(str(target)):
            return
        try:
            content = target.read_bytes()
        except OSError:
            return
        if hashlib.sha256(content).hexdigest() != file_sha256 or line_start > len(content.splitlines()):
            return
        self.sources.append(ObservedSource(str(target.relative_to(self.workspace)), file_sha256,
                                           line_start, min(line_end, len(content.splitlines()))))

    def accept(self, payload: dict[str, JsonValue]) -> str | None:
        """Return a machine-readable rejection, or retain a source-bound analysis."""
        try:
            analysis = CodeAnalysis.model_validate_json(json.dumps(payload))
        except ValidationError:
            return "code_analysis_invalid"
        if not all(value.strip() for value in
                   (analysis.findings, analysis.change_reasoning, analysis.test_reasoning)):
            return "code_analysis_invalid"
        if not self.codecompass_observed:
            return "codecompass_inspection_required"
        for ref in analysis.sources:
            if not any(ref.path == source.path and ref.file_sha256 == source.file_sha256
                       and source.line_start <= ref.line_start <= ref.line_end <= source.line_end
                       for source in self.sources):
                return "code_analysis_source_not_observed"
            try:
                current_hash = hashlib.sha256((self.workspace / ref.path).read_bytes()).hexdigest()
            except OSError:
                return "code_analysis_source_unavailable"
            if current_hash != ref.file_sha256:
                return "code_analysis_source_changed"
        self.analysis = analysis
        return None

    def state(self) -> AnalysisState:
        return {
            "required": self.required,
            "status": "required" if self.pending else "accepted" if self.analysis else "optional",
            "codecompass_observed": self.codecompass_observed,
            "observed_sources": [{"path": s.path, "file_sha256": s.file_sha256,
                                  "line_start": s.line_start, "line_end": s.line_end} for s in self.sources],
            "analysis": self.analysis.model_dump(mode="json") if self.analysis else None,
        }

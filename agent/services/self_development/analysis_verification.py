"""Hub verification of the delegated runtime's source-analysis prerequisite."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.services.self_development.workspace import read_source


class AnalysisVerificationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ObservedRange(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    path: str
    file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class RecordedAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    sources: tuple[ObservedRange, ...] = Field(min_length=1)
    findings: str = Field(min_length=1)
    change_reasoning: str = Field(min_length=1)
    test_reasoning: str = Field(min_length=1)


class AnalysisObservation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    required: Literal[True]
    status: Literal['accepted']
    codecompass_observed: Literal[True]
    observed_sources: tuple[ObservedRange, ...] = Field(min_length=1)
    analysis: RecordedAnalysis


class WorkerAnalysisReport(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    schema_name: Literal['ananta_worker_mutation_report.v1'] = Field(alias='schema')
    task_id: str
    outcome: Literal['final_answer']
    code_analysis: AnalysisObservation


def verify_worker_analysis(workspace: Path, *, task_id: str,
                           baseline: Mapping[str, str]) -> WorkerAnalysisReport:
    """Bind observed pre-write source ranges to this Task's exported revision."""
    try:
        report = WorkerAnalysisReport.model_validate_json(
            read_source(workspace, '.ananta/mutation-report.json'))
    except (OSError, ValueError) as exc:
        raise AnalysisVerificationError('worker_code_analysis_report_required') from exc
    if report.task_id != task_id:
        raise AnalysisVerificationError('worker_code_analysis_task_mismatch')
    observation = report.code_analysis
    for ref in observation.analysis.sources:
        if (baseline.get(ref.path) != ref.file_sha256
                or not any(source.path == ref.path and source.file_sha256 == ref.file_sha256
                    and source.line_start <= ref.line_start <= ref.line_end <= source.line_end
                    for source in observation.observed_sources)):
            raise AnalysisVerificationError('worker_code_analysis_source_mismatch')
    return report

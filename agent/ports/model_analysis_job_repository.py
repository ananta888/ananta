"""Atomic persistence port for Hub-owned model-analysis jobs."""

from __future__ import annotations

from typing import Protocol

from agent.models.model_analysis_job import (
    ModelAnalysisJobRecord,
    ModelAnalysisLimits,
)


class ModelAnalysisJobRepository(Protocol):
    def admit(
        self,
        record: ModelAnalysisJobRecord,
        *,
        idempotency_key_digest: str,
        request_digest: str,
        limits: ModelAnalysisLimits,
    ) -> tuple[ModelAnalysisJobRecord, bool]: ...

    def get(self, job_id: str) -> ModelAnalysisJobRecord | None: ...

    def compare_and_set(
        self,
        record: ModelAnalysisJobRecord,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord: ...

    def mark_projected(
        self,
        job_id: str,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord: ...

    def list_recoverable(
        self,
        *,
        now_epoch_ms: int,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]: ...

    def list_page(
        self,
        *,
        tenant_id: str,
        after_job_id: str | None,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]: ...


__all__ = ["ModelAnalysisJobRepository"]

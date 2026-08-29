"""Small ports shared by Hub composition and the isolated optimization worker."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ananta_contracts.dspy_optimization import OptimizationSpecV1, PromptProgramV1
from ananta_contracts.provider_execution import ProviderExecutionBinding


class OptimizationEnginePort(Protocol):
    def optimize(
        self, spec: OptimizationSpecV1, baseline: PromptProgramV1, records: Sequence[Mapping[str, Any]]
    ) -> PromptProgramV1: ...


class AuthorizedLmPort(Protocol):
    def complete(
        self,
        *,
        binding: ProviderExecutionBinding,
        role: str,
        messages: Sequence[Mapping[str, str]],
        request_id: str,
    ) -> Mapping[str, Any]: ...


class AuthorizedRetrieverPort(Protocol):
    def retrieve(self, *, scope: Mapping[str, str], query: str, top_k: int) -> Sequence[Mapping[str, Any]]: ...


class MetricPort(Protocol):
    def evaluate(self, *, expected: Mapping[str, Any], actual: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ProgramArtifactPort(Protocol):
    def put(self, *, tenant_id: str, run_id: str, program: PromptProgramV1) -> Mapping[str, Any]: ...


class PromotionPort(Protocol):
    def promote(self, *, tenant_id: str, candidate_digest: str, expected_revision: int) -> Mapping[str, Any]: ...


__all__ = [
    "AuthorizedLmPort",
    "AuthorizedRetrieverPort",
    "MetricPort",
    "OptimizationEnginePort",
    "ProgramArtifactPort",
    "PromotionPort",
]

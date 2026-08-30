"""Backend port and normalized output for one delegated research stage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ResearchStageOutput:
    artifact_kind: str
    content: bytes
    metrics: Mapping[str, float]
    executable: bool = False


class ResearchWorkerBackend(Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...

    def execute(
        self,
        *,
        run_spec: Mapping[str, Any],
        stage: Mapping[str, Any],
        attempt_id: str,
    ) -> ResearchStageOutput: ...


__all__ = ["ResearchStageOutput", "ResearchWorkerBackend"]

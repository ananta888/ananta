"""Narrow ports separating Hub governance from experiment execution."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ananta_contracts.dendritic_memory import DendriticJobSpecV1, DendriticMemoryPackManifestV1


class DendriticExperimentBackendPort(Protocol):
    def prepare(self, spec: DendriticJobSpecV1) -> Mapping[str, Any]: ...

    def train(self, spec: DendriticJobSpecV1, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...

    def evaluate(self, spec: DendriticJobSpecV1, pack: DendriticMemoryPackManifestV1) -> Mapping[str, Any]: ...

    def compose(
        self, spec: DendriticJobSpecV1, parents: Sequence[DendriticMemoryPackManifestV1]
    ) -> Mapping[str, Any]: ...

    def cancel(self, *, run_id: str, attempt_id: str) -> None: ...


class DendriticPackArtifactPort(Protocol):
    def put(
        self, *, manifest: DendriticMemoryPackManifestV1, files: Mapping[str, bytes]
    ) -> Mapping[str, Any]: ...


class DendriticModelCatalogPort(Protocol):
    def resolve(self, *, model_id: str, snapshot_digest: str) -> Mapping[str, Any]: ...


__all__ = ["DendriticExperimentBackendPort", "DendriticModelCatalogPort", "DendriticPackArtifactPort"]

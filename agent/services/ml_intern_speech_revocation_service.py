"""Fence training jobs and adapters affected by speech lineage revocation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    get_ml_intern_training_repository,
)
from agent.services.ml_intern_speech_adapter_registry import MlInternSpeechAdapterRegistry
from agent.services.ml_intern_speech_lineage_service import SpeechImpactReport
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.voice_governance_domain import VoicePrincipal


class SpeechTrainingFencePort(Protocol):
    def fence(self, principal: VoicePrincipal, *, lineage_digest: str, revocation_epoch: int) -> bool: ...


class SpeechAdapterFencePort(Protocol):
    def fence(self, principal: VoicePrincipal, *, lineage_digest: str, revocation_epoch: int) -> bool: ...


class SqlSpeechTrainingFencePort:
    def __init__(self, repository: MlInternTrainingRepository | None = None) -> None:
        self._repository = repository or get_ml_intern_training_repository()

    def fence(self, principal: VoicePrincipal, *, lineage_digest: str, revocation_epoch: int) -> bool:
        return self._repository.fence_by_request_digest(
            MlInternTrainingPrincipal(principal.tenant_id, principal.subject),
            request_digest=lineage_digest,
            revocation_epoch=revocation_epoch,
        )


class SqlSpeechAdapterFencePort:
    """Compatibility-named adapter over the sole SQL registry authority."""

    def __init__(self, registry: MlInternSpeechAdapterRegistry | None = None) -> None:
        self._registry = registry

    def fence(self, principal: VoicePrincipal, *, lineage_digest: str, revocation_epoch: int) -> bool:
        registry = self._registry or _runtime_registry()
        return bool(
            registry.fence_by_artifact_digest(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                artifact_sha256=lineage_digest,
                revocation_epoch=revocation_epoch,
            )
        )


class FileBackedSpeechAdapterFencePort(SqlSpeechAdapterFencePort):
    """Deprecated name retained as a SQL-backed compatibility adapter."""


class CompositeSpeechAdapterFencePort:
    def __init__(self, *ports: SpeechAdapterFencePort) -> None:
        if not ports:
            raise ValueError("speech_adapter_fence_ports_required")
        self._ports = ports

    def fence(self, principal: VoicePrincipal, *, lineage_digest: str, revocation_epoch: int) -> bool:
        matched = False
        for port in self._ports:
            matched = (
                port.fence(
                    principal,
                    lineage_digest=lineage_digest,
                    revocation_epoch=revocation_epoch,
                )
                or matched
            )
        return matched


@dataclass(frozen=True)
class SpeechTrainingRevocationOutcome:
    fenced_jobs: tuple[str, ...]
    fenced_adapters: tuple[str, ...]
    unresolved: tuple[tuple[str, str], ...]


class MlInternSpeechRevocationService:
    def __init__(
        self,
        *,
        jobs: SpeechTrainingFencePort | None = None,
        adapters: SpeechAdapterFencePort | None = None,
    ) -> None:
        self._jobs = jobs or SqlSpeechTrainingFencePort()
        self._adapters = adapters or SqlSpeechAdapterFencePort()

    def fence_impact(
        self,
        principal: VoicePrincipal,
        report: SpeechImpactReport,
    ) -> SpeechTrainingRevocationOutcome:
        fenced_jobs: list[str] = []
        fenced_adapters: list[str] = []
        unresolved: list[tuple[str, str]] = []
        if report.truncated:
            unresolved.append((report.root_kind, "impact_truncated"))
        for node in report.nodes:
            kind = str(node["kind"])
            digest = str(node["digest"])
            if kind == "job":
                if self._jobs.fence(principal, lineage_digest=digest, revocation_epoch=report.revocation_epoch):
                    fenced_jobs.append(digest)
                else:
                    unresolved.append((kind, digest))
            elif kind == "adapter":
                if self._adapters.fence(principal, lineage_digest=digest, revocation_epoch=report.revocation_epoch):
                    fenced_adapters.append(digest)
                else:
                    unresolved.append((kind, digest))
        return SpeechTrainingRevocationOutcome(
            tuple(sorted(fenced_jobs)),
            tuple(sorted(fenced_adapters)),
            tuple(sorted(unresolved)),
        )


def _runtime_registry() -> MlInternSpeechAdapterRegistry:
    configured: object | None = None
    authority_audit: object | None = None
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            cached = current_app.extensions.get("ml_intern_speech_adapter_registry")
            if isinstance(cached, MlInternSpeechAdapterRegistry):
                return cached
            agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
            speech_config = dict(agent_config.get("speech_adaptation") or {})
            configured = speech_config.get("adapter_registry_path")
            authority_audit = current_app.extensions.get("semantic_media_audit_recorder")
    except RuntimeError:
        pass
    path = Path(
        os.getenv("ANANTA_SPEECH_ADAPTER_REGISTRY_PATH") or configured or "artifacts/speech-adapters/registry.json"
    )
    return MlInternSpeechAdapterRegistry(path, authority_audit=authority_audit)


__all__ = [
    "CompositeSpeechAdapterFencePort",
    "FileBackedSpeechAdapterFencePort",
    "MlInternSpeechRevocationService",
    "SpeechAdapterFencePort",
    "SpeechTrainingFencePort",
    "SpeechTrainingRevocationOutcome",
    "SqlSpeechAdapterFencePort",
    "SqlSpeechTrainingFencePort",
]

"""Concrete adapters from local release policy to existing Hub services."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from agent.services.local_adapter_lifecycle_coordinator import (
    LocalAdapterLifecycleCoordinator,
    LocalAdapterLifecycleRepository,
)
from agent.services.local_adapter_serving_activation import (
    LocalAdapterCandidateSource,
    LocalAdapterServingActivationService,
    LocalAdapterServingProjection,
    SubprocessLocalAdapterServingMaterializer,
)
from agent.services.local_model_runtime_lifecycle_service import (
    LocalRuntimeLifecycleService,
)
from agent.services.local_multi_model_runtime import LocalModelCapability
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryNotFoundError,
)
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)


class MlInternLocalAdapterRegistryPort:
    """Uses the existing atomic promotion history and scoped registry rollback."""

    def __init__(
        self,
        *,
        registry: MlInternAdapterRegistryService,
        principal: MlInternTrainingPrincipal,
        approved_by: str,
        minimum_score: float,
    ) -> None:
        self._registry = registry
        self._principal = principal
        self._approved_by = approved_by
        self._minimum_score = float(minimum_score)

    def promote(
        self,
        *,
        candidate_id: str,
        expected_revision: int,
        idempotency_key: str,
        evidence_sha256: str,
    ) -> Mapping[str, object]:
        promoted, replayed = self._registry.promote_local_evaluated(
            candidate_id,
            lifecycle_evidence_sha256=evidence_sha256,
            approved_by=self._approved_by,
            idempotency_key=idempotency_key,
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
            expected_version=expected_revision,
            minimum_eval_score=self._minimum_score,
        )
        if promoted.status != "approved":
            raise RuntimeError("local_adapter_registry_promotion_incomplete")
        return {
            "registry_revision": promoted.registry_version,
            "replayed": replayed,
        }

    def rollback(self, *, candidate_id: str, reason_code: str) -> Mapping[str, object]:
        record = self._record(candidate_id)
        deprecated, target = self._registry.rollback(
            candidate_id,
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
            expected_version=record.registry_version,
        )
        return {
            "registry_revision": deprecated.registry_version,
            "reason_code": reason_code,
            "rollback_target_id": target.adapter_id if target is not None else None,
            "rollback_target_sha256": getattr(target, "artifact_sha256", None) if target is not None else None,
        }

    def _record(self, candidate_id: str):
        record = self._registry.get(
            candidate_id,
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
        )
        if record is None:
            raise RegistryNotFoundError(f"adapter {candidate_id!r} not found")
        return record


class LocalModelRuntimeRestartAdapter:
    """Maps a release restart to one admitted, digest-bound operator action."""

    def __init__(
        self,
        *,
        lifecycle: LocalRuntimeLifecycleService,
        capabilities: Sequence[LocalModelCapability],
        activation: LocalAdapterServingActivationService,
    ) -> None:
        self._lifecycle = lifecycle
        self._capabilities = tuple(capabilities)
        self._activation = activation

    def restart(
        self,
        *,
        target: str,
        candidate_id: str | None,
        candidate_sha256: str | None,
    ) -> bool:
        if target not in {"needle2", "lfm2.5-2.6b-agentic"}:
            raise ValueError("local_adapter_target_invalid")
        binding = candidate_sha256 or "base-model"
        previous = self._activation.switch(
            target=target,
            candidate_id=candidate_id,
            candidate_sha256=candidate_sha256,
        )
        try:
            decision = self._lifecycle.evaluate(
                request_id=f"adapter-restart-{target}-{binding}",
                capabilities=self._capabilities,
            )
            if not decision.admitted:
                self._activation.restore(target=target, previous=previous)
                return False
            receipt = self._lifecycle.apply(
                decision_id=decision.decision_id,
                action="restart",
            )
        except Exception:
            self._activation.restore(target=target, previous=previous)
            raise
        if receipt.status != "completed":
            self._activation.restore(target=target, previous=previous)
            return False
        return True


class MlInternLocalAdapterCandidateSourcePort:
    """Resolves only an approved, ownership-scoped Registry candidate."""

    def __init__(
        self,
        *,
        registry: MlInternAdapterRegistryService,
        principal: MlInternTrainingPrincipal,
    ) -> None:
        self._registry = registry
        self._principal = principal

    def resolve(
        self,
        *,
        candidate_id: str,
        target: str,
        candidate_sha256: str,
    ) -> LocalAdapterCandidateSource:
        record = self._registry.get(
            candidate_id,
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
        )
        if record is None:
            raise RegistryNotFoundError(f"adapter {candidate_id!r} not found")
        artifact_directory = str(record.artifact_paths.get("adapter_dir") or "").strip()
        if (
            record.status != "approved"
            or record.release_target != target
            or record.artifact_sha256 != candidate_sha256
            or not artifact_directory
        ):
            raise ValueError("local_adapter_serving_candidate_unverified")
        return LocalAdapterCandidateSource(
            candidate_id=record.adapter_id,
            target=target,
            artifact_directory=Path(artifact_directory),
            candidate_sha256=candidate_sha256,
        )


def build_local_adapter_lifecycle_coordinator(
    *,
    state_path: str | Path,
    registry: MlInternAdapterRegistryService,
    principal: MlInternTrainingPrincipal,
    approved_by: str,
    minimum_score: float,
    lifecycle: LocalRuntimeLifecycleService,
    capabilities: Sequence[LocalModelCapability],
    activation: LocalAdapterServingActivationService,
    audit_sink: Callable[[str, Mapping[str, object]], None],
) -> LocalAdapterLifecycleCoordinator:
    """Compose existing Hub authorities without exposing them to Workers."""

    return LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(state_path),
        registry=MlInternLocalAdapterRegistryPort(
            registry=registry,
            principal=principal,
            approved_by=approved_by,
            minimum_score=minimum_score,
        ),
        runtime=LocalModelRuntimeRestartAdapter(
            lifecycle=lifecycle,
            capabilities=capabilities,
            activation=activation,
        ),
        audit_sink=audit_sink,
    )


def build_local_adapter_serving_activation(
    *,
    registry: MlInternAdapterRegistryService,
    principal: MlInternTrainingPrincipal,
    projection_path: str | Path,
    output_root: str | Path,
    needle_binary: str | Path,
    needle_base_checkpoint: str | Path,
    lfm_python: str | Path,
    lfm_converter: str | Path,
    lfm_base_snapshot: str | Path,
) -> LocalAdapterServingActivationService:
    """Compose the fully automatic, offline Hub serving conversion path."""

    return LocalAdapterServingActivationService(
        sources=MlInternLocalAdapterCandidateSourcePort(
            registry=registry,
            principal=principal,
        ),
        materializer=SubprocessLocalAdapterServingMaterializer(
            output_root=output_root,
            needle_binary=needle_binary,
            needle_base_checkpoint=needle_base_checkpoint,
            lfm_python=lfm_python,
            lfm_converter=lfm_converter,
            lfm_base_snapshot=lfm_base_snapshot,
        ),
        projection=LocalAdapterServingProjection(projection_path),
    )


__all__ = [
    "LocalModelRuntimeRestartAdapter",
    "MlInternLocalAdapterCandidateSourcePort",
    "MlInternLocalAdapterRegistryPort",
    "build_local_adapter_serving_activation",
    "build_local_adapter_lifecycle_coordinator",
]

"""Concrete adapters from local release policy to existing Hub services."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from agent.services.local_adapter_lifecycle_coordinator import (
    LocalAdapterLifecycleCoordinator,
    LocalAdapterLifecycleRepository,
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
    ) -> None:
        self._lifecycle = lifecycle
        self._capabilities = tuple(capabilities)

    def restart(self, *, target: str, candidate_sha256: str | None) -> bool:
        if target not in {"needle2", "lfm2.5-2.6b-agentic"}:
            raise ValueError("local_adapter_target_invalid")
        binding = candidate_sha256 or "base-model"
        request_id = f"adapter-restart-{target}-{binding}"
        decision = self._lifecycle.evaluate(
            request_id=request_id,
            capabilities=self._capabilities,
        )
        if not decision.admitted:
            return False
        receipt = self._lifecycle.apply(
            decision_id=decision.decision_id,
            action="restart",
        )
        return receipt.status == "completed"


def build_local_adapter_lifecycle_coordinator(
    *,
    state_path: str | Path,
    registry: MlInternAdapterRegistryService,
    principal: MlInternTrainingPrincipal,
    approved_by: str,
    minimum_score: float,
    lifecycle: LocalRuntimeLifecycleService,
    capabilities: Sequence[LocalModelCapability],
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
        ),
        audit_sink=audit_sink,
    )


__all__ = [
    "LocalModelRuntimeRestartAdapter",
    "MlInternLocalAdapterRegistryPort",
    "build_local_adapter_lifecycle_coordinator",
]

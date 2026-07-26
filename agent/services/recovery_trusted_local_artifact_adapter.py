"""Trusted-local port from Hub execution into Recovery artifact ingress."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ananta_contracts.recovery_artifact_ingress import (
    validate_recovery_artifact_receipts_payload,
)


class RecoveryTrustedLocalArtifactError(RuntimeError):
    """Raised when Hub-local evidence cannot cross the trusted-local port."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class RecoveryTrustedLocalArtifactAdapter:
    """Materialize local workspace claims through the authoritative Hub core."""

    def __init__(
        self,
        *,
        manifest_service_provider: Callable[[], Any] | None = None,
        ingress_service_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._manifest_service_provider = manifest_service_provider
        self._ingress_service_provider = ingress_service_provider

    def materialize(
        self,
        *,
        task: Mapping[str, Any],
        artifacts: list[dict[str, Any]] | None,
        lease_token: str,
        request_fingerprint: str,
    ) -> list[dict[str, Any]] | None:
        if artifacts is None:
            return None
        if not artifacts:
            return []
        from agent.config import settings

        executor_id = str(settings.agent_name or "").strip()
        executor_url = str(
            settings.agent_url
            or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        if (
            str(settings.role or "").strip().lower() != "hub"
            or not bool(settings.hub_can_be_worker)
            or not executor_id
            or not executor_url
        ):
            raise RecoveryTrustedLocalArtifactError(
                "recovery_artifact_trusted_local_authority_denied"
            )
        try:
            built = self._manifest_service().build(
                task=task,
                artifacts=artifacts,
                lease_token=lease_token,
                request_fingerprint=request_fingerprint,
                executor_id=executor_id,
                executor_url=executor_url,
            )
            payload = self._ingress_service().materialize_trusted_local(
                task_id=str(task.get("id") or ""),
                manifest=built.manifest,
                lease_token=lease_token,
                request_fingerprint=request_fingerprint,
                executor_id=executor_id,
                executor_url=executor_url,
            )
            return validate_recovery_artifact_receipts_payload(
                payload,
                manifest=built.manifest,
                descriptors=built.descriptors,
            )["artifacts"]
        except Exception as exc:
            if isinstance(exc, RecoveryTrustedLocalArtifactError):
                raise
            reason_code = str(
                getattr(exc, "reason_code", "") or str(exc)
            )
            raise RecoveryTrustedLocalArtifactError(
                reason_code
                or "recovery_artifact_trusted_local_materialization_failed"
            ) from exc

    def _manifest_service(self) -> Any:
        if self._manifest_service_provider is not None:
            return self._manifest_service_provider()
        from agent.services.recovery_workspace_artifact_manifest_service import (
            get_recovery_workspace_artifact_manifest_service,
        )

        return get_recovery_workspace_artifact_manifest_service()

    def _ingress_service(self) -> Any:
        if self._ingress_service_provider is not None:
            return self._ingress_service_provider()
        from agent.services.recovery_artifact_ingress_service import (
            get_recovery_artifact_ingress_service,
        )

        return get_recovery_artifact_ingress_service()


_ADAPTER = RecoveryTrustedLocalArtifactAdapter()


def get_recovery_trusted_local_artifact_adapter() -> (
    RecoveryTrustedLocalArtifactAdapter
):
    return _ADAPTER


__all__ = [
    "RecoveryTrustedLocalArtifactAdapter",
    "RecoveryTrustedLocalArtifactError",
    "get_recovery_trusted_local_artifact_adapter",
]

"""Worker-side publisher for lease-bound Recovery workspace artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ananta_contracts.recovery_artifact_ingress import (
    RecoveryArtifactIngressContractError,
    validate_recovery_artifact_receipts_payload,
)


class RecoveryWorkerArtifactPublishError(RuntimeError):
    """Raised when a Worker cannot obtain exact Hub-owned receipts."""


class RecoveryWorkerArtifactPublisher:
    """Publish workspace metadata and replace local refs with Hub receipts."""

    def __init__(
        self,
        *,
        http_client_provider: Callable[[], Any] | None = None,
        manifest_service_provider: Callable[[], Any] | None = None,
        workspace_service_provider: Callable[[], Any] | None = None,
        token_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._http_client_provider = http_client_provider
        self._manifest_service_provider = manifest_service_provider
        self._workspace_service_provider = workspace_service_provider
        self._token_provider = token_provider

    def publish(
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

        task_id = str(task.get("id") or "").strip()
        worker_id = str(settings.agent_name or "").strip()
        worker_url = str(settings.agent_url or "").strip().rstrip("/")
        hub_url = str(settings.hub_url or "").strip().rstrip("/")
        worker_token = self._worker_token()
        if not all(
            (
                task_id,
                worker_id,
                worker_url,
                hub_url,
                worker_token,
                lease_token,
                request_fingerprint,
            )
        ):
            raise RecoveryWorkerArtifactPublishError(
                "recovery_artifact_publisher_identity_unavailable"
            )
        try:
            built = self._manifest_service().build(
                task=task,
                artifacts=artifacts,
                lease_token=lease_token,
                request_fingerprint=request_fingerprint,
                executor_id=worker_id,
                executor_url=worker_url,
            )
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "") or str(exc)
            )
            raise RecoveryWorkerArtifactPublishError(
                reason_code
                or "recovery_artifact_manifest_build_failed"
            ) from exc
        manifest = built.manifest
        descriptors = list(built.descriptors)
        from agent.services.workflow_worker_service_auth import (
            WORKER_ID_HEADER,
            WORKER_URL_HEADER,
        )

        response = self._http_client().post(
            (
                f"{hub_url}/internal/tasks/{task_id}"
                "/recovery-artifacts"
            ),
            data=manifest,
            timeout=30,
            return_response=True,
            silent=True,
            idempotency_key=manifest["digest"],
            headers={
                "Authorization": f"Bearer {worker_token}",
                WORKER_ID_HEADER: worker_id,
                WORKER_URL_HEADER: worker_url,
                "X-Ananta-Recovery-Dispatch-Lease": (
                    lease_token
                ),
            },
        )
        if (
            response is None
            or int(getattr(response, "status_code", 500)) != 200
        ):
            raise RecoveryWorkerArtifactPublishError(
                "recovery_artifact_hub_ingress_failed"
            )
        try:
            body = response.json()
        except Exception as exc:
            raise RecoveryWorkerArtifactPublishError(
                "recovery_artifact_hub_receipt_invalid"
            ) from exc
        payload = (
            body.get("data")
            if isinstance(body, Mapping)
            else None
        )
        try:
            return validate_recovery_artifact_receipts_payload(
                payload,
                manifest=manifest,
                descriptors=descriptors,
            )["artifacts"]
        except RecoveryArtifactIngressContractError as exc:
            raise RecoveryWorkerArtifactPublishError(
                exc.reason_code
            ) from exc

    def _http_client(self) -> Any:
        if self._http_client_provider is not None:
            return self._http_client_provider()
        from agent.common.http import HttpClient

        return HttpClient(timeout=30, retries=1)

    def _manifest_service(self) -> Any:
        if self._manifest_service_provider is not None:
            return self._manifest_service_provider()
        from agent.services.recovery_workspace_artifact_manifest_service import (
            RecoveryWorkspaceArtifactManifestService,
            get_recovery_workspace_artifact_manifest_service,
        )

        if self._workspace_service_provider is not None:
            return RecoveryWorkspaceArtifactManifestService(
                workspace_service_provider=(
                    self._workspace_service_provider
                )
            )
        return get_recovery_workspace_artifact_manifest_service()

    def _worker_token(self) -> str:
        if self._token_provider is not None:
            return str(self._token_provider() or "")
        from agent.auth import resolve_configured_agent_token

        return str(resolve_configured_agent_token() or "")


_PUBLISHER = RecoveryWorkerArtifactPublisher()


def get_recovery_worker_artifact_publisher() -> (
    RecoveryWorkerArtifactPublisher
):
    return _PUBLISHER


__all__ = [
    "RecoveryWorkerArtifactPublishError",
    "RecoveryWorkerArtifactPublisher",
    "get_recovery_worker_artifact_publisher",
]

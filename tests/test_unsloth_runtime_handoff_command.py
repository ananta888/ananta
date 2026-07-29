from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_mutation_command_service import (
    SqliteUnslothMutationLedger,
    UnslothMutationCommandService,
    UnslothMutationError,
)


class _RuntimeExecutor:
    def preview(self, **_values):
        raise AssertionError("operation-specific preview is required")

    def execute(self, **_values):
        raise AssertionError("operation-specific execute is required")

    def preview_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        operation_payload: Mapping[str, object],
    ):
        return {
            "tenant": principal.tenant_id,
            "adapter_id": resource_id,
            "reason_length": len(reason),
            "artifact_sha256": operation_payload["promoted_artifact_sha256"],
        }

    def execute_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
        operation_payload: Mapping[str, object],
    ):
        return {
            "tenant": principal.tenant_id,
            "adapter_id": resource_id,
            "reason_length": len(reason),
            "task_key": idempotency_key[:8],
            "artifact_sha256": operation_payload["promoted_artifact_sha256"],
        }


def _payload(*, artifact_sha256: str = "a" * 64):
    return {
        "operation": "runtime_handoff",
        "resource_id": "adapter-a",
        "reason": "Deploy promoted adapter endpoint",
        "dry_run": True,
        "confirmed": False,
        "promoted_artifact_id": "lora-export-artifact-a",
        "promoted_artifact_sha256": artifact_sha256,
        "provider_descriptor": {
            "provider_id": "local-provider",
            "provider_type": "local-openai-compatible",
            "model_id": "local/model",
            "provider_revision": "revision-a",
            "capabilities": {
                "openai_chat": True,
                "openai_responses": False,
                "anthropic_messages": False,
                "streaming": True,
                "tools": True,
                "structured_output": True,
            },
            "limits": {
                "timeout_seconds": 60,
                "context_tokens": 8192,
                "max_output_tokens": 2048,
                "stream_idle_timeout_seconds": 30,
            },
        },
        "endpoint_descriptor": {
            "endpoint_id": "endpoint-a",
            "display_name": "Promoted local adapter",
            "routing_key": "adapter-a",
        },
        "expected_endpoint_revision": 0,
        "source_ids": ["SRC_supplied-model"],
        "run_ids": ["RUN_supplied-evaluation"],
    }


def test_confirmation_binds_every_runtime_handoff_field(tmp_path) -> None:
    clock = lambda: 1_800_000_000
    service = UnslothMutationCommandService(
        executors={"runtime_handoff": _RuntimeExecutor()},
        ledger=SqliteUnslothMutationLedger(
            tmp_path / "ledger.sqlite3",
            clock=clock,
        ),
        confirmation_secret=b"runtime-handoff-confirmation-secret-32-bytes",
        clock=clock,
    )
    principal = MlInternTrainingPrincipal(
        tenant_id="tenant-a",
        subject="admin-a",
    )
    dry_run = service.execute(
        principal,
        route_operation="runtime_handoff",
        payload=_payload(),
        idempotency_key="runtime-dry-run-a",
    )
    changed = {
        **_payload(artifact_sha256="b" * 64),
        "dry_run": False,
        "confirmed": True,
        "confirmation_id": dry_run["confirmation_id"],
    }

    with pytest.raises(UnslothMutationError) as raised:
        service.execute(
            principal,
            route_operation="runtime_handoff",
            payload=changed,
            idempotency_key="runtime-confirm-a",
        )

    assert raised.value.reason_code == "unsloth_confirmation_invalid"


def test_runtime_descriptor_rejects_direct_targets_before_executor(tmp_path) -> None:
    service = UnslothMutationCommandService(
        executors={"runtime_handoff": _RuntimeExecutor()},
        ledger=SqliteUnslothMutationLedger(tmp_path / "ledger.sqlite3"),
        confirmation_secret=b"runtime-handoff-confirmation-secret-32-bytes",
    )
    payload = _payload()
    payload["provider_descriptor"] = {
        **payload["provider_descriptor"],
        "base_url": "http://provider.internal",
    }

    with pytest.raises(UnslothMutationError) as raised:
        service.execute(
            MlInternTrainingPrincipal(
                tenant_id="tenant-a",
                subject="admin-a",
            ),
            route_operation="runtime_handoff",
            payload=payload,
            idempotency_key="runtime-direct-target-a",
        )

    assert raised.value.reason_code == "unsloth_mutation_result_unsafe"

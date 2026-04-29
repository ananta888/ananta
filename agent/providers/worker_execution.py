from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .interfaces import ProviderDescriptor
from .registry import GenericProviderRegistry

EXECUTOR_KIND_TO_PROVIDER_ID = {
    "ananta_worker": "ananta_worker",
    "opencode": "opencode",
    "openai_codex_cli": "openai_codex_cli",
    "custom": "custom",
}


@dataclass(frozen=True)
class WorkerExecutionRequest:
    task_id: str
    worker_job: dict[str, Any] = field(default_factory=dict)
    context_bundle: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    policy_context: dict[str, Any] = field(default_factory=dict)
    executor_kind: str = "custom"


@dataclass(frozen=True)
class WorkerExecutionProviderResult:
    status: str
    reason: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    result_payload: dict[str, Any] = field(default_factory=dict)


class WorkerExecutionProvider(Protocol):
    descriptor: ProviderDescriptor

    def execute(self, request: WorkerExecutionRequest) -> WorkerExecutionProviderResult: ...


def normalize_executor_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in EXECUTOR_KIND_TO_PROVIDER_ID else "custom"


def register_default_worker_execution_descriptors(registry: GenericProviderRegistry) -> None:
    for provider_id, risk_class in (
        ("ananta_worker", "medium"),
        ("opencode", "high"),
        ("openai_codex_cli", "high"),
        ("custom", "medium"),
    ):
        registry.register_descriptor(
            ProviderDescriptor(
                provider_id=provider_id,
                provider_family="worker_execution",
                capabilities=("worker_job_execute", "artifact_result_emit"),
                risk_class=risk_class,
                enabled_by_default=False,
            )
        )


class WorkerExecutorDispatchBridge:
    """Maps executor_kind to provider-registry dispatch without backend-specific core imports."""

    def __init__(self, registry: GenericProviderRegistry) -> None:
        self._registry = registry

    def dispatch(
        self,
        *,
        executor_kind: str,
        request: WorkerExecutionRequest,
        enable_provider: bool = False,
    ) -> WorkerExecutionProviderResult:
        normalized_executor = normalize_executor_kind(executor_kind)
        provider_id = EXECUTOR_KIND_TO_PROVIDER_ID.get(normalized_executor, "custom")
        resolution = self._registry.resolve_provider(
            provider_family="worker_execution",
            provider_id=provider_id,
            enable=enable_provider,
        )
        if resolution.status != "available" or resolution.provider is None:
            return WorkerExecutionProviderResult(
                status="degraded",
                reason=resolution.reason or "provider_unavailable",
                artifacts=[],
                result_payload={
                    "dispatch_status": resolution.status,
                    "provider_id": provider_id,
                    "executor_kind": normalized_executor,
                },
            )
        provider = resolution.provider
        return provider.execute(request)

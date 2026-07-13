"""Production composition for Hub-delegated workflow adapter tasks.

The Worker receives one already-created Hub task.  This module only composes
execution adapters and authenticated Hub decision clients; it owns neither a
task queue nor orchestration state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from flask import Flask

from agent.providers.lc_lg import LangGraphProviderConfig
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.langgraph_checkpoint_adapter import (
    HttpLangGraphCheckpointGateway,
)
from worker.runtime.native_graph.composition import (
    build_native_graph_worker_task_adapter,
)
from worker.runtime.workflow_adapter_task_consumer import (
    ExecutionAuthorizationDecision,
    WorkflowAdapterTaskConsumer,
)
from worker.runtime.workflow_adapter_worker_profile import (
    WorkflowAdapterWorkerProfileError,
    configured_workflow_adapter_worker_config,
)
from worker.runtime.workflow_hub_gateway import (
    HttpWorkflowHubDecisionClient,
    HubExecutionAuthorizationAdapter,
)
from worker.runtime.workflow_tool_pipeline_composition import (
    build_workflow_tool_pipeline,
)

logger = logging.getLogger(__name__)

_CONSUMER_EXTENSION = "workflow_adapter_task_consumer"
_RUNTIME_EXTENSION = "workflow_adapter_worker_runtime"
_REGISTRATION_EXTENSION = "workflow_adapter_worker_registration"


class _UnavailableHubAuthorization:
    def __init__(self, reason_code: str) -> None:
        self._reason_code = str(reason_code)

    def authorize(self, **_values: Any) -> ExecutionAuthorizationDecision:
        return ExecutionAuthorizationDecision(False, self._reason_code)


@dataclass(frozen=True)
class WorkflowAdapterWorkerRuntime:
    """Built adapters plus the exact metadata a Worker may advertise."""

    consumer: WorkflowAdapterTaskConsumer
    capabilities: tuple[str, ...] = ()
    runtime_targets: tuple[dict[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()

    def registration_metadata(self) -> dict[str, Any]:
        return {
            "capabilities": list(self.capabilities),
            "runtime_targets": [dict(value) for value in self.runtime_targets],
            "reason_codes": list(self.reason_codes),
        }


def build_workflow_adapter_worker_runtime(
    *,
    agent_config: Mapping[str, Any],
    client: HttpWorkflowHubDecisionClient | None = None,
    tool_registry: Any | None = None,
    tool_invoker: Any | None = None,
    native_executor: Any | None = None,
) -> WorkflowAdapterWorkerRuntime:
    """Build configured adapters; missing authority/config always fails closed."""

    reasons: list[str] = []
    try:
        resolved_agent_config = configured_workflow_adapter_worker_config(
            agent_config
        )
    except WorkflowAdapterWorkerProfileError as exc:
        return WorkflowAdapterWorkerRuntime(
            consumer=WorkflowAdapterTaskConsumer(
                authorization=_UnavailableHubAuthorization(exc.reason_code)
            ),
            reason_codes=(exc.reason_code,),
        )
    resolved_client = client
    if resolved_client is None:
        try:
            resolved_client = HttpWorkflowHubDecisionClient.from_environment()
        except ValueError:
            reasons.append("workflow_hub_gateway_config_invalid")
    if resolved_client is None:
        reason = reasons[-1] if reasons else "workflow_hub_gateway_not_configured"
        return WorkflowAdapterWorkerRuntime(
            consumer=WorkflowAdapterTaskConsumer(
                authorization=_UnavailableHubAuthorization(reason)
            ),
            reason_codes=tuple(reasons or [reason]),
        )

    authorization = HubExecutionAuthorizationAdapter(resolved_client)
    native_adapter = None
    langgraph_adapter = None
    capabilities: list[str] = []
    targets: list[dict[str, Any]] = []

    try:
        native_capabilities = _native_worker_capabilities(resolved_agent_config)
        native_adapter = build_native_graph_worker_task_adapter(
            client=resolved_client,
            agent_config=resolved_agent_config,
            executor=native_executor,
        )
    except Exception as exc:  # noqa: BLE001 - composition must remain fail-closed
        reasons.append(_safe_reason(exc, "native_worker_adapter_config_invalid"))
    if native_adapter is not None:
        capabilities.extend(("workflow.adapter.native", *native_capabilities))
        targets.append(
            {
                "runtime_target_id": "workflow-adapter-native",
                "runtime_id": "ananta-native",
                "adapter_id": "native",
                "runtime_kind": "docker_container",
                "runtime_version": "1.0.0",
                "allowed_capabilities": list(native_capabilities),
            }
        )
    elif not any(value.startswith("native_") for value in reasons):
        reasons.append("native_worker_adapter_not_configured")

    langgraph_config = _langgraph_config(resolved_agent_config, reasons)
    if langgraph_config is not None:
        try:
            checkpoint_gateway = HttpLangGraphCheckpointGateway.from_environment()
            tool_dependencies = {}
            if tool_registry is not None:
                tool_dependencies["registry"] = tool_registry
            if tool_invoker is not None:
                tool_dependencies["invoker"] = tool_invoker
            tool_pipeline = build_workflow_tool_pipeline(
                resolved_client, **tool_dependencies
            )
            candidate = LangGraphAdapter(
                langgraph_config,
                tool_pipeline=tool_pipeline,
                checkpoint_gateway=checkpoint_gateway,
            )
            descriptor = candidate.descriptor()
        except Exception as exc:  # noqa: BLE001 - optional runtime boundary
            reasons.append(_safe_reason(exc, "langgraph_worker_adapter_config_invalid"))
        else:
            if descriptor.enabled and descriptor.status == "ready":
                langgraph_adapter = candidate
                capabilities.append("workflow.adapter.langgraph")
                targets.append(
                    {
                        "runtime_target_id": "workflow-adapter-langgraph",
                        "runtime_id": "langgraph",
                        "adapter_id": "langgraph",
                        "runtime_kind": "docker_container",
                        "runtime_version": descriptor.version,
                    }
                )
            else:
                reasons.append(
                    str(descriptor.reason or "langgraph_worker_adapter_unavailable")
                )

    return WorkflowAdapterWorkerRuntime(
        consumer=WorkflowAdapterTaskConsumer(
            authorization=authorization,
            native_adapter=native_adapter,
            langgraph_adapter=langgraph_adapter,
        ),
        capabilities=tuple(sorted(capabilities)),
        runtime_targets=tuple(targets),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def initialize_workflow_adapter_worker_runtime(
    app: Flask,
    *,
    client: HttpWorkflowHubDecisionClient | None = None,
    tool_registry: Any | None = None,
    tool_invoker: Any | None = None,
    native_executor: Any | None = None,
) -> WorkflowAdapterWorkerRuntime:
    """Install the composition before Worker registration/background startup."""

    runtime = build_workflow_adapter_worker_runtime(
        agent_config=dict(app.config.get("AGENT_CONFIG") or {}),
        client=client,
        tool_registry=tool_registry,
        tool_invoker=tool_invoker,
        native_executor=native_executor,
    )
    app.extensions[_CONSUMER_EXTENSION] = runtime.consumer
    app.extensions[_RUNTIME_EXTENSION] = runtime
    app.extensions[_REGISTRATION_EXTENSION] = runtime.registration_metadata()
    if runtime.reason_codes:
        logger.info(
            "workflow adapter worker composition: runtimes=%s reasons=%s",
            [value.get("runtime_id") for value in runtime.runtime_targets],
            list(runtime.reason_codes),
        )
    return runtime


def workflow_adapter_registration_metadata(app: Flask) -> dict[str, Any]:
    raw = app.extensions.get(_REGISTRATION_EXTENSION)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _langgraph_config(
    agent_config: Mapping[str, Any], reasons: list[str]
) -> LangGraphProviderConfig | None:
    providers = agent_config.get("providers")
    providers = providers if isinstance(providers, Mapping) else {}
    raw = providers.get("langgraph")
    if not isinstance(raw, Mapping):
        reasons.append("langgraph_provider_not_configured")
        return None
    try:
        config = LangGraphProviderConfig(**dict(raw))
    except (TypeError, ValueError) as exc:
        reasons.append(_safe_reason(exc, "langgraph_provider_config_invalid"))
        return None
    if not config.enabled:
        reasons.append("langgraph_provider_disabled")
        return None
    return config


def _native_worker_capabilities(
    agent_config: Mapping[str, Any],
) -> tuple[str, ...]:
    runtime = agent_config.get("worker_runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    native = runtime.get("native_graph")
    native = native if isinstance(native, Mapping) else {}
    raw = native.get("capabilities") or ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ValueError("native_graph_worker_capabilities_invalid")
    values = tuple(
        sorted({str(value).strip() for value in raw if str(value).strip()})
    )
    if len(values) > 128 or any(len(value) > 128 or "\x00" in value for value in values):
        raise ValueError("native_graph_worker_capabilities_invalid")
    return values


def _safe_reason(exc: BaseException, fallback: str) -> str:
    reason = str(getattr(exc, "reason_code", "") or "").strip()
    return reason if reason and len(reason) <= 160 else fallback


__all__ = [
    "WorkflowAdapterWorkerRuntime",
    "build_workflow_adapter_worker_runtime",
    "initialize_workflow_adapter_worker_runtime",
    "workflow_adapter_registration_metadata",
]

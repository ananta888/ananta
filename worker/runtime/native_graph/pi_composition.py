"""Explicit Pi deployment opt-in within the existing Native task adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from worker.runtime.native_graph.handlers import NativeTaskKindHandlers, UnavailableNativeNodeHandler
from worker.runtime.native_graph.pi_configuration import NativePiWorkerProfile, PiProfileCredentialFiles
from worker.runtime.native_graph.pi_node import (
    PI_NATIVE_CAPABILITY,
    PI_NATIVE_TASK_KIND,
    NativePiNodeHandler,
    PiTaskScopePort,
)
from worker.runtime.native_graph.ports import NativeNodeHandlerPort
from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient, HubProviderBudgetAdapter
from worker.runtime.workspace_resolver import ConfiguredWorkerWorkspaceResolver


def build_native_node_handlers(
    *,
    default: NativeNodeHandlerPort,
    scope: PiTaskScopePort,
    client: HttpWorkflowHubDecisionClient,
    native_config: Mapping[str, Any],
    agent_config: Mapping[str, Any],
) -> NativeNodeHandlerPort:
    raw = native_config.get("pi")
    profile = NativePiWorkerProfile.model_validate({} if raw is None else raw)
    pi: NativeNodeHandlerPort = UnavailableNativeNodeHandler("pi_disabled")
    if profile.enabled:
        if PI_NATIVE_TASK_KIND not in native_config.get(
            "allowed_task_types", ()
        ) or PI_NATIVE_CAPABILITY not in native_config.get("capabilities", ()):
            raise ValueError("pi_native_deployment_scope_required")
        runtime_root = Path(profile.runtime_root).resolve(strict=True)
        if not runtime_root.is_dir():
            raise ValueError("pi_native_runtime_root_unavailable")
        workspaces = ConfiguredWorkerWorkspaceResolver(agent_config)
        credentials = PiProfileCredentialFiles(profile)
        pi = NativePiNodeHandler(
            scope=scope,
            budget=HubProviderBudgetAdapter(client),
            workspace_for_task=lambda task: workspaces.resolve_workspace_context(task=task).workspace_dir,
            credential_for_profile=credentials.resolve,
            runtime_root=runtime_root,
        )
    # Even a disabled Pi task is intercepted: never reinterpret its prompt as
    # a generic shell command or silently fall back to another coding agent.
    return NativeTaskKindHandlers(default=default, handlers={PI_NATIVE_TASK_KIND: pi})

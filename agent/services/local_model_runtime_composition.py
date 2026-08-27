"""Hub composition root for the local KAT/LFM/Needle runtime domain."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agent.common.audit import log_audit
from agent.repositories.local_model_runtime_decision import SqliteLocalRuntimeDecisionRepository
from agent.services.local_model_runtime_invocation_observer import (
    LocalRuntimeInvocationObserver,
)
from agent.services.local_model_runtime_lifecycle_service import (
    HttpLocalRuntimeControl,
    LocalRuntimeControlPort,
    LocalRuntimeLifecycleService,
)
from agent.services.local_model_runtime_status_service import (
    HttpLocalResourceSnapshot,
    HttpLocalRuntimeProbe,
    LocalResourceSnapshotPort,
    LocalRuntimeStatusService,
    SystemLocalResourceSnapshot,
)
from agent.services.local_multi_model_runtime import LocalModelCapability, rtx3080_local_model_capabilities


@dataclass(slots=True)
class LocalModelRuntimeComposition:
    capabilities: tuple[LocalModelCapability, ...]
    status: LocalRuntimeStatusService
    lifecycle: LocalRuntimeLifecycleService
    invocations: LocalRuntimeInvocationObserver | None = None

    def snapshot(self):
        latest = self.lifecycle.latest_decision()
        return self.status.snapshot(
            self.capabilities,
            revision=latest.revision if latest is not None else 1,
        )


def build_local_model_runtime_composition(app: Any) -> LocalModelRuntimeComposition:
    control_url = str(
        app.config.get("ANANTA_LOCAL_MODEL_CONTROL_URL") or os.environ.get("ANANTA_LOCAL_MODEL_CONTROL_URL") or ""
    ).strip()
    control_token = str(
        app.config.get("ANANTA_LOCAL_MODEL_CONTROL_TOKEN") or os.environ.get("ANANTA_LOCAL_MODEL_CONTROL_TOKEN") or ""
    )
    resources: LocalResourceSnapshotPort
    control: LocalRuntimeControlPort | None
    if control_url:
        resources = HttpLocalResourceSnapshot(control_url, token=control_token)
        control = HttpLocalRuntimeControl(control_url, token=control_token)
    else:
        resources = SystemLocalResourceSnapshot()
        control = None
    state_path = Path(
        str(
            app.config.get("ANANTA_LOCAL_MODEL_STATE_DB")
            or os.environ.get("ANANTA_LOCAL_MODEL_STATE_DB")
            or "data/local-model-runtime/hub-decisions.sqlite3"
        )
    )
    capabilities = _runtime_capabilities(app)
    repository = SqliteLocalRuntimeDecisionRepository(state_path)
    lifecycle = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=repository,
        capabilities=capabilities,
        control=control,
        audit_sink=lambda action, facts: log_audit(action, dict(facts)),
    )
    composition = LocalModelRuntimeComposition(
        capabilities=capabilities,
        status=LocalRuntimeStatusService(
            probes=HttpLocalRuntimeProbe(),
            resources=resources,
        ),
        lifecycle=lifecycle,
    )
    composition.invocations = LocalRuntimeInvocationObserver(
        snapshot=composition.snapshot,
        audit_sink=lambda action, facts: log_audit(action, dict(facts)),
    )
    return composition


def get_local_model_runtime_composition(app: Any) -> LocalModelRuntimeComposition:
    existing = app.extensions.get("local_model_runtime_composition")
    if isinstance(existing, LocalModelRuntimeComposition):
        return existing
    composition = build_local_model_runtime_composition(app)
    app.extensions["local_model_runtime_composition"] = composition
    app.extensions["model_invocation_observation_port"] = composition.invocations
    app.extensions["tiny_router_telemetry_sink"] = composition.invocations
    return composition


def _runtime_capabilities(app: Any) -> tuple[LocalModelCapability, ...]:
    endpoints = {
        "kat": str(app.config.get("ANANTA_KAT_ENDPOINT") or os.environ.get("ANANTA_KAT_ENDPOINT") or "").strip(),
        "lfm": str(app.config.get("ANANTA_LFM_ENDPOINT") or os.environ.get("ANANTA_LFM_ENDPOINT") or "").strip(),
        "needle": str(
            app.config.get("ANANTA_NEEDLE_ENDPOINT") or os.environ.get("ANANTA_NEEDLE_ENDPOINT") or ""
        ).strip(),
    }
    return tuple(
        replace(capability, endpoint=endpoints[capability.runtime_id])
        if endpoints[capability.runtime_id]
        else capability
        for capability in rtx3080_local_model_capabilities()
    )


__all__ = [
    "LocalModelRuntimeComposition",
    "build_local_model_runtime_composition",
    "get_local_model_runtime_composition",
]

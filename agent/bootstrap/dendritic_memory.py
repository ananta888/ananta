"""Hub-only composition root for optional dendritic-memory experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.dendritic_memory_artifact_service import DendriticMemoryArtifactService
from agent.services.dendritic_memory_capability_service import DendriticMemoryCapabilityService
from agent.services.dendritic_memory_evaluation_attestation import DendriticMemoryEvaluationAttestation
from agent.services.dendritic_memory_evaluation_service import DendriticMemoryEvaluationService
from agent.services.dendritic_memory_job_service import DendriticMemoryJobService
from agent.services.dendritic_memory_lifecycle_service import DendriticMemoryLifecycleService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_registry_service import DendriticMemoryRegistryService
from agent.services.dendritic_memory_release_gate import DendriticMemoryReleaseGate
from agent.services.dendritic_memory_runtime_gate import DendriticMemoryRuntimeGate
from agent.services.dendritic_memory_state_store import DendriticMemoryStateStore


@dataclass(frozen=True, slots=True)
class DendriticMemoryWiringStatus:
    ready: bool
    mode: str
    reason_code: str | None


def initialize_dendritic_memory(app: Flask) -> DendriticMemoryWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = DendriticMemoryWiringStatus(False, "disabled", "dendritic_hub_role_required")
    else:
        try:
            raw = json.loads(
                Path(
                    str(app.config.get("ANANTA_DENDRITIC_MEMORY_POLICY_PATH") or settings.dendritic_memory_policy_path)
                ).read_text()
            )
            raw.update(
                {
                    "enabled": _bool(
                        app.config.get("ANANTA_DENDRITIC_MEMORY_ENABLED", settings.dendritic_memory_enabled)
                    ),
                    "mode": str(
                        app.config.get("ANANTA_DENDRITIC_MEMORY_MODE", settings.dendritic_memory_mode)
                    ).strip(),
                    "runtime_enabled": _bool(
                        app.config.get(
                            "ANANTA_DENDRITIC_MEMORY_RUNTIME_ENABLED", settings.dendritic_memory_runtime_enabled
                        )
                    ),
                    "automatic_activation_enabled": _bool(
                        app.config.get(
                            "ANANTA_DENDRITIC_MEMORY_AUTOMATIC_ACTIVATION_ENABLED",
                            settings.dendritic_memory_automatic_activation_enabled,
                        )
                    ),
                }
            )
            policy = DendriticMemoryPolicy.from_mapping(raw)
            capabilities = DendriticMemoryCapabilityService(policy)
            if policy.mode == "mock":
                capabilities.report_worker(
                    {
                        "state": "available",
                        "reason_code": None,
                        "torch_version": None,
                        "safetensors_version": None,
                        "gpu_profiles": ["none"],
                        "base_models": ["mock-local-model"],
                        "architecture_versions": ["branch-projection-v1"],
                        "network_probe_performed": False,
                    }
                )
            if not app.secret_key:
                raise ValueError("dendritic_hub_secret_key_required")
            state_path = Path(str(app.config.get("ANANTA_DENDRITIC_MEMORY_STATE") or settings.dendritic_memory_state))
            signing_key = hashlib.sha256(f"dendritic-memory-v1:{app.secret_key}".encode()).digest()
            attestations = DendriticMemoryEvaluationAttestation(
                hashlib.sha256(f"dendritic-evaluation-v1:{app.secret_key}".encode()).digest()
            )
            runtime_gate = DendriticMemoryRuntimeGate(
                policy=policy,
                evaluations=attestations,
                signing_key=hashlib.sha256(f"dendritic-runtime-v1:{app.secret_key}".encode()).digest(),
            )
            jobs = DendriticMemoryJobService(
                DendriticMemoryStateStore(state_path),
                policy=policy,
                capabilities=capabilities,
                signing_key=signing_key,
            )
            registry = DendriticMemoryRegistryService(
                state_path.with_name(f"{state_path.stem}-registry.sqlite3"),
                policy=policy,
                attestations=attestations,
                runtime_gate=runtime_gate,
            )
            artifacts = DendriticMemoryArtifactService(
                str(
                    app.config.get("ANANTA_DENDRITIC_MEMORY_ARTIFACT_ROOT")
                    or settings.dendritic_memory_artifact_root
                ),
                max_pack_bytes=policy.max_pack_bytes,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            status = DendriticMemoryWiringStatus(False, "disabled", "dendritic_configuration_invalid")
        else:
            app.extensions["dendritic_memory_policy"] = policy
            app.extensions["dendritic_memory_capabilities"] = capabilities
            app.extensions["dendritic_memory_jobs"] = jobs
            app.extensions["dendritic_memory_evaluation"] = DendriticMemoryEvaluationService(attestations)
            app.extensions["dendritic_memory_registry"] = registry
            app.extensions["dendritic_memory_artifacts"] = artifacts
            app.extensions["dendritic_memory_lifecycle"] = DendriticMemoryLifecycleService(
                registry=registry, artifacts=artifacts
            )
            app.extensions["dendritic_memory_release_gate"] = DendriticMemoryReleaseGate()
            app.extensions["dendritic_memory_runtime_gate"] = runtime_gate
            status = DendriticMemoryWiringStatus(True, policy.mode, None)
    app.extensions["dendritic_memory_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["DendriticMemoryWiringStatus", "initialize_dendritic_memory"]

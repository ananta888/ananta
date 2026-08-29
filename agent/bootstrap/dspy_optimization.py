"""Hub-only composition root for optional DSPy optimization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.dspy_evaluation_bridge_service import DspyEvaluationBridgeService
from agent.services.dspy_optimization_job_service import DspyOptimizationJobService
from agent.services.dspy_optimization_policy import DspyOptimizationPolicy
from agent.services.dspy_optimization_state_store import DspyOptimizationStateStore
from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from agent.services.dspy_promotion_service import DspyPromotionService


@dataclass(frozen=True, slots=True)
class DspyOptimizationWiringStatus:
    ready: bool
    mode: str
    reason_code: str | None


def initialize_dspy_optimization(app: Flask) -> DspyOptimizationWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = DspyOptimizationWiringStatus(False, "disabled", "dspy_hub_role_required")
    else:
        try:
            raw = json.loads(
                Path(
                    str(
                        app.config.get("ANANTA_DSPY_OPTIMIZATION_POLICY_PATH") or settings.dspy_optimization_policy_path
                    )
                ).read_text()
            )
            raw["enabled"] = _bool(
                app.config.get("ANANTA_DSPY_OPTIMIZATION_ENABLED", settings.dspy_optimization_enabled)
            )
            raw["mode"] = str(app.config.get("ANANTA_DSPY_OPTIMIZATION_MODE", settings.dspy_optimization_mode)).strip()
            policy = DspyOptimizationPolicy.from_mapping(raw)
            state_path = Path(str(app.config.get("ANANTA_DSPY_OPTIMIZATION_STATE") or settings.dspy_optimization_state))
            capabilities = DspyEngineCapabilityService(policy)
            if policy.mode == "mock":
                capabilities.report_worker(
                    {
                        "state": "available",
                        "installed_version": "mock",
                        "compatibility_profile": "dspy-mock-v1",
                        "reason_code": "dspy_mock_worker_ready",
                        "network_probe_performed": False,
                    }
                )
            if not app.secret_key:
                raise ValueError("dspy_hub_secret_key_required")
            key = hashlib.sha256(f"dspy-optimization-v1:{app.secret_key}".encode()).digest()
            attestations = DspyEvaluationAttestationService(
                hashlib.sha256(f"dspy-evaluation-v1:{app.secret_key}".encode()).digest()
            )
            jobs = DspyOptimizationJobService(
                DspyOptimizationStateStore(state_path),
                policy=policy,
                capabilities=capabilities,
                signing_key=key,
            )
            promotion = DspyPromotionService(
                state_path.with_name(f"{state_path.stem}-registry.sqlite3"), attestations=attestations
            )
            artifacts = DspyProgramArtifactStore(
                str(
                    app.config.get("ANANTA_DSPY_OPTIMIZATION_ARTIFACT_ROOT") or settings.dspy_optimization_artifact_root
                )
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            status = DspyOptimizationWiringStatus(False, "disabled", "dspy_configuration_invalid")
        else:
            app.extensions["dspy_optimization_policy"] = policy
            app.extensions["dspy_engine_capabilities"] = capabilities
            app.extensions["dspy_optimization_jobs"] = jobs
            app.extensions["dspy_optimization_evaluation"] = DspyEvaluationBridgeService(attestations)
            app.extensions["dspy_optimization_promotion"] = promotion
            app.extensions["dspy_program_artifacts"] = artifacts
            status = DspyOptimizationWiringStatus(True, policy.mode, None)
    app.extensions["dspy_optimization_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["DspyOptimizationWiringStatus", "initialize_dspy_optimization"]

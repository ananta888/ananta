"""Hub-only composition root for optional full-model research training."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_evaluation_attestation import ResearchTrainingEvaluationAttestation
from agent.services.research_training_evaluation_service import ResearchTrainingEvaluationService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_policy import ResearchTrainingPolicy
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_release_gate import ResearchTrainingReleaseGate
from agent.services.research_training_run_service import ResearchTrainingRunService
from agent.services.research_training_state_store import ResearchTrainingStateStore
from ananta_contracts.research_training import STAGE_CAPABILITIES


@dataclass(frozen=True, slots=True)
class ResearchTrainingWiringStatus:
    ready: bool
    mode: str
    reason_code: str | None


def initialize_research_training(app: Flask) -> ResearchTrainingWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = ResearchTrainingWiringStatus(False, "disabled", "research_hub_role_required")
    else:
        try:
            raw = json.loads(
                Path(
                    str(
                        app.config.get("ANANTA_RESEARCH_TRAINING_POLICY_PATH")
                        or settings.research_training_policy_path
                    )
                ).read_text()
            )
            raw.update(
                {
                    "enabled": _bool(
                        app.config.get("ANANTA_RESEARCH_TRAINING_ENABLED", settings.research_training_enabled)
                    ),
                    "mode": str(
                        app.config.get("ANANTA_RESEARCH_TRAINING_MODE", settings.research_training_mode)
                    ).strip(),
                    "automatic_release_enabled": _bool(
                        app.config.get(
                            "ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED",
                            settings.research_training_automatic_release_enabled,
                        )
                    ),
                }
            )
            policy = ResearchTrainingPolicy.from_mapping(raw)
            capabilities = ResearchTrainingCapabilityService(policy)
            if policy.mode == "mock":
                capabilities.report_worker(
                    {
                        "state": "available",
                        "reason_code": None,
                        "engine_version": "deterministic-mock-v1",
                        "capabilities": sorted(set(STAGE_CAPABILITIES.values())),
                        "gpu_profiles": ["none"],
                        "network_probe_performed": False,
                    }
                )
            if not app.secret_key:
                raise ValueError("research_hub_secret_key_required")
            state_path = Path(
                str(app.config.get("ANANTA_RESEARCH_TRAINING_STATE") or settings.research_training_state)
            )
            recipes = ResearchTrainingRecipeService(policy)
            evaluation_attestations = ResearchTrainingEvaluationAttestation(
                hashlib.sha256(f"research-evaluation-v1:{app.secret_key}".encode()).digest()
            )
            evaluations = ResearchTrainingEvaluationService(evaluation_attestations)
            runs = ResearchTrainingRunService(
                ResearchTrainingStateStore(state_path),
                policy=policy,
                capabilities=capabilities,
                recipes=recipes,
                signing_key=hashlib.sha256(f"research-run-v1:{app.secret_key}".encode()).digest(),
            )
            artifacts = ResearchTrainingArtifactService(
                str(
                    app.config.get("ANANTA_RESEARCH_TRAINING_ARTIFACT_ROOT")
                    or settings.research_training_artifact_root
                ),
                max_artifact_bytes=policy.max_artifact_bytes,
            )
            lineage = ResearchTrainingLineageService(
                state_path.with_name(f"{state_path.stem}-lineage.sqlite3")
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            status = ResearchTrainingWiringStatus(False, "disabled", "research_configuration_invalid")
        else:
            app.extensions["research_training_policy"] = policy
            app.extensions["research_training_capabilities"] = capabilities
            app.extensions["research_training_recipes"] = recipes
            app.extensions["research_training_runs"] = runs
            app.extensions["research_training_artifacts"] = artifacts
            app.extensions["research_training_lineage"] = lineage
            app.extensions["research_training_evaluation"] = evaluations
            app.extensions["research_training_release_gate"] = ResearchTrainingReleaseGate(evaluations)
            status = ResearchTrainingWiringStatus(True, policy.mode, None)
    app.extensions["research_training_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["ResearchTrainingWiringStatus", "initialize_research_training"]

"""Hub-only composition root for optional full-model research training."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_completion_service import ResearchTrainingCompletionService
from agent.services.research_training_dataset_service import ResearchTrainingDatasetService
from agent.services.research_training_dispatch_service import ResearchTrainingDispatchService
from agent.services.research_training_evaluation_attestation import ResearchTrainingEvaluationAttestation
from agent.services.research_training_evaluation_service import ResearchTrainingEvaluationService
from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_policy import ResearchTrainingPolicy
from agent.services.research_training_preemption_ingress import ResearchTrainingPreemptionIngress
from agent.services.research_training_promotion_service import ResearchTrainingPromotionService
from agent.services.research_training_quality_gate import ResearchTrainingQualityGate
from agent.services.research_training_quota_service import ResearchTrainingQuotaService
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_release_gate import ResearchTrainingReleaseGate
from agent.services.research_training_result_ingress import ResearchTrainingResultIngress
from agent.services.research_training_retention_service import ResearchTrainingRetentionService
from agent.services.research_training_rollout_service import (
    ResearchTrainingRolloutPolicy,
    ResearchTrainingRolloutService,
)
from agent.services.research_training_run_service import ResearchTrainingRunService
from agent.services.research_training_safety_policy import ResearchTrainingSafetyPolicy
from agent.services.research_training_state_store import ResearchTrainingStateStore
from agent.services.research_training_sweep_service import ResearchTrainingSweepService
from agent.services.research_training_telemetry_service import ResearchTrainingTelemetryService
from agent.services.research_training_worker_registry import ResearchTrainingWorkerRegistry
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
                        app.config.get("ANANTA_RESEARCH_TRAINING_POLICY_PATH") or settings.research_training_policy_path
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
            rollout_policy = ResearchTrainingRolloutPolicy.from_mapping(
                json.loads(
                    Path(
                        str(
                            app.config.get("ANANTA_RESEARCH_TRAINING_ROLLOUT_PATH")
                            or settings.research_training_rollout_path
                        )
                    ).read_text()
                )
            )
            safety_policy = ResearchTrainingSafetyPolicy.from_mapping(
                json.loads(
                    Path(
                        str(
                            app.config.get("ANANTA_RESEARCH_TRAINING_SAFETY_PATH")
                            or settings.research_training_safety_path
                        )
                    ).read_text()
                )
            )
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
            state_path = Path(str(app.config.get("ANANTA_RESEARCH_TRAINING_STATE") or settings.research_training_state))
            artifact_root = Path(
                str(
                    app.config.get("ANANTA_RESEARCH_TRAINING_ARTIFACT_ROOT")
                    or settings.research_training_artifact_root
                )
            )
            dataset_root = Path(
                str(
                    app.config.get("ANANTA_RESEARCH_TRAINING_DATASET_ROOT")
                    or settings.research_training_dataset_root
                )
            )
            dataset_root.mkdir(parents=True, exist_ok=True)
            result_root = Path(
                str(
                    app.config.get("ANANTA_RESEARCH_TRAINING_RESULT_ROOT")
                    or settings.research_training_result_root
                )
            )
            result_root.mkdir(parents=True, exist_ok=True)
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
            evidence_registry = get_hub_evidence_registry_service()
            evidence = ResearchTrainingEvidenceService(evidence_registry)
            quota = ResearchTrainingQuotaService(
                state_path.with_name(f"{state_path.stem}-quota.sqlite3"),
                maximum_bytes_per_tenant=policy.max_storage_bytes,
            )
            assignments = ResearchTrainingAssignmentStore(
                state_path.with_name(f"{state_path.stem}-assignments.sqlite3")
            )
            artifacts = ResearchTrainingArtifactService(
                artifact_root,
                max_artifact_bytes=policy.max_artifact_bytes,
                quota=quota,
            )
            lineage = ResearchTrainingLineageService(state_path.with_name(f"{state_path.stem}-lineage.sqlite3"))
            licenses = tuple(
                item.strip()
                for item in str(
                    app.config.get("ANANTA_RESEARCH_TRAINING_ALLOWED_LICENSES")
                    or settings.research_training_allowed_licenses
                ).split(",")
                if item.strip()
            )
            datasets = ResearchTrainingDatasetService(
                dataset_root,
                evidence=evidence,
                allowed_licenses=licenses,
                maximum_dataset_bytes=policy.max_storage_bytes,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            status = ResearchTrainingWiringStatus(False, "disabled", "research_configuration_invalid")
        else:
            app.extensions["research_training_policy"] = policy
            app.extensions["research_training_safety_policy"] = safety_policy
            app.extensions["research_training_capabilities"] = capabilities
            app.extensions["research_training_recipes"] = recipes
            app.extensions["research_training_sweeps"] = ResearchTrainingSweepService(
                recipes=recipes,
                runs=runs,
                state_path=state_path.with_name(f"{state_path.stem}-sweeps.sqlite3"),
            )
            app.extensions["research_training_runs"] = runs
            app.extensions["research_training_artifacts"] = artifacts
            app.extensions["research_training_lineage"] = lineage
            app.extensions["research_training_evidence"] = evidence
            app.extensions["research_training_datasets"] = datasets
            app.extensions["research_training_quota"] = quota
            app.extensions["research_training_assignments"] = assignments
            app.extensions["research_training_retention"] = ResearchTrainingRetentionService(
                artifact_root, quota, lineage
            )
            worker_registry = ResearchTrainingWorkerRegistry()
            app.extensions["research_training_worker_registry"] = worker_registry
            app.extensions["research_training_telemetry"] = ResearchTrainingTelemetryService()
            app.extensions["research_training_quality_gate"] = ResearchTrainingQualityGate()
            result_ingress = ResearchTrainingResultIngress(
                result_root,
                evidence=evidence,
                assignments=assignments,
                artifacts=artifacts,
                lineage=lineage,
                maximum_result_bytes=policy.max_artifact_bytes,
            )
            app.extensions["research_training_result_ingress"] = result_ingress
            app.extensions["research_training_completion"] = ResearchTrainingCompletionService(
                assignments=assignments,
                ingress=result_ingress,
                runs=runs,
            )
            app.extensions["research_training_preemption_ingress"] = ResearchTrainingPreemptionIngress(
                result_root,
                assignments=assignments,
                evidence=evidence,
                artifacts=artifacts,
                lineage=lineage,
                runs=runs,
                maximum_checkpoint_bytes=safety_policy.maximum_checkpoint_bytes,
            )
            app.extensions["research_training_dispatch"] = ResearchTrainingDispatchService(
                runs=runs,
                workers=worker_registry,
                evidence=evidence,
                assignments=assignments,
                safety=safety_policy,
                quota=quota,
            )
            app.extensions["research_training_evaluation"] = evaluations
            app.extensions["research_training_release_gate"] = ResearchTrainingReleaseGate(evaluations)
            app.extensions["research_training_promotion"] = ResearchTrainingPromotionService(
                runs=runs,
                evaluations=evaluations,
                registry=evidence_registry,
            )
            app.extensions["research_training_rollout"] = ResearchTrainingRolloutService(rollout_policy)
            status = ResearchTrainingWiringStatus(True, policy.mode, None)
    app.extensions["research_training_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["ResearchTrainingWiringStatus", "initialize_research_training"]

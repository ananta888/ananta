"""Verify deterministic, source-bound release evidence for central model routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ananta_contracts.model_selection import ModelRoutingReleaseGateCheck

RELEASE_EVIDENCE_SCHEMA = "ananta.model-routing-release-evidence.v1"
REQUIRED_RELEASE_GATES = (
    "contract", "security", "frontend_unit", "e2e", "performance",
)
RELEASE_SOURCE_PATTERNS = (
    "ananta_contracts/model_selection.py",
    "agent/cli_backends/model_inventory.py",
    "agent/routes/config/providers.py",
    "agent/routes/config/settings.py",
    "agent/services/dashboard_feature_flag_service.py",
    "agent/services/model_catalog_service.py",
    "agent/services/model_inventory*.py",
    "agent/services/model_routing*.py",
    "agent/services/model_selection_service.py",
    "agent/services/openrouter_model_inventory_adapter.py",
    "frontend-angular/src/app/features/system/model-dashboard/*.ts",
    "frontend-angular/src/app/features/dashboard-foundation/dashboard-feature-flags.ts",
    "frontend-angular/tests/central-model-settings.spec.ts",
    "tests/test_cli_model_inventory.py",
    "tests/test_model_catalog*.py",
    "tests/test_model_inventory_service.py",
    "tests/test_model_routing*.py",
    "tests/test_openrouter_model_inventory_adapter.py",
    "scripts/model_routing_release_gate.py",
)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRoutingReleaseEvidenceGate(_Closed):
    passed: bool
    command: str = Field(min_length=1, max_length=2000)


class ModelRoutingReleaseEvidence(_Closed):
    schema_version: str = Field(alias="schema", pattern=r"^ananta\.model-routing-release-evidence\.v1$")
    suite_revision: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,100}$")
    source_digests: dict[str, str] = Field(min_length=1)
    gates: dict[str, ModelRoutingReleaseEvidenceGate]


def release_source_files(repo_root: Path) -> tuple[Path, ...]:
    matched: set[Path] = set()
    for pattern in RELEASE_SOURCE_PATTERNS:
        matched.update(path for path in repo_root.glob(pattern) if path.is_file())
    return tuple(sorted(matched, key=lambda path: path.relative_to(repo_root).as_posix()))


def release_source_digests(repo_root: Path) -> dict[str, str]:
    return {
        path.relative_to(repo_root).as_posix(): "sha256:" + hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in release_source_files(repo_root)
    }


class ModelRoutingReleaseEvidenceService:
    def __init__(self, *, repo_root: Path, evidence_path: Path) -> None:
        self._repo_root = repo_root.resolve()
        self._evidence_path = evidence_path.resolve()

    def checks(self) -> tuple[ModelRoutingReleaseGateCheck, ...]:
        try:
            evidence = ModelRoutingReleaseEvidence.model_validate_json(
                self._evidence_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return self._failed("model_routing_release_evidence_missing")
        except (OSError, ValidationError, json.JSONDecodeError):
            return self._failed("model_routing_release_evidence_invalid")
        current = release_source_digests(self._repo_root)
        source_fresh = current == evidence.source_digests
        checks: list[ModelRoutingReleaseGateCheck] = []
        for gate_id in REQUIRED_RELEASE_GATES:
            gate = evidence.gates.get(gate_id)
            passed = bool(source_fresh and gate is not None and gate.passed)
            if not source_fresh:
                reason = "model_routing_release_source_drift"
            elif gate is None:
                reason = "model_routing_release_gate_evidence_missing"
            elif not gate.passed:
                reason = "model_routing_release_gate_failed"
            else:
                reason = f"model_routing_release_{gate_id}_passed"
            checks.append(ModelRoutingReleaseGateCheck(
                check_id=f"release_evidence_{gate_id}",
                passed=passed,
                reason_code=reason,
            ))
        return tuple(checks)

    @staticmethod
    def _failed(reason_code: str) -> tuple[ModelRoutingReleaseGateCheck, ...]:
        return tuple(ModelRoutingReleaseGateCheck(
            check_id=f"release_evidence_{gate_id}",
            passed=False,
            reason_code=reason_code,
        ) for gate_id in REQUIRED_RELEASE_GATES)


__all__ = [
    "ModelRoutingReleaseEvidence", "ModelRoutingReleaseEvidenceGate",
    "ModelRoutingReleaseEvidenceService", "RELEASE_EVIDENCE_SCHEMA",
    "REQUIRED_RELEASE_GATES", "release_source_digests", "release_source_files",
]

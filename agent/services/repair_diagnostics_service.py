"""Repair diagnostics read-model for operator observability.

DRR-T034: Operators need visibility into repair engine readiness:
signatures, catalog, feature flags, outcome persistence, runner state,
and safety policy summary. No secrets or raw logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.deterministic_repair_path_service import (
    build_initial_failure_signature_catalog,
    get_initial_diagnosis_playbooks,
    build_initial_repair_procedure_catalog,
)
from agent.services.repair_procedure_catalog import get_catalog


# ── RepairDiagnosticsReadModel (DRR-T034) ─────────────────────────────────────

@dataclass
class RepairDiagnosticsReadModel:
    """Operator-safe repair engine diagnostics snapshot. DRR-T034.

    Never exposes raw logs, evidence, or secrets.
    """
    deterministic_repair_analysis_enabled: bool
    deterministic_repair_preview_enabled: bool
    deterministic_repair_execution_enabled: bool
    signature_count: int
    playbook_count: int
    procedure_count: int
    outcome_persistence_ready: bool
    runner_ready: bool
    last_error_code: str
    safety_policy_summary: dict[str, Any] = field(default_factory=dict)
    approval_required_classes: list[str] = field(default_factory=list)
    feature_flag_states: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "deterministic_repair_analysis_enabled": self.deterministic_repair_analysis_enabled,
            "deterministic_repair_preview_enabled": self.deterministic_repair_preview_enabled,
            "deterministic_repair_execution_enabled": self.deterministic_repair_execution_enabled,
            "signature_count": self.signature_count,
            "playbook_count": self.playbook_count,
            "procedure_count": self.procedure_count,
            "outcome_persistence_ready": self.outcome_persistence_ready,
            "runner_ready": self.runner_ready,
            "last_error_code": self.last_error_code,
            "safety_policy_summary": dict(self.safety_policy_summary),
            "approval_required_classes": list(self.approval_required_classes),
            "feature_flag_states": dict(self.feature_flag_states),
        }

    def has_secrets(self) -> bool:
        _sensitive = frozenset({"api_key", "secret", "password", "token", "credential"})
        def _scan(obj: Any) -> bool:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if any(s in str(k).lower() for s in _sensitive):
                        return True
                    if _scan(v):
                        return True
            elif isinstance(obj, (list, tuple)):
                return any(_scan(i) for i in obj)
            return False
        return _scan(self.as_dict())


def build_repair_diagnostics_read_model(
    *,
    feature_flags: dict[str, bool] | None = None,
    outcome_persistence_ready: bool = True,
    last_error_code: str = "",
) -> RepairDiagnosticsReadModel:
    """Build a safe repair diagnostics snapshot. DRR-T034."""
    flags = feature_flags or {}
    analysis_enabled = bool(flags.get("deterministic_repair_analysis_enabled", True))
    preview_enabled = bool(flags.get("deterministic_repair_preview_enabled", True))
    execution_enabled = bool(flags.get("deterministic_repair_execution_enabled", False))

    try:
        sig_catalog = build_initial_failure_signature_catalog()
        signature_count = len(sig_catalog)
    except Exception:
        signature_count = 0

    try:
        playbooks = get_initial_diagnosis_playbooks()
        playbook_count = len(playbooks)
    except Exception:
        playbook_count = 0

    try:
        catalog_entries = get_catalog()
        procedure_count = len(catalog_entries)
    except Exception:
        procedure_count = 0

    runner_ready = True
    try:
        from worker.repair.repair_procedure_runner import RepairProcedureRunner
        RepairProcedureRunner()
    except Exception:
        runner_ready = False

    safety_policy_summary = {
        "inspect_only_requires_approval": False,
        "bounded_low_risk_requires_approval": False,
        "confirm_required_requires_approval": True,
        "high_risk_requires_approval": True,
        "unknown_class_default": "deny",
    }
    approval_required_classes = ["confirm_required", "high_risk"]

    feature_flag_states = {
        "deterministic_repair_analysis_enabled": analysis_enabled,
        "deterministic_repair_preview_enabled": preview_enabled,
        "deterministic_repair_execution_enabled": execution_enabled,
    }

    return RepairDiagnosticsReadModel(
        deterministic_repair_analysis_enabled=analysis_enabled,
        deterministic_repair_preview_enabled=preview_enabled,
        deterministic_repair_execution_enabled=execution_enabled,
        signature_count=signature_count,
        playbook_count=playbook_count,
        procedure_count=procedure_count,
        outcome_persistence_ready=outcome_persistence_ready,
        runner_ready=runner_ready,
        last_error_code=last_error_code,
        safety_policy_summary=safety_policy_summary,
        approval_required_classes=approval_required_classes,
        feature_flag_states=feature_flag_states,
    )

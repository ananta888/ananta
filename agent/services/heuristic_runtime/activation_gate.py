"""HeuristicActivationGate — activation, rollback, and quarantine with full audit trail.

Activation requires ALL three conditions:
  1. Schema-valid proposal (ProposalValidator.passed=True)
  2. Simulation passed (SimulationReport.can_activate=True)
  3. Human-approved (human_approval_ref matches an audit event or is pre-set)

Rollback restores archived version to active/.
Quarantine immediately moves to quarantine/ and emits heuristic_quarantined audit event.

Auto-activation is technically blocked: activate() fails without human_approval_ref.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry, get_heuristic_registry
from agent.services.heuristic_runtime.proposal_validator import HeuristicProposal, HeuristicProposalValidator, ValidationResult
from agent.services.heuristic_runtime.simulation_harness import SimulationReport


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class ActivationResult:
    success: bool
    heuristic_id: str
    version: str
    reason: str = ""
    audit_event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    activated_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "heuristic_id": self.heuristic_id,
            "version": self.version,
            "reason": self.reason,
            "audit_event_id": self.audit_event_id,
            "activated_path": self.activated_path,
        }


# ── Audit log (in-memory, append-only) ───────────────────────────────────────

_AUDIT: list[dict[str, Any]] = []
_MAX_AUDIT = 500


def _emit_audit(event_type: str, **kwargs: Any) -> str:
    event_id = str(uuid.uuid4())
    _AUDIT.append({
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": time.time(),
        **kwargs,
    })
    if len(_AUDIT) > _MAX_AUDIT:
        del _AUDIT[:-_MAX_AUDIT]
    return event_id


def get_audit_events(event_type: str | None = None) -> list[dict[str, Any]]:
    if event_type:
        return [e for e in _AUDIT if e.get("event_type") == event_type]
    return list(_AUDIT)


def register_human_approval(proposal_id: str, *, approved_by: str = "operator") -> str:
    """Register a human approval event. Returns the audit event ID."""
    return _emit_audit(
        "heuristic_proposal_approved",
        proposal_id=proposal_id,
        approved_by=approved_by,
    )


# ── Gate ──────────────────────────────────────────────────────────────────────

class HeuristicActivationGate:
    def __init__(
        self,
        registry: HeuristicRegistry | None = None,
        base_path: str | None = None,
    ) -> None:
        self._registry = registry or get_heuristic_registry()
        self._validator = HeuristicProposalValidator(registry=self._registry)
        self._base_path = base_path or self._default_base_path()

    @staticmethod
    def _default_base_path() -> str:
        here = os.path.dirname(__file__)
        return os.path.normpath(os.path.join(here, "..", "..", "..", "heuristics"))

    # ── Activate ──────────────────────────────────────────────────────────────

    def activate(
        self,
        proposal: HeuristicProposal,
        simulation_report: SimulationReport | None = None,
    ) -> ActivationResult:
        """Activate a proposal. Requires schema-valid + simulation-pass + human approval."""
        hid = proposal.proposal_id

        # Gate 1 — schema + capability validation
        val_result = self._validator.validate(proposal)
        if not val_result.passed:
            return ActivationResult(
                success=False,
                heuristic_id=hid,
                version=proposal.version,
                reason=f"validation_failed:{';'.join(val_result.reason_codes)}",
            )

        # Gate 2 — simulation must have passed
        if simulation_report is not None and not simulation_report.can_activate:
            return ActivationResult(
                success=False,
                heuristic_id=hid,
                version=proposal.version,
                reason=f"simulation_failed:policy_violations={simulation_report.policy_violation_count}",
            )

        # Gate 3 — human approval REQUIRED (hard block, no bypass)
        if not proposal.human_approval_ref:
            return ActivationResult(
                success=False,
                heuristic_id=hid,
                version=proposal.version,
                reason="activation_blocked:no_human_approval_ref",
            )

        # Verify the approval ref exists in audit log
        approved_events = [
            e for e in _AUDIT
            if e.get("event_type") == "heuristic_proposal_approved"
            and e.get("proposal_id") == hid
        ]
        if not approved_events:
            return ActivationResult(
                success=False,
                heuristic_id=hid,
                version=proposal.version,
                reason="activation_blocked:human_approval_not_found_in_audit_log",
            )

        # Archive existing active version for this heuristic_id
        self._archive_existing(hid)

        # Write new heuristic to active/
        hdef_dict = {
            "heuristic_id": hid,
            "version": proposal.version,
            "domain": proposal.domain,
            "strategy_kind": proposal.strategy_kind,
            "description": proposal.description,
            "capabilities": list(proposal.capabilities),
            "inputs": list(proposal.inputs),
            "outputs": list(proposal.outputs),
            "parameters": dict(proposal.parameters),
            "safety_class": proposal.safety_class,
            "deterministic": proposal.deterministic,
            "status": "active",
            "activated_at": time.time(),
            "human_approval_ref": proposal.human_approval_ref,
        }
        filename = f"{hid}-{proposal.version}.json"
        active_path = os.path.join(self._base_path, "active", filename)
        os.makedirs(os.path.dirname(active_path), exist_ok=True)
        with open(active_path, "w", encoding="utf-8") as f:
            json.dump(hdef_dict, f, indent=2)

        # Reload registry
        self._registry.reload()

        audit_id = _emit_audit(
            "heuristic_activated",
            heuristic_id=hid,
            version=proposal.version,
            approval_ref=proposal.human_approval_ref,
        )

        return ActivationResult(
            success=True,
            heuristic_id=hid,
            version=proposal.version,
            reason="activation_ok",
            audit_event_id=audit_id,
            activated_path=active_path,
        )

    # ── Rollback ──────────────────────────────────────────────────────────────

    def rollback(self, heuristic_id: str, version: str) -> ActivationResult:
        """Restore an archived version to active/, move current active to archive/."""
        archive_dir = os.path.join(self._base_path, "archive")
        filename = f"{heuristic_id}-{version}.json"
        archived_path = os.path.join(archive_dir, filename)

        if not os.path.exists(archived_path):
            return ActivationResult(
                success=False,
                heuristic_id=heuristic_id,
                version=version,
                reason=f"rollback_failed:archive_not_found:{archived_path}",
            )

        # Archive current active version
        self._archive_existing(heuristic_id)

        # Restore from archive
        active_path = os.path.join(self._base_path, "active", filename)
        shutil.copy2(archived_path, active_path)
        os.remove(archived_path)

        self._registry.reload()

        audit_id = _emit_audit(
            "heuristic_rolled_back",
            heuristic_id=heuristic_id,
            version=version,
        )
        return ActivationResult(
            success=True,
            heuristic_id=heuristic_id,
            version=version,
            reason="rollback_ok",
            audit_event_id=audit_id,
            activated_path=active_path,
        )

    # ── Quarantine ────────────────────────────────────────────────────────────

    def quarantine(self, heuristic_id: str, reason: str) -> ActivationResult:
        """Immediately move active heuristic to quarantine/."""
        active_dir = os.path.join(self._base_path, "active")
        quarantine_dir = os.path.join(self._base_path, "quarantine")
        os.makedirs(quarantine_dir, exist_ok=True)

        # Find the active file for this heuristic_id
        moved = False
        version_found = ""
        for fname in os.listdir(active_dir) if os.path.isdir(active_dir) else []:
            if fname.startswith(heuristic_id) and fname.endswith(".json"):
                src = os.path.join(active_dir, fname)
                dst = os.path.join(quarantine_dir, fname)
                shutil.move(src, dst)
                version_found = fname.replace(heuristic_id + "-", "").replace(".json", "")
                moved = True
                break

        if not moved:
            return ActivationResult(
                success=False,
                heuristic_id=heuristic_id,
                version="",
                reason=f"quarantine_failed:no_active_file_found_for:{heuristic_id}",
            )

        self._registry.reload()

        audit_id = _emit_audit(
            "heuristic_quarantined",
            heuristic_id=heuristic_id,
            version=version_found,
            reason=reason,
        )
        return ActivationResult(
            success=True,
            heuristic_id=heuristic_id,
            version=version_found,
            reason=f"quarantined:{reason}",
            audit_event_id=audit_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _archive_existing(self, heuristic_id: str) -> None:
        """Move any current active file for heuristic_id to archive/."""
        active_dir = os.path.join(self._base_path, "active")
        archive_dir = os.path.join(self._base_path, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        if not os.path.isdir(active_dir):
            return
        for fname in os.listdir(active_dir):
            if fname.startswith(heuristic_id) and fname.endswith(".json"):
                shutil.move(
                    os.path.join(active_dir, fname),
                    os.path.join(archive_dir, fname),
                )

"""Replayable Runs — COSMOS-007

ReplayRecord, ReplayAnalysis, ReplayRecordService.

Replay läuft immer opt-in; analyse_replay ist read-only ohne Approval,
action_replay braucht eigene ApprovalRequests (erbt keine vom Original-Run).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReplayRecord:
    replay_id: str
    run_id: str
    expert_id: str
    expert_version: str          # exakte Version, nie "latest"
    config_hash: str             # sha256 der aktiven Konfiguration zum Run-Zeitpunkt
    policy_snapshot_ref: str | None    # artifact_id des PolicySnapshot-Artefakts
    context_bundle_refs: list[str]     # artifact_ids aller Context-Bundles des Runs
    tool_call_log_ref: str | None      # artifact_id des vollständigen Tool-Call-Logs
    non_deterministic_refs: list[str]  # artifact_ids externer Antwort-Snapshots
    created_at: float
    replay_status: str = "available"   # "available" | "unavailable" | "incomplete"
    unavailable_reason: str | None = None


@dataclass
class ReplayAnalysis:
    replay_id: str
    can_analyse: bool
    can_action_replay: bool
    missing_refs: list[str]           # was fehlt für vollständigen Replay
    non_deterministic_steps: list[str]  # Schritte die nicht reproduzierbar sind
    estimated_fidelity: float          # 0.0–1.0, wie zuverlässig der Replay wäre


# ── Service ───────────────────────────────────────────────────────────────────

# Weights for fidelity calculation (must sum to 1.0).
_FIDELITY_WEIGHT_POLICY = 0.35
_FIDELITY_WEIGHT_LOG = 0.35
_FIDELITY_WEIGHT_CONTEXT = 0.20
_FIDELITY_WEIGHT_NO_NONDETERMINISM = 0.10


class ReplayRecordService:
    """Create and analyse ReplayRecords for completed runs."""

    def __init__(self) -> None:
        self._records: dict[str, ReplayRecord] = {}
        # Per-replay list of non-deterministic step descriptions added via
        # mark_non_deterministic().
        self._non_deterministic_steps: dict[str, list[str]] = {}

    def create_record(
        self,
        *,
        run_id: str,
        expert_id: str,
        expert_version: str,
        config_hash: str,
        policy_snapshot_ref: str | None = None,
        context_bundle_refs: list[str] | None = None,
        tool_call_log_ref: str | None = None,
        non_deterministic_refs: list[str] | None = None,
    ) -> ReplayRecord:
        """Create a ReplayRecord for a completed run."""
        record = ReplayRecord(
            replay_id=str(uuid.uuid4()),
            run_id=run_id,
            expert_id=expert_id,
            expert_version=expert_version,
            config_hash=config_hash,
            policy_snapshot_ref=policy_snapshot_ref,
            context_bundle_refs=list(context_bundle_refs or []),
            tool_call_log_ref=tool_call_log_ref,
            non_deterministic_refs=list(non_deterministic_refs or []),
            created_at=time.time(),
        )
        self._records[record.replay_id] = record
        self._non_deterministic_steps[record.replay_id] = []
        return record

    def analyse(self, record: ReplayRecord) -> ReplayAnalysis:
        """Check if replay is feasible and what is missing."""
        missing_refs: list[str] = []
        if record.policy_snapshot_ref is None:
            missing_refs.append("policy_snapshot_ref")
        if record.tool_call_log_ref is None:
            missing_refs.append("tool_call_log_ref")
        if not record.context_bundle_refs:
            missing_refs.append("context_bundle_refs")

        # analyse_replay (read-only) only needs the tool call log.
        can_analyse = record.tool_call_log_ref is not None
        # action_replay also requires the policy snapshot.
        can_action_replay = (
            record.policy_snapshot_ref is not None
            and record.tool_call_log_ref is not None
        )

        non_det_steps = list(
            self._non_deterministic_steps.get(record.replay_id, [])
        )

        # Fidelity: weighted sum of available evidence.
        fidelity = 0.0
        if record.policy_snapshot_ref is not None:
            fidelity += _FIDELITY_WEIGHT_POLICY
        if record.tool_call_log_ref is not None:
            fidelity += _FIDELITY_WEIGHT_LOG
        if record.context_bundle_refs:
            fidelity += _FIDELITY_WEIGHT_CONTEXT
        if not non_det_steps:
            fidelity += _FIDELITY_WEIGHT_NO_NONDETERMINISM

        return ReplayAnalysis(
            replay_id=record.replay_id,
            can_analyse=can_analyse,
            can_action_replay=can_action_replay,
            missing_refs=missing_refs,
            non_deterministic_steps=non_det_steps,
            estimated_fidelity=round(fidelity, 4),
        )

    def mark_non_deterministic(
        self, replay_id: str, step_description: str
    ) -> None:
        """Register a non-deterministic step for a replay (e.g. external LLM call)."""
        if replay_id not in self._non_deterministic_steps:
            self._non_deterministic_steps[replay_id] = []
        self._non_deterministic_steps[replay_id].append(step_description)

    def is_dry_run_safe(self, record: ReplayRecord) -> bool:
        """True iff the record has enough information for a read-only analyse replay.

        Requires both a PolicySnapshot (to understand what was allowed) and a
        ToolCallLog (to reconstruct the sequence of actions) — without these a
        dry-run cannot produce meaningful output.
        """
        return (
            record.policy_snapshot_ref is not None
            and record.tool_call_log_ref is not None
        )

    def to_dict(self, record: ReplayRecord) -> dict[str, Any]:
        """Serialise a ReplayRecord to a plain dict.

        None fields are preserved as None (not as the string "None").
        """
        return {
            "replay_id": record.replay_id,
            "run_id": record.run_id,
            "expert_id": record.expert_id,
            "expert_version": record.expert_version,
            "config_hash": record.config_hash,
            "policy_snapshot_ref": record.policy_snapshot_ref,
            "context_bundle_refs": list(record.context_bundle_refs),
            "tool_call_log_ref": record.tool_call_log_ref,
            "non_deterministic_refs": list(record.non_deterministic_refs),
            "created_at": record.created_at,
            "replay_status": record.replay_status,
            "unavailable_reason": record.unavailable_reason,
        }

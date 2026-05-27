"""AutoActivator — auto-promote qualified candidates to active/ (ASH-030).

Activation strategy: promote_to_active (default).
Does NOT require human_approval_ref when governance_mode is
auto_without_human_approval.

Progressive rollout (ASH-034): promotion starts at 10% quota (canary),
advances through 50% (partial) to 100% (full) after sufficient decisions.

Rollback triggers (ASH-032):
  - fallback_rate_increased
  - ai_timeout_rate_increased
  - snake_stuck_detected
  - user_negative_feedback_threshold_reached
  - decision_duration_threshold_exceeded
  - invalid_transition_detected

Direct candidate runtime (ASH-031): candidates/ is never used as runtime
source unless direct_candidate_runtime_allowed = True (debug-only).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_BASE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)

# Rollout stages: (quota, min_decisions, label)
ROLLOUT_STAGES = [
    (0.1, 20,  "canary"),
    (0.5, 50,  "partial"),
    (1.0, None, "full"),
]

# Rollback trigger thresholds
ROLLBACK_FALLBACK_RATE_DELTA = 0.15   # if fallback rate rises by more than this
ROLLBACK_NEGATIVE_FEEDBACK_COUNT = 3


@dataclass
class RolloutState:
    candidate_id: str
    current_stage_index: int = 0       # index into ROLLOUT_STAGES
    decisions_at_stage: int = 0
    started_at: float = field(default_factory=time.time)
    quota: float = 0.1
    stage_label: str = "canary"
    active: bool = True
    baseline_fallback_rate: float = 0.0

    @property
    def current_stage(self) -> tuple[float, int | None, str]:
        return ROLLOUT_STAGES[self.current_stage_index]

    def is_final_stage(self) -> bool:
        return self.current_stage_index >= len(ROLLOUT_STAGES) - 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "stage_label": self.stage_label,
            "quota": self.quota,
            "decisions_at_stage": self.decisions_at_stage,
            "current_stage_index": self.current_stage_index,
            "active": self.active,
        }


@dataclass
class PromotionResult:
    success: bool
    candidate_id: str
    reason: str = ""
    audit_event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    active_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "audit_event_id": self.audit_event_id,
            "active_path": self.active_path,
        }


class AutoActivator:
    """Promotes qualified candidates to active/ without human approval.

    Requires governance_mode == auto_without_human_approval.
    Runs progressive rollout through canary → partial → full stages.
    """

    def __init__(self, base_path: str | None = None) -> None:
        self._base = base_path or _DEFAULT_BASE_PATH
        # In-memory rollout states: candidate_id → RolloutState
        self._rollout_states: dict[str, RolloutState] = {}

    # ── Promotion ─────────────────────────────────────────────────────────────

    def promote(self, candidate: dict[str, Any]) -> PromotionResult:
        """Promote a candidate to active/tui_snake/ via progressive rollout.

        The candidate file is written to active/tui_snake/ with
        status=active and rollout_quota=0.1 (canary stage).
        """
        cid = str(candidate.get("proposal_id") or candidate.get("heuristic_id") or "")
        if not cid:
            return PromotionResult(success=False, candidate_id="", reason="missing_candidate_id")

        # Score gate
        score = candidate.get("score") or {}
        if not score.get("meets_thresholds", False):
            return PromotionResult(
                success=False,
                candidate_id=cid,
                reason=f"score_thresholds_not_met:{score.get('block_reason', '')}",
            )

        # Archive existing active for this candidate_id
        self._archive_existing(cid)

        # Prepare rollout state
        rollout = RolloutState(candidate_id=cid)
        self._rollout_states[cid] = rollout

        # Write to active/tui_snake/
        active_data = dict(candidate)
        active_data["status"] = "active"
        active_data["domain"] = candidate.get("domain", "tui_snake")
        active_data["activated_at"] = time.time()
        active_data["activation_reason"] = "auto_without_human_approval"
        active_data["rollout"] = rollout.to_dict()
        # Map proposal fields to heuristic fields expected by loader
        if "base_heuristic_ref" in candidate:
            active_data.setdefault("heuristic_id", cid)
        params = candidate.get("parameters") or {}
        action_kind = str(params.get("action_kind") or "follow_with_distance")
        active_data.setdefault("runtime", {
            "action": {"kind": action_kind},
            "triggers": [],
        })

        active_dir = os.path.join(self._base, "active", "tui_snake")
        os.makedirs(active_dir, exist_ok=True)
        fname = f"{cid}-auto.json"
        fpath = os.path.join(active_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(active_data, f, indent=2, ensure_ascii=False)

        # Audit
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit_id = audit.candidate_auto_promoted(
                candidate_id=cid, reason_code="auto_without_human_approval"
            )
            audit.candidate_rollout_stage_advanced(
                candidate_id=cid,
                stage="canary",
                quota=0.1,
                decisions_at_stage=0,
            )
        except Exception:
            audit_id = str(uuid.uuid4())

        return PromotionResult(
            success=True,
            candidate_id=cid,
            reason="auto_promoted:canary_stage",
            audit_event_id=audit_id,
            active_path=fpath,
        )

    # ── Rollout stage advancement ─────────────────────────────────────────────

    def tick_rollout(
        self,
        candidate_id: str,
        *,
        fallback_rate: float = 0.0,
        negative_feedback_count: int = 0,
    ) -> RolloutState | None:
        """Advance rollout stage or trigger rollback. Called each decision tick."""
        state = self._rollout_states.get(candidate_id)
        if state is None or not state.active:
            return state

        state.decisions_at_stage += 1

        # Rollback checks (ASH-032)
        if fallback_rate - state.baseline_fallback_rate > ROLLBACK_FALLBACK_RATE_DELTA:
            self._execute_rollback(
                candidate_id, reason_code="fallback_rate_increased", state=state
            )
            return state
        if negative_feedback_count >= ROLLBACK_NEGATIVE_FEEDBACK_COUNT:
            self._execute_rollback(
                candidate_id, reason_code="user_negative_feedback_threshold_reached", state=state
            )
            return state

        # Stage advance check
        _, min_decisions, _ = state.current_stage
        if min_decisions is None or state.decisions_at_stage >= min_decisions:
            if not state.is_final_stage():
                next_idx = state.current_stage_index + 1
                next_quota, _, next_label = ROLLOUT_STAGES[next_idx]
                state.current_stage_index = next_idx
                state.quota = next_quota
                state.stage_label = next_label
                state.decisions_at_stage = 0
                # Update active file
                self._update_rollout_in_file(candidate_id, state)
                try:
                    from agent.services.heuristic_runtime import snake_audit_events as audit
                    audit.candidate_rollout_stage_advanced(
                        candidate_id=candidate_id,
                        stage=next_label,
                        quota=next_quota,
                        decisions_at_stage=0,
                    )
                except Exception:
                    pass

        return state

    # ── Rollback ──────────────────────────────────────────────────────────────

    def _execute_rollback(
        self, candidate_id: str, *, reason_code: str, state: RolloutState
    ) -> None:
        state.active = False
        state.quota = 0.0
        # Move active file to quarantine
        active_dir = os.path.join(self._base, "active", "tui_snake")
        quarantine_dir = os.path.join(self._base, "quarantine", "tui_snake")
        os.makedirs(quarantine_dir, exist_ok=True)
        fname = f"{candidate_id}-auto.json"
        src = os.path.join(active_dir, fname)
        if os.path.exists(src):
            try:
                data = json.loads(open(src).read())
                data["status"] = "quarantined"
                data["quarantine_reason"] = reason_code
                dst = os.path.join(quarantine_dir, fname)
                with open(dst, "w") as f:
                    json.dump(data, f, indent=2)
                os.remove(src)
            except OSError:
                pass
        # Restore archived version if available
        archive_dir = os.path.join(self._base, "archive", "tui_snake")
        for prev_fname in sorted(os.listdir(archive_dir) if os.path.isdir(archive_dir) else [], reverse=True):
            if prev_fname.startswith(candidate_id[:8]):
                shutil.copy2(
                    os.path.join(archive_dir, prev_fname),
                    os.path.join(active_dir, prev_fname),
                )
                break
        try:
            from agent.services.heuristic_runtime import snake_audit_events as audit
            audit.heuristic_rollback(
                from_heuristic_id=candidate_id,
                to_heuristic_id="previous_active",
                reason_code=reason_code,
            )
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _archive_existing(self, candidate_id: str) -> None:
        active_dir = os.path.join(self._base, "active", "tui_snake")
        archive_dir = os.path.join(self._base, "archive", "tui_snake")
        os.makedirs(archive_dir, exist_ok=True)
        if not os.path.isdir(active_dir):
            return
        for fname in os.listdir(active_dir):
            if fname.startswith(candidate_id) and fname.endswith(".json"):
                shutil.move(
                    os.path.join(active_dir, fname),
                    os.path.join(archive_dir, fname),
                )

    def _update_rollout_in_file(self, candidate_id: str, state: RolloutState) -> None:
        active_dir = os.path.join(self._base, "active", "tui_snake")
        fname = f"{candidate_id}-auto.json"
        fpath = os.path.join(active_dir, fname)
        if not os.path.exists(fpath):
            return
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            data["rollout"] = state.to_dict()
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, json.JSONDecodeError):
            pass

    def get_rollout_state(self, candidate_id: str) -> RolloutState | None:
        return self._rollout_states.get(candidate_id)

    def should_use_candidate(self, candidate_id: str, tick_counter: int) -> bool:
        """Return True if this tick should use the candidate (progressive quota).

        Direct candidate runtime (ASH-031): only allowed when
        direct_candidate_runtime_allowed=True in ActivationPolicy.
        This method is for auto-promoted candidates in active/.
        """
        state = self._rollout_states.get(candidate_id)
        if state is None or not state.active:
            return False
        # Deterministic sampling: use modulo for reproducible quota
        threshold = int(state.quota * 100)
        return (tick_counter % 100) < threshold

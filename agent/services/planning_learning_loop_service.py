from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from agent.db_models import PlanningTemplateCandidateDB
from agent.services.model_response_behavior_aggregation_service import get_model_response_behavior_aggregation_service
from agent.services.planning_metrics_service import get_planning_metrics_service
from agent.services.planning_model_profile_service import get_planning_model_profile_service, normalize_learning_state
from agent.services.planning_prompt_evolver_service import get_planning_prompt_evolver_service
from agent.services.planning_review_queue_service import get_planning_review_queue_service
from agent.services.repository_registry import get_repository_registry


def _sleep_with_shutdown(total_seconds: int) -> None:
    import agent.common.context

    for _ in range(max(1, int(total_seconds))):
        if agent.common.context.shutdown_requested:
            break
        time.sleep(1)


@dataclass
class LearningLoopDecision:
    ran: bool
    reason_codes: list[str] = field(default_factory=list)
    profiles_examined: int = 0
    candidates_created: int = 0
    profiles_activated: int = 0
    profiles_rolled_back: int = 0
    review_items_created: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason_codes": list(self.reason_codes),
            "profiles_examined": self.profiles_examined,
            "candidates_created": self.candidates_created,
            "profiles_activated": self.profiles_activated,
            "profiles_rolled_back": self.profiles_rolled_back,
            "review_items_created": self.review_items_created,
            "details": list(self.details),
        }


class PlanningLearningLoopService:
    def _learning_policy(self, planning_policy: dict[str, Any] | None) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        learning = policy.get("learning_loop") if isinstance(policy.get("learning_loop"), dict) else {}
        return dict(learning or {})

    def _qualifies_for_learning(self, *, group: dict[str, Any], learning: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if int(group.get("run_count") or 0) < int(learning.get("min_runs") or 8):
            reasons.append("insufficient_runs")

        trigger_signals: list[str] = []
        if float(group.get("parse_success_rate") or 0.0) < float(learning.get("min_parse_success_rate") or 0.7):
            trigger_signals.append("parse_success_rate_low")
        if float(group.get("validation_success_rate") or 0.0) < float(learning.get("min_validation_success_rate") or 0.7):
            trigger_signals.append("validation_success_rate_low")
        if float(group.get("materialization_success_rate") or 0.0) < float(learning.get("min_materialization_success_rate") or 0.6):
            trigger_signals.append("materialization_success_rate_low")
        if float(group.get("repair_rate") or 0.0) > float(learning.get("max_repair_rate") or 0.4):
            trigger_signals.append("repair_rate_high")
        if str(group.get("trend_direction") or "").strip().lower() == "degrading":
            trigger_signals.append("trend_degrading")

        if not trigger_signals:
            reasons.append("metrics_within_bounds")
            return False, reasons

        min_failures = int(learning.get("min_failures") or 3)
        if len(trigger_signals) < min_failures:
            reasons.append(f"insufficient_trigger_signals:{len(trigger_signals)}/{min_failures}")
            reasons.extend(trigger_signals)
            return False, reasons

        reasons.extend(trigger_signals)
        return int(group.get("run_count") or 0) >= int(learning.get("min_runs") or 8), reasons

    @staticmethod
    def _quality_score(group: dict[str, Any]) -> float:
        return float(group.get("quality_score") or 0.0)

    def _find_profile_groups(self, *, profile_name: str, lookback_runs: int, prompt_version: str | None = None) -> list[dict[str, Any]]:
        metrics = get_planning_metrics_service().summarize(
            behavior_profile_name=profile_name,
            prompt_version=prompt_version,
            group_by_profile=True,
            limit=lookback_runs,
        )
        groups = list(metrics.get("groups") or [])
        if not groups:
            return []
        groups.sort(key=lambda item: float(item.get("quality_score") or 0.0))
        return groups

    @staticmethod
    def _extract_prompt_version_id(profile) -> str | None:
        value = getattr(profile, "preferred_prompt_version_id", None)
        return str(value).strip() or None

    def _baseline_behavior(self, *, profile_name: str, prompt_version: str | None) -> dict[str, Any]:
        return get_model_response_behavior_aggregation_service().aggregate(
            behavior_profile_name=profile_name,
            prompt_version=prompt_version,
            limit=200,
        )

    def _activation_guard(self, *, learning: dict[str, Any], current_candidate, current_group: dict[str, Any]) -> dict[str, Any]:
        freeze_minutes = int(learning.get("freeze_minutes") or 120)
        threshold = float(learning.get("candidate_activation_threshold") or 0.75)
        rollback_min_runs = int(learning.get("rollback_min_runs") or max(3, int(learning.get("canary_window_runs") or 10)))
        payload = dict((getattr(current_candidate, "candidate_payload", {}) or {}))
        candidate_age_seconds = None
        if current_candidate is not None:
            created_at = float(payload.get("created_at") or getattr(current_candidate, "created_at", 0.0) or 0.0)
            if created_at:
                candidate_age_seconds = max(0.0, time.time() - created_at)
        freeze_active = bool(candidate_age_seconds is not None and candidate_age_seconds < freeze_minutes * 60)
        quality = self._quality_score(current_group)
        can_activate = bool(
            current_candidate is not None
            and str(getattr(current_candidate, "status", "") or "").strip().lower() == "canary"
            and not freeze_active
            and int(current_group.get("run_count") or 0) >= int(payload.get("canary_window_runs") or learning.get("canary_window_runs") or 10)
            and quality >= threshold
        )
        reason_codes: list[str] = []
        if current_candidate is None:
            reason_codes.append("no_candidate")
        if freeze_active:
            reason_codes.append("freeze_active")
        if int(current_group.get("run_count") or 0) < int(payload.get("canary_window_runs") or learning.get("canary_window_runs") or 10):
            reason_codes.append("canary_window_not_met")
        if quality < threshold:
            reason_codes.append("quality_below_threshold")
        return {
            "can_activate": can_activate,
            "reason_codes": reason_codes,
            "freeze_active": freeze_active,
            "freeze_minutes": freeze_minutes,
            "rollback_min_runs": rollback_min_runs,
            "candidate_age_seconds": candidate_age_seconds,
            "quality_score": quality,
        }

    def build_snapshot(self, *, planning_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        learning = self._learning_policy(policy)
        repos = get_repository_registry()
        profiles = list(repos.planning_model_profile_repo.get_enabled())
        candidates = list(repos.planning_template_candidate_repo.get_recent(limit=100))
        reviews = list(repos.planning_review_item_repo.get_open(limit=200))
        try:
            behavior_aggregation = get_model_response_behavior_aggregation_service().aggregate(
                limit=int(learning.get("lookback_runs") or 120),
            )
        except Exception:
            behavior_aggregation = {
                "observed_run_count": 0,
                "primary_output_shape_distribution": {},
                "parse_mode_distribution": {},
                "repair_success_distribution": {},
                "model_family_distribution": {},
                "preferred_output_shape": {"value": None, "share": 0.0, "state": "unknown"},
                "preferred_parse_mode": {"value": None, "share": 0.0, "state": "unknown"},
                "preferred_model_family": {"value": None, "share": 0.0, "state": "unknown"},
                "family_behavior_profiles": [],
            }

        profile_rows: list[dict[str, Any]] = []
        for profile in profiles:
            profile_name = str(profile.profile_name or "").strip()
            prompt_version_id = self._extract_prompt_version_id(profile)
            groups = self._find_profile_groups(
                profile_name=profile_name,
                lookback_runs=int(learning.get("lookback_runs") or 120),
                prompt_version=prompt_version_id,
            ) if profile_name else []
            current_group = groups[0] if groups else {}
            current_candidate = next(
                (
                    cand
                    for cand in candidates
                    if str((cand.candidate_payload or {}).get("profile_name") or "").strip() == profile_name
                ),
                None,
            )
            current_quality = self._quality_score(current_group) if current_group else 0.0
            activation_guard = self._activation_guard(learning=learning, current_candidate=current_candidate, current_group=current_group)
            profile_learning_state = normalize_learning_state(getattr(profile, "learning_state", None), default_state="stable")

            profile_rows.append(
                {
                    "profile_name": profile_name,
                    "provider": str(profile.provider or ""),
                    "model_name_pattern": str(profile.model_name_pattern or ""),
                    "model_family": str(profile.model_family or ""),
                    "enabled": bool(profile.enabled),
                    "active_prompt_version_id": prompt_version_id,
                    "learning_state": profile_learning_state,
                    "observed_output_shape": str(profile_learning_state.get("observed_output_shape") or profile_learning_state.get("observed_output_format") or ""),
                    "observed_output_format": str(profile_learning_state.get("observed_output_format") or ""),
                    "preferred_output_format": str((behavior_aggregation.get("preferred_output_format") or {}).get("value") or ""),
                    "current_quality_score": current_quality,
                    "trend_direction": str(current_group.get("trend_direction") or ""),
                    "sample_size_is_small": bool(current_group.get("sample_size_is_small")),
                    "current_candidate": {
                        "id": str(getattr(current_candidate, "id", "") or ""),
                        "status": str(getattr(current_candidate, "status", "") or ""),
                        "prompt_version_id": str((getattr(current_candidate, "candidate_payload", {}) or {}).get("new_prompt_version_id") or ""),
                        "current_prompt_version_id": str((getattr(current_candidate, "candidate_payload", {}) or {}).get("current_prompt_version_id") or ""),
                        "candidate_state": str((getattr(current_candidate, "candidate_payload", {}) or {}).get("candidate_state") or ""),
                        "candidate_age_seconds": activation_guard.get("candidate_age_seconds"),
                    } if current_candidate is not None else None,
                    "freeze": {
                        "enabled": bool(learning.get("enabled", False)),
                        "active": bool(activation_guard.get("freeze_active")),
                        "freeze_minutes": int(activation_guard.get("freeze_minutes") or 120),
                    },
                    "activation_policy": {
                        "candidate_activation_threshold": float(learning.get("candidate_activation_threshold") or 0.75),
                        "rollback_threshold": float(learning.get("rollback_threshold") or 0.55),
                        "canary_window_runs": int(learning.get("canary_window_runs") or 10),
                        "rollback_min_runs": int(activation_guard.get("rollback_min_runs") or 3),
                    },
                    "guard": activation_guard,
                    "metrics": current_group,
                }
            )

        return {
            "enabled": bool(learning.get("enabled", False)),
            "policy": learning,
            "profiles": profile_rows,
            "candidate_count": len(candidates),
            "review_item_count": len(reviews),
            "behavior_aggregation": behavior_aggregation,
        }

    @staticmethod
    def _learning_state_payload(
        *,
        state: str,
        source: str,
        observed_output_format: str | None = None,
        observed_output_shape: str | None = None,
        observed_model_family: str | None = None,
        prompt_version_id: str | None = None,
        sample_size: int | None = None,
        reason_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        observed_shape = str(observed_output_shape or observed_output_format or "").strip() or None
        observed_format = str(observed_output_format or observed_output_shape or "").strip() or None
        return normalize_learning_state(
            {
                "state": state,
                "source": source,
                "observed_output_format": observed_format,
                "observed_output_shape": observed_shape,
                "observed_model_family": observed_model_family,
                "prompt_version_id": prompt_version_id,
                "sample_size": sample_size,
                "reason_codes": reason_codes or [],
                "updated_at": time.time(),
            },
            default_state="stable",
        )

    def _set_profile_learning_state(
        self,
        *,
        profile,
        state: str,
        source: str,
        observed_output_format: str | None = None,
        observed_output_shape: str | None = None,
        observed_model_family: str | None = None,
        prompt_version_id: str | None = None,
        sample_size: int | None = None,
        reason_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        learning_state = self._learning_state_payload(
            state=state,
            source=source,
            observed_output_format=observed_output_format,
            observed_output_shape=observed_output_shape,
            observed_model_family=observed_model_family,
            prompt_version_id=prompt_version_id,
            sample_size=sample_size,
            reason_codes=reason_codes,
        )
        get_planning_model_profile_service().update_learning_state(
            profile,
            state=state,
            source=source,
            observed_output_format=observed_output_format,
            observed_output_shape=observed_output_shape,
            observed_model_family=observed_model_family,
            prompt_version_id=prompt_version_id,
            sample_size=sample_size,
            reason_codes=reason_codes or [],
        )
        return learning_state

    def _create_candidate(
        self,
        *,
        profile,
        trigger_run,
        group: dict[str, Any],
        learning: dict[str, Any],
        reason_codes: list[str],
    ) -> dict[str, Any]:
        repos = get_repository_registry()
        prompt_evolver = get_planning_prompt_evolver_service()
        previous_prompt_version_id = self._extract_prompt_version_id(profile)
        auto_activate_requested = bool(learning.get("auto_activate", False))
        evolved = prompt_evolver.evolve_from_run(
            run=trigger_run,
            planning_policy={"planner_prompt_evolution": dict(learning or {}), "preferred_output_format": "json"},
            activate_profile=False,
            enabled=False,
            output_shape=str((trigger_run.mode_data or {}).get("__output_shape__") or ""),
            parse_mode=str(getattr(trigger_run, "parse_mode", "") or ""),
            model_family=str(profile.model_family or profile.model_name_pattern or ""),
        )
        if not evolved.get("evolved"):
            return {"created": False, "reason": evolved.get("reason", "evolution_skipped")}

        candidate_payload = {
            "profile_name": str(profile.profile_name or ""),
            "provider": str(profile.provider or ""),
            "model_name": str(profile.model_name_pattern or profile.model_family or ""),
            "current_prompt_version_id": previous_prompt_version_id,
            "new_prompt_version_id": str(evolved["new_prompt_version_id"]),
            "new_prompt_version": str(evolved["new_prompt_version"]),
            "trigger_run_id": str(trigger_run.id or ""),
            "reason_codes": list(reason_codes),
            "baseline_metrics": dict(group or {}),
            "behavior_baseline": self._baseline_behavior(profile_name=str(profile.profile_name or ""), prompt_version=previous_prompt_version_id),
            "canary_window_runs": int(learning.get("canary_window_runs") or 10),
            "freeze_minutes": int(learning.get("freeze_minutes") or 120),
            "created_at": time.time(),
            "candidate_state": "canary" if auto_activate_requested else "proposed",
            "auto_activate_requested": auto_activate_requested,
        }
        candidate = repos.planning_template_candidate_repo.save(
            PlanningTemplateCandidateDB(
                source_run_id=str(trigger_run.id or ""),
                goal_type=str((trigger_run.mode_data or {}).get("__intent__", {}).get("goal_type") or trigger_run.mode or "generic"),
                mode=str(trigger_run.mode or "generic"),
                candidate_payload=candidate_payload,
                confidence="high" if float(group.get("quality_score") or 0.0) < 0.5 else "medium",
                status="canary" if auto_activate_requested else "proposed",
            )
        )
        self._set_profile_learning_state(
            profile=profile,
            state="candidate",
            source="planning_learning_loop",
            observed_output_format=str((trigger_run.mode_data or {}).get("__output_shape__") or ""),
            observed_output_shape=str((trigger_run.mode_data or {}).get("__output_shape__") or ""),
            observed_model_family=str(profile.model_family or profile.model_name_pattern or ""),
            prompt_version_id=str(evolved["new_prompt_version_id"]),
            sample_size=int(group.get("run_count") or 0),
            reason_codes=list(reason_codes),
        )
        return {
            "created": True,
            "candidate_id": str(candidate.id),
            "activated": False,
            "profile_name": str(profile.profile_name or ""),
            "new_prompt_version_id": str(evolved["new_prompt_version_id"]),
            "new_prompt_version": str(evolved["new_prompt_version"]),
            "status": "canary" if auto_activate_requested else "proposed",
        }

    def _maybe_rollback_candidate(
        self,
        *,
        profile,
        active_candidate,
        learning: dict[str, Any],
        current_group: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        repos = get_repository_registry()
        payload = dict(active_candidate.candidate_payload or {})
        previous_prompt_version_id = str(payload.get("current_prompt_version_id") or "").strip()
        if not previous_prompt_version_id:
            return False, {"reason": "missing_previous_prompt_version"}

        quality = self._quality_score(current_group)
        if quality >= float(learning.get("rollback_threshold") or 0.55):
            return False, {"reason": "rollback_not_needed", "quality_score": quality}
        rollback_min_runs = int(learning.get("rollback_min_runs") or max(3, int(learning.get("canary_window_runs") or 10)))
        if int(current_group.get("run_count") or 0) < rollback_min_runs:
            return False, {
                "reason": "rollback_window_not_met",
                "required": rollback_min_runs,
                "current": int(current_group.get("run_count") or 0),
                "quality_score": quality,
            }

        previous_version = repos.planning_prompt_version_repo.get_by_id(previous_prompt_version_id)
        if previous_version is None:
            return False, {"reason": "previous_prompt_version_missing"}

        current_version_id = str(payload.get("new_prompt_version_id") or "").strip()
        if current_version_id:
            current_version = repos.planning_prompt_version_repo.get_by_id(current_version_id)
            if current_version is not None:
                current_version.enabled = False
                repos.planning_prompt_version_repo.save(current_version)

        previous_version.enabled = True
        repos.planning_prompt_version_repo.save(previous_version)
        profile.preferred_prompt_version_id = previous_prompt_version_id
        repos.planning_model_profile_repo.save(profile)
        self._set_profile_learning_state(
            profile=profile,
            state="degraded",
            source="planning_learning_loop",
            observed_output_format=str((current_group.get("preferred_output_shape") or {}).get("value") or ""),
            observed_output_shape=str((current_group.get("preferred_output_shape") or {}).get("value") or ""),
            observed_model_family=str(profile.model_family or profile.model_name_pattern or ""),
            prompt_version_id=previous_prompt_version_id,
            sample_size=int(current_group.get("run_count") or 0),
            reason_codes=["rollback", "quality_below_threshold"],
        )
        active_candidate.status = "rolled_back"
        active_candidate.candidate_payload = {**payload, "rolled_back_at": time.time(), "candidate_state": "rolled_back", "current_quality_score": quality}
        repos.planning_template_candidate_repo.save(active_candidate)
        return True, {
            "rolled_back_to": previous_prompt_version_id,
            "quality_score": quality,
        }

    def _maybe_promote_canary(
        self,
        *,
        profile,
        active_candidate,
        learning: dict[str, Any],
        current_group: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        repos = get_repository_registry()
        payload = dict(active_candidate.candidate_payload or {})
        canary_window_runs = int(payload.get("canary_window_runs") or learning.get("canary_window_runs") or 10)
        if int(current_group.get("run_count") or 0) < canary_window_runs:
            return False, {"reason": "canary_window_not_met", "required": canary_window_runs}

        quality = self._quality_score(current_group)
        if quality < float(learning.get("candidate_activation_threshold") or 0.75):
            return False, {"reason": "quality_below_activation_threshold", "quality_score": quality}

        active_candidate.status = "activated"
        active_candidate.candidate_payload = {**payload, "activated_at": time.time(), "candidate_state": "activated", "current_quality_score": quality}
        repos.planning_template_candidate_repo.save(active_candidate)
        self._set_profile_learning_state(
            profile=profile,
            state="stable",
            source="planning_learning_loop",
            observed_output_format=str((current_group.get("preferred_output_shape") or {}).get("value") or ""),
            observed_output_shape=str((current_group.get("preferred_output_shape") or {}).get("value") or ""),
            observed_model_family=str(profile.model_family or profile.model_name_pattern or ""),
            prompt_version_id=str(profile.preferred_prompt_version_id or ""),
            sample_size=int(current_group.get("run_count") or 0),
            reason_codes=["canary_activated"],
        )
        return True, {
            "activated_prompt_version_id": str(profile.preferred_prompt_version_id or ""),
            "quality_score": quality,
        }

    def run_once(self, *, planning_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        learning = self._learning_policy(policy)
        decision = LearningLoopDecision(ran=True)
        if not bool(learning.get("enabled", False)):
            decision.ran = False
            decision.reason_codes.append("disabled")
            return decision.as_dict()

        repos = get_repository_registry()
        profiles = list(repos.planning_model_profile_repo.get_enabled())
        if not profiles:
            decision.reason_codes.append("no_enabled_profiles")
            return decision.as_dict()

        recent_candidates = list(repos.planning_template_candidate_repo.get_recent(limit=100))
        open_reviews = list(repos.planning_review_item_repo.get_open(limit=200))
        decision.profiles_examined = len(profiles)

        for profile in profiles:
            profile_name = str(profile.profile_name or "").strip()
            if not profile_name:
                continue

            active_prompt_version_id = self._extract_prompt_version_id(profile)
            groups = self._find_profile_groups(
                profile_name=profile_name,
                lookback_runs=int(learning.get("lookback_runs") or 120),
                prompt_version=active_prompt_version_id,
            )
            if not groups and active_prompt_version_id:
                # Fallback: version string vs UUID mismatch between profile and runs.
                # For qualification, include all runs for this profile regardless of prompt version.
                groups = self._find_profile_groups(
                    profile_name=profile_name,
                    lookback_runs=int(learning.get("lookback_runs") or 120),
                    prompt_version=None,
                )
            if not groups:
                decision.details.append({"profile_name": profile_name, "reason": "no_recent_runs"})
                continue

            worst_group = groups[0]
            qualifies, reason_codes = self._qualifies_for_learning(group=worst_group, learning=learning)
            active_candidate = next(
                (
                    cand
                    for cand in recent_candidates
                    if str(cand.status or "").strip().lower() in {"canary", "proposed"}
                    and str((cand.candidate_payload or {}).get("profile_name") or "").strip() == profile_name
                ),
                None,
            )
            trigger_run = next(
                (
                    run
                    for run in repos.planning_run_repo.get_recent(limit=max(20, int(learning.get("lookback_runs") or 120)))
                    if str(run.planning_profile or "").strip() == profile_name
                ),
                None,
            )
            if active_candidate is not None and active_candidate.status == "canary" and str(active_candidate.source_run_id or ""):
                promoted, promotion_details = self._maybe_promote_canary(
                    profile=profile,
                    active_candidate=active_candidate,
                    learning=learning,
                    current_group=worst_group,
                )
                if promoted:
                    decision.profiles_activated += 1
                    decision.details.append({"profile_name": profile_name, "action": "activated", **promotion_details})
                    continue
                rollbacked, rollback_details = self._maybe_rollback_candidate(
                    profile=profile,
                    active_candidate=active_candidate,
                    learning=learning,
                    current_group=worst_group,
                )
                if rollbacked:
                    decision.profiles_rolled_back += 1
                    decision.details.append({"profile_name": profile_name, "action": "rolled_back", **rollback_details})
                    continue

            if active_candidate is not None:
                self._set_profile_learning_state(
                    profile=profile,
                    state="candidate",
                    source="planning_learning_loop",
                    observed_output_format=str((worst_group.get("preferred_output_shape") or {}).get("value") or ""),
                    observed_output_shape=str((worst_group.get("preferred_output_shape") or {}).get("value") or ""),
                    observed_model_family=str(profile.model_family or profile.model_name_pattern or ""),
                    prompt_version_id=str((active_candidate.candidate_payload or {}).get("new_prompt_version_id") or active_prompt_version_id or ""),
                    sample_size=int(worst_group.get("run_count") or 0),
                    reason_codes=["candidate_pending"],
                )
                decision.details.append(
                    {
                        "profile_name": profile_name,
                        "action": "candidate_pending",
                        "candidate_status": str(active_candidate.status or ""),
                        "candidate_id": str(active_candidate.id or ""),
                    }
                )
                continue

            if trigger_run is None:
                decision.details.append({"profile_name": profile_name, "action": "skip", "reason": "trigger_run_missing"})
                continue

            if not qualifies:
                decision.details.append({"profile_name": profile_name, "action": "skip", "reason_codes": reason_codes, "quality_score": worst_group.get("quality_score")})
                continue

            if bool(learning.get("require_review_before_activate", True)):
                matching_reviews = [review for review in open_reviews if str(review.planning_run_id or "") == str(trigger_run.id or "")]
                if matching_reviews:
                    decision.details.append({"profile_name": profile_name, "action": "await_review", "open_review_count": len(matching_reviews)})
                    continue

            freeze_minutes = int(learning.get("freeze_minutes") or 120)
            last_candidate = next(
                (
                    cand for cand in recent_candidates
                    if str((cand.candidate_payload or {}).get("profile_name") or "").strip() == profile_name
                ),
                None,
            )
            if last_candidate is not None:
                created_at = float((last_candidate.candidate_payload or {}).get("created_at") or last_candidate.created_at or 0.0)
                if time.time() - created_at < freeze_minutes * 60:
                    decision.details.append({"profile_name": profile_name, "action": "freeze_active", "freeze_minutes": freeze_minutes})
                    continue

            if not bool(profile.enabled):
                decision.details.append({"profile_name": profile_name, "action": "skip", "reason": "profile_disabled"})
                continue

            if int(worst_group.get("run_count") or 0) <= 0:
                continue

            if float(worst_group.get("quality_score") or 0.0) >= float(learning.get("candidate_activation_threshold") or 0.75):
                self._set_profile_learning_state(
                    profile=profile,
                    state="stable",
                    source="planning_learning_loop",
                    observed_output_format=str((worst_group.get("preferred_output_shape") or {}).get("value") or ""),
                    observed_output_shape=str((worst_group.get("preferred_output_shape") or {}).get("value") or ""),
                    observed_model_family=str(profile.model_family or profile.model_name_pattern or ""),
                    prompt_version_id=active_prompt_version_id,
                    sample_size=int(worst_group.get("run_count") or 0),
                    reason_codes=["quality_above_threshold"],
                )
                decision.details.append({"profile_name": profile_name, "action": "skip", "reason": "quality_above_threshold"})
                continue

            result = self._create_candidate(
                profile=profile,
                trigger_run=trigger_run,
                group=worst_group,
                learning=learning,
                reason_codes=reason_codes,
            )
            if not result.get("created"):
                decision.details.append({"profile_name": profile_name, "action": "candidate_failed", "reason": result.get("reason")})
                continue

            decision.candidates_created += 1
            if bool(result.get("activated")):
                decision.profiles_activated += 1
            review_items = get_planning_review_queue_service().evaluate_run_for_review(trigger_run)
            decision.review_items_created += len(review_items)
            decision.details.append({
                "profile_name": profile_name,
                "action": "candidate_created",
                "candidate_status": result.get("status"),
                "new_prompt_version_id": result.get("new_prompt_version_id"),
                "reason_codes": reason_codes,
                "quality_score": worst_group.get("quality_score"),
                "active_prompt_version_id": active_prompt_version_id,
            })

        return decision.as_dict()


_SERVICE = PlanningLearningLoopService()


def get_planning_learning_loop_service() -> PlanningLearningLoopService:
    return _SERVICE


def start_planning_learning_loop_thread(app):
    def run_loop():
        import agent.common.context

        logging.info("Planning-Learning-Loop gestartet.")
        while not agent.common.context.shutdown_requested:
            try:
                policy = ((app.config.get("AGENT_CONFIG") or {}).get("planning_policy") or {})
                result = get_planning_learning_loop_service().run_once(planning_policy=policy)
                if result.get("ran"):
                    logging.info(
                        "Planning-Learning-Loop: candidates=%s activated=%s rolled_back=%s",
                        result.get("candidates_created"),
                        result.get("profiles_activated"),
                        result.get("profiles_rolled_back"),
                    )
            except Exception as exc:
                logging.warning("Planning-Learning-Loop Fehler: %s", exc)
            _sleep_with_shutdown(int((((app.config.get("AGENT_CONFIG") or {}).get("planning_policy") or {}).get("learning_loop") or {}).get("interval_seconds") or 900))
        logging.info("Planning-Learning-Loop beendet.")

    t = threading.Thread(target=run_loop, daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()

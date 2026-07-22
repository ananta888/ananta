"""Fail-closed evaluator for the all-TURN worst-case capacity profile."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class SfuAllTurnGateError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SfuAllTurnGateResult:
    status: str
    reason_codes: tuple[str, ...]
    measured_worst_case_receivers: int
    safe_receiver_limit: int
    source_refs: tuple[str, ...]
    run_refs: tuple[str, ...]
    artifact_sha256: str


class SfuAllTurnCapacityGate:
    """Evaluates real repeat samples; missing or ungrounded data is ``no_go``."""

    _SOURCE = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    _RUN = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    _DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

    def __init__(self, *, control_observer: SfuBroadcastControlObservationPort | None = None) -> None:
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("gate_decision")
    def evaluate(self, profile: Mapping[str, Any], report: Mapping[str, Any]) -> SfuAllTurnGateResult:
        reasons: list[str] = []
        if profile.get("activation_status") not in {"candidate", "approved"}:
            reasons.append("all_turn_profile_not_activated")
        source_refs = tuple(report.get("source_refs", ()))
        run_refs = tuple(report.get("run_refs", ()))
        artifact_digest = str(report.get("artifact_sha256", ""))
        if not source_refs or any(not self._SOURCE.fullmatch(str(value)) for value in source_refs):
            reasons.append("all_turn_source_evidence_missing")
        if not run_refs or any(not self._RUN.fullmatch(str(value)) for value in run_refs):
            reasons.append("all_turn_run_evidence_missing")
        if not self._DIGEST.fullmatch(artifact_digest):
            reasons.append("all_turn_artifact_digest_missing")
        if report.get("profile_id") != profile.get("profile_id"):
            reasons.append("all_turn_profile_mismatch")

        scenario_ids = {str(value["scenario_id"]) for value in profile.get("scenario_matrix", ())}
        grouped: dict[str, list[Mapping[str, Any]]] = {scenario_id: [] for scenario_id in scenario_ids}
        for result in report.get("scenario_results", ()):
            scenario_id = str(result.get("scenario_id", ""))
            if scenario_id not in grouped:
                reasons.append("all_turn_unexpected_scenario")
                continue
            grouped[scenario_id].append(result)
        if any(not values for values in grouped.values()):
            reasons.append("all_turn_scenario_coverage_incomplete")

        run_policy = profile["run_policy"]
        gate_policy = profile["gate_policy"]
        required_repeats = int(run_policy["repeats"])
        receiver_sweep = tuple(sorted(int(value) for value in profile["topology"]["receiver_sweep"]))
        if set(report.get("metric_names", ())) != set(profile["required_metrics"]):
            reasons.append("all_turn_required_metrics_incomplete")
        if float(report.get("confidence", 0)) != float(run_policy["confidence"]):
            reasons.append("all_turn_confidence_mismatch")
        scenario_capacities: list[int] = []
        for scenario_id, results in grouped.items():
            results_by_load = {int(value.get("receiver_count", 0)): value for value in results}
            if set(results_by_load) != set(receiver_sweep) or len(results_by_load) != len(results):
                reasons.append("all_turn_load_coverage_incomplete")
            passing_by_load: dict[int, bool] = {}
            for result in results:
                receiver_count = int(result.get("receiver_count", 0))
                repeats = result.get("repeats", ())
                if len(repeats) != required_repeats:
                    reasons.append("all_turn_repeat_count_invalid")
                    continue
                metadata_matches = (
                    int(result.get("warmup_seconds", -1)) == int(run_policy["warmup_seconds"])
                    and int(result.get("duration_seconds", -1)) == int(run_policy["duration_seconds"])
                    and int(result.get("random_seed", -1)) == int(run_policy["random_seed"])
                )
                if not metadata_matches:
                    reasons.append("all_turn_run_policy_mismatch")
                passing_by_load[receiver_count] = metadata_matches and self._load_passes(
                    receiver_count, repeats, gate_policy
                )
                stable_counts = [int(sample.get("stable_receivers", 0)) for sample in repeats]
                if stable_counts and statistics.mean(stable_counts) > 0:
                    coefficient = statistics.pstdev(stable_counts) / statistics.mean(stable_counts)
                    if coefficient > float(run_policy["max_coefficient_of_variation"]):
                        reasons.append("all_turn_variance_exceeded")
            capacity = 0
            for receiver_count in receiver_sweep:
                if not passing_by_load.get(receiver_count, False):
                    break
                capacity = receiver_count
            if capacity == 0:
                reasons.append("all_turn_no_passing_load")
            else:
                scenario_capacities.append(capacity)

        measured = min(scenario_capacities) if len(scenario_capacities) == len(grouped) and grouped else 0
        safe_limit = math.floor(measured * (1.0 - float(gate_policy["reserve_fraction"])))
        configured_limit = int(report.get("configured_admission_receiver_limit", 0))
        if safe_limit < 1:
            reasons.append("all_turn_safe_capacity_zero")
        if configured_limit < 1 or configured_limit > safe_limit:
            reasons.append("all_turn_admission_limit_unsafe")
        return SfuAllTurnGateResult(
            status="approved" if not reasons else "no_go",
            reason_codes=tuple(sorted(set(reasons))),
            measured_worst_case_receivers=measured,
            safe_receiver_limit=safe_limit,
            source_refs=tuple(str(value) for value in source_refs),
            run_refs=tuple(str(value) for value in run_refs),
            artifact_sha256=artifact_digest,
        )

    @staticmethod
    def _load_passes(
        receiver_count: int,
        repeats: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
    ) -> bool:
        if receiver_count < 1:
            return False
        for sample in repeats:
            if (
                int(sample.get("stable_receivers", 0)) != receiver_count
                or float(sample.get("join_success_ratio", 0)) < float(policy["minimum_success_ratio"])
                or float(sample.get("packet_loss_percent", math.inf)) > float(policy["maximum_packet_loss_percent"])
                or float(sample.get("p95_jitter_ms", math.inf)) > float(policy["maximum_p95_jitter_ms"])
                or float(sample.get("turn_cpu_percent", math.inf)) > float(policy["maximum_turn_cpu_percent"])
                or float(sample.get("turn_memory_percent", math.inf)) > float(policy["maximum_turn_memory_percent"])
                or float(sample.get("turn_bandwidth_utilization_percent", math.inf))
                > float(policy["maximum_turn_bandwidth_utilization_percent"])
                or float(sample.get("sfu_cpu_percent", math.inf)) > float(policy["maximum_sfu_cpu_percent"])
                or float(sample.get("sfu_memory_percent", math.inf)) > float(policy["maximum_sfu_memory_percent"])
                or int(sample.get("nonrelay_candidate_count", 1)) != 0
                or int(sample.get("publisher_relay_bytes", 0)) <= 0
                or int(sample.get("receiver_legs_with_relay_bytes", 0)) != receiver_count
                or not bool(sample.get("cleanup_complete", False))
            ):
                return False
        return True

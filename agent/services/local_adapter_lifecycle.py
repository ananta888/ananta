"""Deterministic Hub policy for Needle and LFM SFT-LoRA candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AdapterGateEvidence:
    candidate_id: str
    target: str
    dataset_sha256: str
    golden_set_sha256: str
    json_validity: float
    known_tool_rate: float
    required_fields_rate: float
    argument_type_rate: float
    selection_accuracy: float
    baseline_selection_accuracy: float
    argument_match: float
    baseline_argument_match: float
    deterministic: bool
    safety_passed: bool
    latency_p95_ms: float
    latency_limit_ms: float
    memory_peak_bytes: int
    memory_limit_bytes: int
    slice_regressions: Mapping[str, float]
    max_slice_regression: float
    shadow_examples: int
    minimum_shadow_examples: int
    shadow_unsafe_actions: int
    canary_examples: int
    minimum_canary_examples: int
    canary_error_rate: float
    maximum_canary_error_rate: float
    confidence_calibrated: bool


@dataclass(frozen=True)
class AdapterLifecycleDecision:
    promote: bool
    reason_codes: tuple[str, ...]


class LocalAdapterPromotionPolicy:
    """Machine-only release gate; candidates are never approved by training."""

    def evaluate(self, evidence: AdapterGateEvidence) -> AdapterLifecycleDecision:
        reasons: list[str] = []
        if evidence.target not in {"needle2", "lfm2.5-2.6b-agentic"}:
            reasons.append("unsupported_adapter_target")
        if len(evidence.dataset_sha256) != 64 or len(evidence.golden_set_sha256) != 64:
            reasons.append("immutable_dataset_evidence_missing")
        perfect = {
            "json_validity_regression": evidence.json_validity,
            "unknown_tool_regression": evidence.known_tool_rate,
            "required_fields_regression": evidence.required_fields_rate,
            "argument_type_regression": evidence.argument_type_rate,
        }
        reasons.extend(code for code, value in perfect.items() if value != 1.0)
        if evidence.selection_accuracy < evidence.baseline_selection_accuracy:
            reasons.append("selection_accuracy_regression")
        if evidence.argument_match < evidence.baseline_argument_match:
            reasons.append("argument_match_regression")
        if not evidence.deterministic:
            reasons.append("determinism_gate_failed")
        if not evidence.safety_passed:
            reasons.append("safety_gate_failed")
        if evidence.latency_p95_ms > evidence.latency_limit_ms:
            reasons.append("latency_gate_failed")
        if evidence.memory_peak_bytes > evidence.memory_limit_bytes:
            reasons.append("memory_gate_failed")
        if any(value > evidence.max_slice_regression for value in evidence.slice_regressions.values()):
            reasons.append("slice_regression_gate_failed")
        if evidence.shadow_examples < evidence.minimum_shadow_examples:
            reasons.append("shadow_sample_gate_failed")
        if evidence.shadow_unsafe_actions:
            reasons.append("shadow_safety_gate_failed")
        if evidence.canary_examples < evidence.minimum_canary_examples:
            reasons.append("canary_sample_gate_failed")
        if evidence.canary_error_rate > evidence.maximum_canary_error_rate:
            reasons.append("canary_error_gate_failed")
        # Needle's confidence head is not LoRA-trained.  LFM has no trusted
        # native tool confidence either, so both require external calibration.
        if not evidence.confidence_calibrated:
            reasons.append("external_confidence_calibration_required")
        return AdapterLifecycleDecision(not reasons, tuple(reasons))


@dataclass(frozen=True)
class LiveAdapterSignals:
    schema_errors: int = 0
    unknown_tools: int = 0
    tool_error_rate: float = 0.0
    maximum_tool_error_rate: float = 0.0
    latency_regression: float = 0.0
    maximum_latency_regression: float = 0.0
    crashes_or_oom: int = 0
    safety_violations: int = 0


class LocalAdapterRollbackPolicy:
    def evaluate(self, signals: LiveAdapterSignals) -> AdapterLifecycleDecision:
        reasons: list[str] = []
        if signals.schema_errors:
            reasons.append("live_schema_error")
        if signals.unknown_tools:
            reasons.append("live_unknown_tool")
        if signals.tool_error_rate > signals.maximum_tool_error_rate:
            reasons.append("live_tool_error_regression")
        if signals.latency_regression > signals.maximum_latency_regression:
            reasons.append("live_latency_regression")
        if signals.crashes_or_oom:
            reasons.append("live_runtime_failure")
        if signals.safety_violations:
            reasons.append("live_safety_violation")
        # Here promote=True means retain the candidate; False means atomically
        # restore the previous registry version and restart the runtime.
        return AdapterLifecycleDecision(not reasons, tuple(reasons))

"""Deterministic Hub policy for Needle and LFM SFT-LoRA candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Mapping

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


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
    known_arguments_rate: float
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
    shadow_match_rate: float
    minimum_shadow_match_rate: float
    shadow_unsafe_actions: int
    canary_examples: int
    minimum_canary_examples: int
    canary_error_rate: float
    maximum_canary_error_rate: float
    canary_accuracy: float
    minimum_canary_accuracy: float
    canary_escalation_rate: float
    maximum_canary_escalation_rate: float
    canary_latency_p95_ms: float
    canary_latency_limit_ms: float
    confidence_calibrated: bool
    evaluation_seed: int

    def __post_init__(self) -> None:
        if not str(self.candidate_id).strip() or not str(self.target).strip():
            raise ValueError("local_adapter_gate_identity_invalid")
        _sha256(self.dataset_sha256)
        _sha256(self.golden_set_sha256)
        for name in (
            "json_validity",
            "known_tool_rate",
            "required_fields_rate",
            "argument_type_rate",
            "known_arguments_rate",
            "selection_accuracy",
            "baseline_selection_accuracy",
            "argument_match",
            "baseline_argument_match",
            "max_slice_regression",
            "canary_error_rate",
            "maximum_canary_error_rate",
            "shadow_match_rate",
            "minimum_shadow_match_rate",
            "canary_accuracy",
            "minimum_canary_accuracy",
            "canary_escalation_rate",
            "maximum_canary_escalation_rate",
        ):
            _rate(getattr(self, name), name)
        _nonnegative_float(self.latency_p95_ms, "latency_p95_ms")
        _nonnegative_float(self.latency_limit_ms, "latency_limit_ms")
        _nonnegative_float(self.canary_latency_p95_ms, "canary_latency_p95_ms")
        _nonnegative_float(self.canary_latency_limit_ms, "canary_latency_limit_ms")
        for name in (
            "memory_peak_bytes",
            "memory_limit_bytes",
            "shadow_examples",
            "minimum_shadow_examples",
            "shadow_unsafe_actions",
            "canary_examples",
            "minimum_canary_examples",
        ):
            _nonnegative_int(getattr(self, name), name)
        _nonnegative_int(self.evaluation_seed, "evaluation_seed")
        if not self.slice_regressions or any(not str(key).strip() for key in self.slice_regressions):
            raise ValueError("local_adapter_gate_slice_regressions_invalid")
        for value in self.slice_regressions.values():
            _rate(value, "slice_regression")


@dataclass(frozen=True)
class AdapterLifecycleDecision:
    promote: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class LocalAdapterReleasePolicy:
    policy_id: str
    target: str
    evaluation_seed: int
    latency_limit_ms: float
    memory_limit_bytes: int
    max_slice_regression: float
    minimum_shadow_examples: int
    minimum_shadow_match_rate: float
    minimum_canary_examples: int
    maximum_canary_error_rate: float
    minimum_canary_accuracy: float
    maximum_canary_escalation_rate: float
    canary_latency_limit_ms: float
    maximum_confidence_brier_score: float
    canary_traffic_basis_points: int
    canary_allowed_tools: tuple[str, ...]
    canary_maximum_duration_seconds: int

    def __post_init__(self) -> None:
        if not str(self.policy_id).strip() or self.target not in {"needle2", "lfm2.5-2.6b-agentic"}:
            raise ValueError("local_adapter_release_policy_identity_invalid")
        for name in (
            "evaluation_seed",
            "memory_limit_bytes",
            "minimum_shadow_examples",
            "minimum_canary_examples",
            "canary_maximum_duration_seconds",
        ):
            _nonnegative_int(getattr(self, name), name)
        _nonnegative_float(self.latency_limit_ms, "latency_limit_ms")
        for name in (
            "max_slice_regression",
            "maximum_canary_error_rate",
            "maximum_confidence_brier_score",
            "minimum_shadow_match_rate",
            "minimum_canary_accuracy",
            "maximum_canary_escalation_rate",
        ):
            _rate(getattr(self, name), name)
        _nonnegative_float(self.canary_latency_limit_ms, "canary_latency_limit_ms")
        if not self.minimum_shadow_examples or not self.minimum_canary_examples:
            raise ValueError("local_adapter_release_policy_sample_minimum_invalid")
        if not 1 <= self.canary_maximum_duration_seconds <= 86_400:
            raise ValueError("local_adapter_release_policy_duration_invalid")
        if not 1 <= self.canary_traffic_basis_points <= 1000:
            raise ValueError("local_adapter_release_policy_traffic_invalid")
        normalized_tools = tuple(sorted({str(value).strip().lower() for value in self.canary_allowed_tools}))
        if not normalized_tools or any(_IDENTIFIER.fullmatch(value) is None for value in normalized_tools):
            raise ValueError("local_adapter_release_policy_tools_invalid")
        object.__setattr__(self, "canary_allowed_tools", normalized_tools)

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()


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
            "unknown_argument_regression": evidence.known_arguments_rate,
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
        if evidence.shadow_match_rate < evidence.minimum_shadow_match_rate:
            reasons.append("shadow_match_gate_failed")
        if evidence.shadow_unsafe_actions:
            reasons.append("shadow_safety_gate_failed")
        if evidence.canary_examples < evidence.minimum_canary_examples:
            reasons.append("canary_sample_gate_failed")
        if evidence.canary_error_rate > evidence.maximum_canary_error_rate:
            reasons.append("canary_error_gate_failed")
        if evidence.canary_accuracy < evidence.minimum_canary_accuracy:
            reasons.append("canary_accuracy_gate_failed")
        if evidence.canary_escalation_rate > evidence.maximum_canary_escalation_rate:
            reasons.append("canary_escalation_gate_failed")
        if evidence.canary_latency_p95_ms > evidence.canary_latency_limit_ms:
            reasons.append("canary_latency_gate_failed")
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

    def __post_init__(self) -> None:
        for name in ("schema_errors", "unknown_tools", "crashes_or_oom", "safety_violations"):
            _nonnegative_int(getattr(self, name), name)
        for name in (
            "tool_error_rate",
            "maximum_tool_error_rate",
            "latency_regression",
            "maximum_latency_regression",
        ):
            _rate(getattr(self, name), name)


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


def _sha256(value: str) -> None:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("local_adapter_gate_digest_invalid")


def _rate(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"local_adapter_gate_{name}_invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"local_adapter_gate_{name}_invalid")


def _nonnegative_float(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"local_adapter_gate_{name}_invalid")
    if not math.isfinite(float(value)) or float(value) < 0.0:
        raise ValueError(f"local_adapter_gate_{name}_invalid")


def _nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"local_adapter_gate_{name}_invalid")

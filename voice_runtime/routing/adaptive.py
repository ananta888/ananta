from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from voice_runtime.backends.base import TranscriptionSegment


@dataclass(frozen=True)
class ConfidenceRegion:
    start_ms: int
    end_ms: int
    confidence: float | None
    calibration_id: str | None

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("confidence region timestamps are invalid")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        if self.calibration_id is not None and not self.calibration_id.strip():
            raise ValueError("calibration ID must not be blank")


@dataclass(frozen=True)
class BackendRoute:
    backend_id: str
    local_execution: bool
    available: bool
    supported_devices: tuple[str, ...]
    fixed_latency_ms: int
    latency_per_audio_second_ms: int
    supports_regional_input: bool = False

    def __post_init__(self) -> None:
        if not self.backend_id or "://" in self.backend_id:
            raise ValueError("backend_id must be an opaque local capability identifier")
        if self.fixed_latency_ms < 0 or self.latency_per_audio_second_ms < 0:
            raise ValueError("backend latency estimates must not be negative")

    def estimate_latency_ms(self, duration_ms: int) -> int:
        return self.fixed_latency_ms + (max(0, duration_ms) * self.latency_per_audio_second_ms + 999) // 1000


@dataclass(frozen=True)
class RoutingPolicyEnvelope:
    allowed_backends: tuple[str, ...]
    preferred_backends: tuple[str, ...]
    allowed_devices: tuple[str, ...]
    max_candidate_count: int
    max_total_latency_ms: int
    max_regional_rerun_ms: int
    confidence_threshold: float

    def __post_init__(self) -> None:
        if not self.allowed_backends:
            raise ValueError("routing policy must allow at least one backend")
        if len(set(self.allowed_backends)) != len(self.allowed_backends):
            raise ValueError("allowed backends must be unique")
        if len(set(self.preferred_backends)) != len(self.preferred_backends):
            raise ValueError("preferred backends must be unique")
        if not set(self.preferred_backends).issubset(self.allowed_backends):
            raise ValueError("preferred backends must be allowed by the Hub policy")
        if not self.allowed_devices:
            raise ValueError("routing policy must allow at least one device")
        if len(set(self.allowed_devices)) != len(self.allowed_devices):
            raise ValueError("allowed devices must be unique")
        if self.max_candidate_count <= 0 or self.max_total_latency_ms <= 0 or self.max_regional_rerun_ms < 0:
            raise ValueError("routing budgets are invalid")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence threshold must be in [0, 1]")


@dataclass(frozen=True)
class RoutingMeasurements:
    audio_duration_ms: int
    overall_confidence: float | None
    overall_calibration_id: str | None
    confidence_regions: tuple[ConfidenceRegion, ...]
    available_devices: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.audio_duration_ms <= 0:
            raise ValueError("audio duration must be positive")
        if self.overall_confidence is not None and not 0 <= self.overall_confidence <= 1:
            raise ValueError("overall confidence must be in [0, 1]")
        if self.overall_calibration_id is not None and not self.overall_calibration_id.strip():
            raise ValueError("overall calibration ID must not be blank")


@dataclass(frozen=True)
class SelectedBackend:
    backend_id: str
    device: str
    estimated_latency_ms: int
    purpose: str


@dataclass(frozen=True)
class SkippedBackend:
    backend_id: str
    reason_code: str


@dataclass(frozen=True)
class RerunRegion:
    region_id: str
    start_ms: int
    end_ms: int
    backend_id: str
    device: str
    reason_code: str = "calibrated_low_confidence"


@dataclass(frozen=True)
class RoutingDecision:
    selected_backends: tuple[SelectedBackend, ...]
    skipped_backends: tuple[SkippedBackend, ...]
    rerun_regions: tuple[RerunRegion, ...]
    reason_codes: tuple[str, ...]
    estimated_total_latency_ms: int
    policy_bounded: bool = True
    remote_execution_allowed: bool = False


class AdaptiveLocalRouter:
    """Pure deterministic router; it neither probes hardware nor invokes a backend."""

    def decide(
        self,
        *,
        policy: RoutingPolicyEnvelope,
        measurements: RoutingMeasurements,
        capabilities: tuple[BackendRoute, ...],
    ) -> RoutingDecision:
        capability_by_id = _unique_capabilities(capabilities)
        ordered_ids = (
            *policy.preferred_backends,
            *sorted(set(policy.allowed_backends) - set(policy.preferred_backends)),
        )
        selected: list[SelectedBackend] = []
        skipped: list[SkippedBackend] = []
        used_latency = 0
        overall_needs_escalation = (
            measurements.overall_confidence is not None
            and measurements.overall_calibration_id is not None
            and measurements.overall_confidence < policy.confidence_threshold
        )
        regional_needs_escalation = any(
            region.confidence is not None
            and region.calibration_id is not None
            and region.confidence < policy.confidence_threshold
            for region in measurements.confidence_regions
        )
        needs_escalation = overall_needs_escalation or regional_needs_escalation
        desired_count = policy.max_candidate_count if needs_escalation else 1

        for backend_id in ordered_ids:
            capability = capability_by_id.get(backend_id)
            if capability is None:
                skipped.append(SkippedBackend(backend_id, "capability_missing"))
                continue
            reason = _ineligible_reason(capability, policy, measurements)
            if reason:
                skipped.append(SkippedBackend(backend_id, reason))
                continue
            if len(selected) >= desired_count:
                skipped.append(SkippedBackend(backend_id, "candidate_budget_reached"))
                continue
            device = next(
                device
                for device in policy.allowed_devices
                if device in measurements.available_devices and device in capability.supported_devices
            )
            estimate = capability.estimate_latency_ms(measurements.audio_duration_ms)
            if used_latency + estimate > policy.max_total_latency_ms:
                skipped.append(SkippedBackend(backend_id, "latency_budget_exceeded"))
                continue
            selected.append(
                SelectedBackend(
                    backend_id=backend_id,
                    device=device,
                    estimated_latency_ms=estimate,
                    purpose="primary" if not selected else "confidence_escalation",
                )
            )
            used_latency += estimate

        rerun_backend = next(
            (item for item in selected[1:] if capability_by_id[item.backend_id].supports_regional_input),
            None,
        )
        rerun_regions = (
            _bounded_rerun_regions(
                measurements=measurements,
                policy=policy,
                backend=rerun_backend,
            )
            if rerun_backend
            else ()
        )
        reason_codes: list[str] = []
        if selected:
            reason_codes.append("local_primary_selected")
        else:
            reason_codes.append("no_policy_compliant_backend")
        if needs_escalation:
            reason_codes.append("calibrated_confidence_below_threshold")
        elif measurements.overall_confidence is not None and measurements.overall_calibration_id is None:
            reason_codes.append("uncalibrated_confidence_not_used")
        if rerun_regions:
            reason_codes.append("regional_rerun_selected")
        return RoutingDecision(
            selected_backends=tuple(selected),
            skipped_backends=tuple(skipped),
            rerun_regions=rerun_regions,
            reason_codes=tuple(reason_codes),
            estimated_total_latency_ms=used_latency,
        )


def merge_regional_segments(
    *,
    baseline: tuple[TranscriptionSegment, ...],
    regions: tuple[RerunRegion, ...],
    replacements: Mapping[str, tuple[TranscriptionSegment, ...]],
) -> tuple[TranscriptionSegment, ...]:
    """Replace only segments intersecting authorized regions.

    Every replacement must stay completely inside its region. Baseline segment
    instances outside those regions are returned unchanged.
    """

    known_ids = {region.region_id for region in regions}
    if not set(replacements).issubset(known_ids):
        raise ValueError("replacement references an unknown rerun region")
    effective_regions = tuple(
        region for region in regions if region.region_id in replacements and replacements[region.region_id]
    )
    retained = [
        segment
        for segment in baseline
        if not any(
            _overlaps(segment.start_ms, segment.end_ms, region.start_ms, region.end_ms) for region in effective_regions
        )
    ]
    additions: list[TranscriptionSegment] = []
    for region in regions:
        for segment in replacements.get(region.region_id, ()):
            if segment.start_ms < region.start_ms or segment.end_ms > region.end_ms:
                raise ValueError("regional replacement escapes its authorized timeline")
            if any(word.start_ms < segment.start_ms or word.end_ms > segment.end_ms for word in segment.words):
                raise ValueError("regional replacement word timestamps escape their segment")
            additions.append(segment)
    return tuple(sorted((*retained, *additions), key=lambda segment: (segment.start_ms, segment.end_ms, segment.text)))


def _unique_capabilities(capabilities: tuple[BackendRoute, ...]) -> dict[str, BackendRoute]:
    result: dict[str, BackendRoute] = {}
    for capability in capabilities:
        if capability.backend_id in result:
            raise ValueError("backend capabilities must be unique")
        result[capability.backend_id] = capability
    return result


def _ineligible_reason(
    capability: BackendRoute,
    policy: RoutingPolicyEnvelope,
    measurements: RoutingMeasurements,
) -> str | None:
    if not capability.local_execution:
        return "remote_execution_blocked"
    if not capability.available:
        return "backend_unavailable"
    if not any(
        device in capability.supported_devices and device in measurements.available_devices
        for device in policy.allowed_devices
    ):
        return "device_not_allowed_or_unavailable"
    return None


def _bounded_rerun_regions(
    *,
    measurements: RoutingMeasurements,
    policy: RoutingPolicyEnvelope,
    backend: SelectedBackend,
) -> tuple[RerunRegion, ...]:
    eligible = tuple(
        region
        for region in measurements.confidence_regions
        if region.calibration_id is not None
        and region.confidence is not None
        and region.confidence < policy.confidence_threshold
    )
    merged = _merge_confidence_regions(eligible, duration_ms=measurements.audio_duration_ms)
    remaining = policy.max_regional_rerun_ms
    result: list[RerunRegion] = []
    for index, (start_ms, end_ms) in enumerate(merged):
        if remaining <= 0:
            break
        bounded_end = min(end_ms, start_ms + remaining)
        if bounded_end <= start_ms:
            continue
        result.append(
            RerunRegion(
                region_id=f"rerun-{index:04d}-{start_ms}-{bounded_end}",
                start_ms=start_ms,
                end_ms=bounded_end,
                backend_id=backend.backend_id,
                device=backend.device,
            )
        )
        remaining -= bounded_end - start_ms
    return tuple(result)


def _merge_confidence_regions(
    regions: tuple[ConfidenceRegion, ...],
    *,
    duration_ms: int,
) -> tuple[tuple[int, int], ...]:
    bounded = sorted(
        (
            max(0, min(region.start_ms, duration_ms)),
            max(0, min(region.end_ms, duration_ms)),
        )
        for region in regions
        if region.start_ms < duration_ms
    )
    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in bounded:
        if end_ms <= start_ms:
            continue
        if merged and start_ms <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end_ms))
        else:
            merged.append((start_ms, end_ms))
    return tuple(merged)


def _overlaps(first_start: int, first_end: int, second_start: int, second_end: int) -> bool:
    return max(first_start, second_start) < min(first_end, second_end)

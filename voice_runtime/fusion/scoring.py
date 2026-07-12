from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Mapping

from voice_runtime.backends.base import TranscriptionCandidate

SCORING_SCHEMA_VERSION = "ananta.voice-candidate-scoring.v2"
DEFAULT_SCORING_POLICY_VERSION = "ananta.voice-candidate-weights.v1"
AGREEMENT_SIGNAL_VERSION = "ananta.voice-token-agreement.v1"
_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class CalibrationEvaluation:
    sample_count: int
    ece_before: float
    ece_after: float
    brier_before: float
    brier_after: float

    def __post_init__(self) -> None:
        if self.sample_count <= 0:
            raise ValueError("voice calibration evaluation sample count must be positive")
        for value in (self.ece_before, self.ece_after, self.brier_before, self.brier_after):
            if not 0.0 <= value <= 1.0:
                raise ValueError("voice calibration evaluation metrics must be within [0, 1]")

    @property
    def quality(self) -> float:
        ece_gain = max(0.0, self.ece_before - self.ece_after)
        brier_gain = max(0.0, self.brier_before - self.brier_after)
        return (ece_gain + brier_gain) / 2.0

    def as_dict(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "ece_before": self.ece_before,
            "ece_after": self.ece_after,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
        }


@dataclass(frozen=True)
class CalibrationProfile:
    backend: str
    model_revision: str
    slope: float = 1.0
    intercept: float = 0.0
    dataset_version: str = "uncalibrated"
    artifact_digest: str = "unverified"
    calibrator_version: str = "linear-v1"
    evaluation: CalibrationEvaluation | None = None
    threshold_version: str | None = None
    minimum_confidence: float | None = None
    language: str = "*"
    hardware_profile: str = "*"

    def calibrate(self, value: float | None) -> float | None:
        if value is None:
            return None
        return max(0.0, min(1.0, self.slope * float(value) + self.intercept))

    def applies_to(self, candidate: TranscriptionCandidate) -> bool:
        language_matches = self.language == "*" or self.language == (candidate.language or "")
        hardware_matches = self.hardware_profile == "*" or self.hardware_profile == (
            candidate.device or ""
        )
        return language_matches and hardware_matches

    @property
    def comparable(self) -> bool:
        return bool(
            self.dataset_version != "uncalibrated"
            and self.artifact_digest.startswith("sha256:")
            and self.calibrator_version
            and self.evaluation is not None
            and self.threshold_version
            and self.minimum_confidence is not None
        )


@dataclass(frozen=True)
class VersionedSignal:
    value: float
    version: str
    artifact_digest: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("voice scoring signal must be within [0, 1]")
        if not self.version.strip():
            raise ValueError("voice scoring signal version is required")
        if not self.artifact_digest.startswith("sha256:"):
            raise ValueError("voice scoring signal artifact digest is required")


@dataclass(frozen=True)
class CandidateScoringSignals:
    agreement: VersionedSignal | None = None
    glossary: VersionedSignal | None = None
    language_model: VersionedSignal | None = None
    audio_quality: VersionedSignal | None = None


@dataclass(frozen=True)
class ScoringPolicy:
    version: str = DEFAULT_SCORING_POLICY_VERSION
    confidence_weight: float = 0.45
    agreement_weight: float = 0.30
    glossary_weight: float = 0.10
    language_model_weight: float = 0.05
    audio_quality_weight: float = 0.05
    calibration_quality_weight: float = 0.05

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("voice scoring policy version is required")
        weights = self.weights()
        if any(value < 0.0 for value in weights.values()):
            raise ValueError("voice scoring weights cannot be negative")
        if abs(sum(weights.values()) - 1.0) > 0.000001:
            raise ValueError("voice scoring weights must sum to one")

    def weights(self) -> dict[str, float]:
        return {
            "confidence": self.confidence_weight,
            "agreement": self.agreement_weight,
            "glossary": self.glossary_weight,
            "language_model": self.language_model_weight,
            "audio_quality": self.audio_quality_weight,
            "calibration_quality": self.calibration_quality_weight,
        }


class CandidateScorer:
    """Quantized candidate scorer with explicit, versioned evidence only."""

    def __init__(
        self,
        calibration: Mapping[tuple[str, ...], CalibrationProfile] | None = None,
        *,
        policy: ScoringPolicy | None = None,
    ) -> None:
        self._calibration = dict(calibration or {})
        self._policy = policy or ScoringPolicy()

    def calibration_profile(self, candidate: TranscriptionCandidate) -> CalibrationProfile | None:
        backend = candidate.backend
        revision = candidate.model_revision or ""
        language = candidate.language or ""
        hardware = candidate.device or ""
        keys = (
            (backend, revision, language, hardware),
            (backend, revision, language, "*"),
            (backend, revision, "*", hardware),
            (backend, revision, "*", "*"),
            (backend, revision),
        )
        for key in keys:
            profile = self._calibration.get(key)
            if profile is not None and profile.applies_to(candidate):
                return profile
        return None

    def confidence_is_comparable(self, candidate: TranscriptionCandidate) -> bool:
        profile = self.calibration_profile(candidate)
        return bool(profile and profile.comparable and candidate.confidence is not None)

    def score(
        self,
        candidate: TranscriptionCandidate,
        *,
        signals: CandidateScoringSignals | None = None,
        allow_uncalibrated_confidence: bool = False,
        allow_calibrated_signals: bool = True,
    ) -> tuple[float, dict[str, object]]:
        supplied = signals or CandidateScoringSignals()
        profile = self.calibration_profile(candidate)
        calibrated = profile.calibrate(candidate.confidence) if profile else candidate.confidence
        confidence_signal: VersionedSignal | None = None
        confidence_reason: str | None = None
        if candidate.confidence is None:
            confidence_reason = "confidence_missing"
        elif (
            profile
            and profile.comparable
            and calibrated is not None
            and allow_calibrated_signals
        ):
            confidence_signal = VersionedSignal(
                value=calibrated,
                version=f"{profile.calibrator_version}:{profile.dataset_version}",
                artifact_digest=profile.artifact_digest,
            )
        elif allow_uncalibrated_confidence:
            raw_digest = _stable_digest(
                {
                    "backend": candidate.backend,
                    "model_revision": candidate.model_revision or "",
                    "signal": "raw_backend_confidence",
                }
            )
            confidence_signal = VersionedSignal(
                value=float(candidate.confidence),
                version=f"raw-backend-confidence:{candidate.backend}:{candidate.model_revision or 'unknown'}",
                artifact_digest=raw_digest,
            )
        else:
            confidence_reason = (
                "calibration_set_incomplete"
                if profile and profile.comparable
                else "calibration_evaluation_incomplete"
                if profile
                else "calibration_missing"
            )

        optional = {
            "agreement": supplied.agreement,
            "glossary": supplied.glossary or _candidate_signal(candidate, "glossary"),
            "language_model": supplied.language_model
            or _candidate_signal(candidate, "language_model"),
            "audio_quality": supplied.audio_quality or _candidate_signal(candidate, "audio_quality"),
        }
        calibration_quality = (
            VersionedSignal(
                value=profile.evaluation.quality,
                version=f"calibration-report:{profile.dataset_version}",
                artifact_digest=profile.artifact_digest,
            )
            if profile
            and profile.comparable
            and profile.evaluation is not None
            and allow_calibrated_signals
            else None
        )
        all_signals: dict[str, VersionedSignal | None] = {
            "confidence": confidence_signal,
            **optional,
            "calibration_quality": calibration_quality,
        }
        weights = self._policy.weights()
        trace_signals: dict[str, dict[str, object]] = {}
        total = Decimal("0")
        missing: list[str] = []
        for name in (
            "confidence",
            "agreement",
            "glossary",
            "language_model",
            "audio_quality",
            "calibration_quality",
        ):
            signal = all_signals[name]
            weight = weights[name]
            contribution = (
                _decimal(signal.value) * _decimal(weight) if signal is not None else Decimal("0")
            )
            total += contribution
            if signal is None:
                missing.append(name)
            trace_signals[name] = {
                "available": signal is not None,
                "value": signal.value if signal is not None else None,
                "version": signal.version if signal is not None else None,
                "artifact_digest": signal.artifact_digest if signal is not None else None,
                "weight": weight,
                "contribution": _quantize(contribution),
            }
        if confidence_reason:
            trace_signals["confidence"]["reason"] = confidence_reason

        quantized = _quantize(total)
        trace: dict[str, object] = {
            "scoring_schema_version": SCORING_SCHEMA_VERSION,
            "weights_version": self._policy.version,
            "weights": weights,
            "signals": trace_signals,
            "missing_signals": missing,
            "confidence": calibrated,
            "confidence_comparable": confidence_signal is not None,
            "calibration": profile.dataset_version if profile else "raw_uncalibrated",
            "calibration_digest": profile.artifact_digest if profile else None,
            "calibration_report": profile.evaluation.as_dict()
            if profile and profile.evaluation
            else None,
            "threshold_version": profile.threshold_version if profile else None,
            "minimum_confidence": profile.minimum_confidence if profile else None,
            "below_threshold": bool(
                profile
                and profile.minimum_confidence is not None
                and calibrated is not None
                and calibrated < profile.minimum_confidence
            ),
            "score": quantized,
        }
        return quantized, trace


def load_calibration_profiles(path: str | Path) -> dict[tuple[str, ...], CalibrationProfile]:
    payload_bytes = Path(path).expanduser().resolve(strict=True).read_bytes()
    if len(payload_bytes) > 1024 * 1024:
        raise ValueError("voice calibration artifact exceeds its size budget")
    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict) or payload.get("schema_version") != "ananta.voice-calibration.v1":
        raise ValueError("unsupported voice calibration schema")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    result: dict[tuple[str, ...], CalibrationProfile] = {}
    for raw in payload.get("profiles") or []:
        if not isinstance(raw, dict):
            raise ValueError("voice calibration profile must be an object")
        backend = str(raw.get("backend") or "").strip()
        revision = str(raw.get("model_revision") or "").strip()
        dataset = str(raw.get("dataset_version") or "").strip()
        if not backend or not revision or not dataset:
            raise ValueError("voice calibration identity must be complete")
        slope = float(raw.get("slope", 1.0))
        intercept = float(raw.get("intercept", 0.0))
        if not -10 <= slope <= 10 or not -10 <= intercept <= 10:
            raise ValueError("voice calibration coefficients are out of bounds")
        evaluation = _load_evaluation(raw.get("evaluation"))
        thresholds = raw.get("thresholds")
        if thresholds is not None and not isinstance(thresholds, Mapping):
            raise ValueError("voice calibration thresholds must be an object")
        threshold_mapping = thresholds if isinstance(thresholds, Mapping) else {}
        threshold_version = str(threshold_mapping.get("version") or "").strip() or None
        minimum_confidence_raw = threshold_mapping.get("minimum_confidence")
        minimum_confidence = (
            float(minimum_confidence_raw) if minimum_confidence_raw is not None else None
        )
        if minimum_confidence is not None and not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("voice calibration minimum confidence must be within [0, 1]")
        language = str(threshold_mapping.get("language") or "*").strip()
        hardware_profile = str(
            threshold_mapping.get("hardware_profile") or "*"
        ).strip()
        key: tuple[str, ...] = (
            (backend, revision)
            if language == "*" and hardware_profile == "*"
            else (backend, revision, language, hardware_profile)
        )
        if key in result:
            raise ValueError("duplicate voice calibration profile scope")
        result[key] = CalibrationProfile(
            backend=backend,
            model_revision=revision,
            slope=slope,
            intercept=intercept,
            dataset_version=dataset,
            artifact_digest=digest,
            calibrator_version=str(raw.get("calibrator_version") or "linear-v1").strip(),
            evaluation=evaluation,
            threshold_version=threshold_version,
            minimum_confidence=minimum_confidence,
            language=language,
            hardware_profile=hardware_profile,
        )
    if not result:
        raise ValueError("voice calibration artifact has no profiles")
    return result


def _load_evaluation(value: object) -> CalibrationEvaluation | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("voice calibration evaluation must be an object")
    required = ("sample_count", "ece_before", "ece_after", "brier_before", "brier_after")
    if any(item not in value for item in required):
        raise ValueError("voice calibration evaluation report is incomplete")
    return CalibrationEvaluation(
        sample_count=int(value["sample_count"]),
        ece_before=float(value["ece_before"]),
        ece_after=float(value["ece_after"]),
        brier_before=float(value["brier_before"]),
        brier_after=float(value["brier_after"]),
    )


def _candidate_signal(candidate: TranscriptionCandidate, name: str) -> VersionedSignal | None:
    raw_signals = candidate.provenance.get("scoring_signals")
    if not isinstance(raw_signals, Mapping):
        return None
    raw = raw_signals.get(name)
    if not isinstance(raw, Mapping):
        return None
    try:
        return VersionedSignal(
            value=float(raw["value"]),
            version=str(raw["version"]),
            artifact_digest=str(raw["artifact_digest"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _stable_digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _quantize(value: Decimal) -> float:
    return float(value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN))

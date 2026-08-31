"""Optional, offline statistical findings behind an admitted capability gate."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from ananta_contracts.business_controlling import (
    CONTRACT_VERSION,
    BusinessFinding,
    DatasetReceipt,
    FindingDisposition,
    FindingKind,
    FindingSeverity,
    RecordLocator,
    derive_stable_id,
)


class BusinessControllingStatisticsError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class StatisticalCapabilityDecision:
    admitted: bool
    local_execution: bool
    network_allowed: bool
    catalog_entry_id: str
    skill_name: str
    upstream_pin: str
    capability_digest: str
    reason_code: str


class StatisticalCapabilityGatePort(Protocol):
    def assess(
        self,
        *,
        tenant_id: str,
        project_id: str,
        skill_name: str,
        catalog_entry_id: str,
    ) -> StatisticalCapabilityDecision: ...


@dataclass(frozen=True)
class StatisticalAnalysisPolicy:
    policy_id: str
    policy_version: str
    method: str
    skill_name: str
    catalog_entry_id: str
    minimum_reference_points: int = 12
    season_length: int = 1
    robust_z_threshold: float = 3.5

    def validate(self) -> None:
        if (
            not _token(self.policy_id)
            or not _token(self.policy_version)
            or self.method != "robust_seasonal_zscore"
            or not _token(self.skill_name)
            or not self.catalog_entry_id.startswith("skillentry_")
            or self.minimum_reference_points < 4
            or not 1 <= self.season_length <= 366
            or not math.isfinite(self.robust_z_threshold)
            or not 1.0 <= self.robust_z_threshold <= 20.0
        ):
            raise BusinessControllingStatisticsError(
                "controlling_statistical_policy_invalid"
            )


@dataclass(frozen=True)
class StatisticalObservation:
    locator: RecordLocator
    period_index: int
    amount: Decimal
    reference: bool

    @classmethod
    def create(
        cls,
        *,
        locator: RecordLocator,
        period_index: int,
        amount: Decimal | str | int,
        reference: bool,
    ) -> "StatisticalObservation":
        try:
            normalized = Decimal(str(amount))
        except InvalidOperation as exc:
            raise BusinessControllingStatisticsError(
                "controlling_statistical_amount_invalid"
            ) from exc
        if (
            isinstance(period_index, bool)
            or not isinstance(period_index, int)
            or period_index < 0
            or not normalized.is_finite()
            or not isinstance(reference, bool)
        ):
            raise BusinessControllingStatisticsError(
                "controlling_statistical_observation_invalid"
            )
        return cls(locator, period_index, normalized, reference)


@dataclass(frozen=True)
class StatisticalAnalysisReceipt:
    schema_version: str
    execution_id: str
    dataset_id: str
    dataset_version: str
    method: str
    policy_id: str
    policy_version: str
    reference_start: int
    reference_end: int
    reference_count: int
    candidate_count: int
    season_length: int
    threshold: float
    catalog_entry_id: str
    skill_name: str
    upstream_pin: str
    capability_digest: str
    configuration_digest: str
    output_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class StatisticalAnalysisResult:
    deterministic_findings: tuple[BusinessFinding, ...]
    statistical_findings: tuple[BusinessFinding, ...]
    receipt: StatisticalAnalysisReceipt


class BusinessControllingStatisticsService:
    """Create advisory anomalies without changing deterministic findings."""

    def __init__(self, capability_gate: StatisticalCapabilityGatePort) -> None:
        self._capability_gate = capability_gate

    def evaluate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        dataset: DatasetReceipt,
        policy: StatisticalAnalysisPolicy,
        observations: Sequence[StatisticalObservation],
        deterministic_findings: Sequence[BusinessFinding] = (),
    ) -> StatisticalAnalysisResult:
        policy.validate()
        capability = self._capability_gate.assess(
            tenant_id=tenant_id,
            project_id=project_id,
            skill_name=policy.skill_name,
            catalog_entry_id=policy.catalog_entry_id,
        )
        if not capability.admitted:
            raise BusinessControllingStatisticsError(
                capability.reason_code or "controlling_statistical_capability_denied"
            )
        if not capability.local_execution or capability.network_allowed:
            raise BusinessControllingStatisticsError(
                "controlling_statistical_offline_execution_required"
            )
        if (
            capability.skill_name != policy.skill_name
            or capability.catalog_entry_id != policy.catalog_entry_id
            or not capability.upstream_pin
            or len(capability.capability_digest) != 64
        ):
            raise BusinessControllingStatisticsError(
                "controlling_statistical_capability_binding_invalid"
            )

        normalized = tuple(observations)
        if not normalized or len(normalized) > 100_000:
            raise BusinessControllingStatisticsError(
                "controlling_statistical_observation_budget_invalid"
            )
        self._validate_bindings(dataset, normalized)
        reference = tuple(item for item in normalized if item.reference)
        candidates = tuple(item for item in normalized if not item.reference)
        if len(reference) < policy.minimum_reference_points or not candidates:
            raise BusinessControllingStatisticsError(
                "controlling_statistical_reference_insufficient"
            )

        baselines = _seasonal_baselines(reference, policy.season_length)
        residuals = [
            float(item.amount) - baselines[item.period_index % policy.season_length]
            for item in reference
        ]
        residual_center = statistics.median(residuals)
        deviations = [abs(value - residual_center) for value in residuals]
        mad = statistics.median(deviations)
        scale = max(1e-12, 1.4826 * mad)
        configuration = {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "method": policy.method,
            "season_length": policy.season_length,
            "threshold": policy.robust_z_threshold,
            "catalog_entry_id": policy.catalog_entry_id,
            "capability_digest": capability.capability_digest,
        }
        configuration_digest = _digest(configuration)
        input_digest = _digest(
            {
                "dataset": dataset.to_dict(),
                "locators": [item.locator.to_dict() for item in normalized],
                "period_indexes": [item.period_index for item in normalized],
                "reference_flags": [item.reference for item in normalized],
                "amount_digests": [
                    hashlib.sha256(str(item.amount).encode()).hexdigest()
                    for item in normalized
                ],
            }
        )
        execution_id = derive_stable_id(
            "ctrlstat",
            {
                "input_digest": input_digest,
                "configuration_digest": configuration_digest,
            },
        )

        candidates_with_score: list[tuple[StatisticalObservation, float]] = []
        for item in candidates:
            expected = baselines[item.period_index % policy.season_length]
            score = abs((float(item.amount) - expected) - residual_center) / scale
            if score >= policy.robust_z_threshold:
                candidates_with_score.append((item, score))

        output_material = [
            {
                "locator": item.locator.to_dict(),
                "score": round(score, 8),
            }
            for item, score in candidates_with_score
        ]
        output_digest = _digest(output_material)
        receipt = StatisticalAnalysisReceipt(
            schema_version="ananta.business-controlling-statistics.v1",
            execution_id=execution_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            method=policy.method,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            reference_start=min(item.period_index for item in reference),
            reference_end=max(item.period_index for item in reference),
            reference_count=len(reference),
            candidate_count=len(candidates),
            season_length=policy.season_length,
            threshold=policy.robust_z_threshold,
            catalog_entry_id=capability.catalog_entry_id,
            skill_name=capability.skill_name,
            upstream_pin=capability.upstream_pin,
            capability_digest=capability.capability_digest,
            configuration_digest=configuration_digest,
            output_digest=output_digest,
        )
        receipt_digest = receipt.receipt_digest
        findings = tuple(
            _finding(
                dataset=dataset,
                policy=policy,
                item=item,
                score=score,
                execution_id=execution_id,
                receipt_digest=receipt_digest,
            )
            for item, score in candidates_with_score
        )
        return StatisticalAnalysisResult(
            deterministic_findings=tuple(deterministic_findings),
            statistical_findings=findings,
            receipt=receipt,
        )

    @staticmethod
    def _validate_bindings(
        dataset: DatasetReceipt,
        observations: Sequence[StatisticalObservation],
    ) -> None:
        seen: set[tuple[str, str, str, str]] = set()
        for item in observations:
            locator = item.locator
            if locator.source_version != dataset.dataset_version:
                raise BusinessControllingStatisticsError(
                    "controlling_statistical_locator_scope_mismatch"
                )
            key = (
                locator.source_kind,
                locator.source_version,
                locator.locator_kind,
                locator.locator,
            )
            if key in seen:
                raise BusinessControllingStatisticsError(
                    "controlling_statistical_locator_duplicate"
                )
            seen.add(key)


def _seasonal_baselines(
    observations: Sequence[StatisticalObservation],
    season_length: int,
) -> dict[int, float]:
    global_median = statistics.median(float(item.amount) for item in observations)
    by_season: dict[int, list[float]] = {
        index: [] for index in range(season_length)
    }
    for item in observations:
        by_season[item.period_index % season_length].append(float(item.amount))
    return {
        index: statistics.median(values) if values else global_median
        for index, values in by_season.items()
    }


def _finding(
    *,
    dataset: DatasetReceipt,
    policy: StatisticalAnalysisPolicy,
    item: StatisticalObservation,
    score: float,
    execution_id: str,
    receipt_digest: str,
) -> BusinessFinding:
    confidence = min(1.0, score / (policy.robust_z_threshold * 2.0))
    evidence_digest = _digest(
        {
            "execution_id": execution_id,
            "locator": item.locator.to_dict(),
            "method": policy.method,
            "threshold": policy.robust_z_threshold,
            "score": round(score, 8),
        }
    )
    finding_id = derive_stable_id(
        "ctrlfinding",
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "locator": item.locator.to_dict(),
            "evidence_digest": evidence_digest,
        },
    )
    return BusinessFinding(
        contract_version=CONTRACT_VERSION,
        finding_id=finding_id,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        kind=FindingKind.STATISTICAL_ANOMALY,
        severity=FindingSeverity.MEDIUM,
        rule_id=policy.method,
        rule_version=policy.policy_version,
        locator=item.locator,
        evidence_digest=evidence_digest,
        confidence=confidence,
        disposition=FindingDisposition.OPEN,
        execution_receipt_digest=receipt_digest,
    )


def _token(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(character.isalnum() or character in "_.:-" for character in value)
    )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingStatisticsError",
    "BusinessControllingStatisticsService",
    "StatisticalAnalysisPolicy",
    "StatisticalAnalysisReceipt",
    "StatisticalAnalysisResult",
    "StatisticalCapabilityDecision",
    "StatisticalCapabilityGatePort",
    "StatisticalObservation",
]

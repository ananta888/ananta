from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.business_controlling_statistics import (
    BusinessControllingStatisticsError,
    BusinessControllingStatisticsService,
    StatisticalAnalysisPolicy,
    StatisticalCapabilityDecision,
    StatisticalObservation,
)
from ananta_contracts.business_controlling import (
    CONTRACT_VERSION,
    BusinessFinding,
    DatasetReceipt,
    FindingDisposition,
    FindingKind,
    FindingSeverity,
    RecordLocator,
)


class _Gate:
    def __init__(self, decision: StatisticalCapabilityDecision | None = None) -> None:
        self.decision = decision or StatisticalCapabilityDecision(
            admitted=True,
            local_execution=True,
            network_allowed=False,
            catalog_entry_id="skillentry_" + "a" * 64,
            skill_name="statsmodels",
            upstream_pin="a" * 40,
            capability_digest="b" * 64,
            reason_code="controlling_statistical_capability_admitted",
        )

    def assess(self, **_: object) -> StatisticalCapabilityDecision:
        return self.decision


def _dataset() -> DatasetReceipt:
    return DatasetReceipt.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "dataset_id": "dataset-a",
            "dataset_version": "version-a",
            "source_digest": "1" * 64,
            "period_start": "2025-01-01",
            "period_end": "2026-12-31",
            "currency": "EUR",
            "column_mapping": {"amount": "amount", "period": "period"},
        }
    )


def _locator(row: int) -> RecordLocator:
    return RecordLocator("csv", "version-a", "csv_row", f"row-{row}")


def _policy() -> StatisticalAnalysisPolicy:
    return StatisticalAnalysisPolicy(
        policy_id="monthly-anomaly",
        policy_version="v1",
        method="robust_seasonal_zscore",
        skill_name="statsmodels",
        catalog_entry_id="skillentry_" + "a" * 64,
        minimum_reference_points=12,
        season_length=12,
        robust_z_threshold=3.5,
    )


def test_seasonal_baseline_avoids_false_positive_and_flags_one_off() -> None:
    observations = [
        StatisticalObservation.create(
            locator=_locator(index + 1),
            period_index=index,
            amount=100 + (index % 12) * 10,
            reference=True,
        )
        for index in range(24)
    ]
    observations.extend(
        [
            StatisticalObservation.create(
                locator=_locator(25),
                period_index=24,
                amount=100,
                reference=False,
            ),
            StatisticalObservation.create(
                locator=_locator(26),
                period_index=25,
                amount=900,
                reference=False,
            ),
        ]
    )

    result = BusinessControllingStatisticsService(_Gate()).evaluate(
        tenant_id="tenant-a",
        project_id="project-a",
        dataset=_dataset(),
        policy=_policy(),
        observations=observations,
    )

    assert [item.locator.locator for item in result.statistical_findings] == ["row-26"]
    assert result.statistical_findings[0].kind is FindingKind.STATISTICAL_ANOMALY
    assert result.receipt.reference_count == 24
    assert result.receipt.candidate_count == 2
    assert result.receipt.skill_name == "statsmodels"
    assert "amount" not in result.receipt.to_dict()


def test_statistical_findings_never_replace_deterministic_findings() -> None:
    deterministic = BusinessFinding(
        CONTRACT_VERSION,
        "finding-deterministic",
        "dataset-a",
        "version-a",
        FindingKind.DETERMINISTIC_VIOLATION,
        FindingSeverity.HIGH,
        "rule-required",
        "v1",
        _locator(1),
        "2" * 64,
        None,
        FindingDisposition.OPEN,
        "3" * 64,
    )
    observations = tuple(
        StatisticalObservation.create(
            locator=_locator(index + 1),
            period_index=index,
            amount=100,
            reference=index < 12,
        )
        for index in range(13)
    )

    result = BusinessControllingStatisticsService(_Gate()).evaluate(
        tenant_id="tenant-a",
        project_id="project-a",
        dataset=_dataset(),
        policy=_policy(),
        observations=observations,
        deterministic_findings=(deterministic,),
    )

    assert result.deterministic_findings == (deterministic,)


@pytest.mark.parametrize(
    "decision,reason",
    [
        (
            StatisticalCapabilityDecision(
                False,
                True,
                False,
                "skillentry_" + "a" * 64,
                "statsmodels",
                "a" * 40,
                "b" * 64,
                "controlling_statistical_feature_disabled",
            ),
            "feature_disabled",
        ),
        (
            StatisticalCapabilityDecision(
                True,
                True,
                True,
                "skillentry_" + "a" * 64,
                "statsmodels",
                "a" * 40,
                "b" * 64,
                "admitted",
            ),
            "offline_execution_required",
        ),
    ],
)
def test_capability_gate_fails_closed(
    decision: StatisticalCapabilityDecision,
    reason: str,
) -> None:
    observations = tuple(
        StatisticalObservation.create(
            locator=_locator(index + 1),
            period_index=index,
            amount=100,
            reference=index < 12,
        )
        for index in range(13)
    )
    with pytest.raises(BusinessControllingStatisticsError, match=reason):
        BusinessControllingStatisticsService(_Gate(decision)).evaluate(
            tenant_id="tenant-a",
            project_id="project-a",
            dataset=_dataset(),
            policy=_policy(),
            observations=observations,
        )


def test_locator_must_remain_dataset_version_bound() -> None:
    observations = [
        StatisticalObservation.create(
            locator=replace(_locator(index + 1), source_version="other-version"),
            period_index=index,
            amount=100,
            reference=index < 12,
        )
        for index in range(13)
    ]
    with pytest.raises(BusinessControllingStatisticsError, match="scope_mismatch"):
        BusinessControllingStatisticsService(_Gate()).evaluate(
            tenant_id="tenant-a",
            project_id="project-a",
            dataset=_dataset(),
            policy=_policy(),
            observations=observations,
        )

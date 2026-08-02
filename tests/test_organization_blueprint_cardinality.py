from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.models.organization_models import OrganizationCompileRequest
from agent.services.organization_blueprint_compiler import OrganizationCompilationError
from tests.organization_support import organization_compiler


def _request(**overrides) -> OrganizationCompileRequest:
    payload = {
        "tenant_id": "tenant-cardinality",
        "project_id": "project-cardinality",
        "organization_id": "organization-cardinality",
        "definition_ref": "enterprise_scrum_organization@1",
        "composition_mode": "standard",
        "team_count": 8,
    }
    payload.update(overrides)
    return OrganizationCompileRequest(**payload)


@pytest.mark.parametrize("team_count", range(5, 11))
def test_every_standard_team_count_expands_to_the_requested_cardinality(team_count: int) -> None:
    plan = organization_compiler().compile(_request(team_count=team_count))

    materialized_teams = [unit for unit in plan.units if unit.team_blueprint_ref]
    assert len(materialized_teams) == team_count
    assert plan.expected_counts["team"] == team_count


@pytest.mark.parametrize(
    ("composition", "expected_count"),
    (
        ({"enterprise_product_delivery_scrum": 2}, 2),
        ({"enterprise_product_delivery_scrum": 1, "platform_devops_sre": 1}, 2),
        ({"enterprise_product_delivery_scrum": 2, "platform_devops_sre": 1}, 3),
        (
            {
                "enterprise_product_delivery_scrum": 1,
                "research_and_discovery": 1,
                "platform_devops_sre": 1,
            },
            3,
        ),
    ),
)
def test_small_custom_two_and_three_team_compositions_are_explicit(
    composition: dict[str, int], expected_count: int
) -> None:
    plan = organization_compiler().compile(
        _request(
            composition_mode="custom",
            team_count=None,
            custom_composition=composition,
            admission_exception_ref="test-only-small@1",
        )
    )

    assert plan.requested_team_count == expected_count
    assert (
        sum(count for key, count in plan.expected_counts.items() if key.startswith("team_blueprint:")) == expected_count
    )


@pytest.mark.parametrize("team_count", (31, 32))
def test_custom_group_accepts_limit_minus_one_and_limit(team_count: int) -> None:
    plan = organization_compiler().compile(
        _request(
            composition_mode="custom",
            team_count=None,
            custom_composition={"enterprise_product_delivery_scrum": team_count},
            admission_exception_ref="test-only-small@1",
        )
    )

    assert plan.requested_team_count == team_count


def test_custom_group_rejects_effective_limit_plus_one() -> None:
    with pytest.raises(OrganizationCompilationError) as exc:
        organization_compiler().compile(
            _request(
                composition_mode="custom",
                team_count=None,
                custom_composition={"enterprise_product_delivery_scrum": 33},
                admission_exception_ref="test-only-small@1",
            )
        )

    assert exc.value.reason_code == "ORGANIZATION_TEAM_LIMIT_EXCEEDED"


@pytest.mark.parametrize("invalid_count", (True, 2.5, "8"))
def test_non_integer_standard_counts_fail_contract_validation(invalid_count: object) -> None:
    with pytest.raises(ValidationError):
        _request(team_count=invalid_count)


def test_resize_preserves_group_identities_across_two_to_n_to_two() -> None:
    compiler = organization_compiler()

    def compile_delivery(count: int):
        return compiler.compile(
            _request(
                composition_mode="custom",
                team_count=None,
                custom_composition={"enterprise_product_delivery_scrum": count},
                admission_exception_ref="test-only-small@1",
            )
        )

    two_before = compile_delivery(2)
    expanded = compile_delivery(10)
    two_after = compile_delivery(2)
    first_ids = {unit.unit_key: unit.planned_id for unit in two_before.units}
    expanded_ids = {unit.unit_key: unit.planned_id for unit in expanded.units}
    final_ids = {unit.unit_key: unit.planned_id for unit in two_after.units}

    assert final_ids == first_ids
    assert all(expanded_ids[key] == planned_id for key, planned_id in first_ids.items())

from agent.models.organization_models import OrganizationCompileRequest
from agent.services.organization_topology_change_planner import OrganizationTopologyChangePlanner
from tests.organization_support import organization_compiler


def _compile(team_count: int):
    return organization_compiler().compile(
        OrganizationCompileRequest(
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="org-a",
            definition_ref="enterprise_scrum_organization@1",
            composition_mode="standard",
            team_count=team_count,
        )
    )


def _snapshot(plan):
    return {
        "organization_id": plan.organization_id,
        "snapshot_hash": f"snapshot-{plan.requested_team_count}",
        "units": [
            {
                "unit_key": unit.unit_key,
                "parent_unit_key": unit.parent_unit_key,
            }
            for unit in plan.units
        ],
    }


class RuntimeGuard:
    def __init__(self, activity=None):
        self.activity = activity or {}
        self.calls = []

    def unit_activity(self, organization_id, unit_keys):
        self.calls.append((organization_id, tuple(unit_keys)))
        return {key: self.activity.get(key, {}) for key in unit_keys}


def test_resize_retains_stable_units_and_only_creates_scale_out_delta():
    source = _compile(5)
    target = _compile(10)
    guard = RuntimeGuard()

    result = OrganizationTopologyChangePlanner(runtime_guard=guard).plan(
        current_snapshot=_snapshot(source),
        target=target,
    )

    actions = {change.unit_key: change.action for change in result.changes}
    assert actions["product_delivery:001"] == "retain"
    assert actions["product_delivery:004"] == "create"
    assert not result.blockers
    assert len(guard.calls) == 1


def test_scale_down_with_runtime_activity_requires_drain_instead_of_orphaning_lineage():
    source = _compile(10)
    target = _compile(5)
    guard = RuntimeGuard({"product_delivery:003": {"tasks": 1, "leases": 1}})

    result = OrganizationTopologyChangePlanner(runtime_guard=guard).plan(
        current_snapshot=_snapshot(source),
        target=target,
    )

    change = next(item for item in result.changes if item.unit_key == "product_delivery:003")
    assert change.action == "drain"
    assert change.requires_confirmation is True
    assert {item.reason_code for item in result.blockers} == {"ORGANIZATION_UNIT_DRAIN_REQUIRED"}

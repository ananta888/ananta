from __future__ import annotations

from types import SimpleNamespace

from agent.services.organization_topology_apply_service import (
    OrganizationPatchState,
    OrganizationTopologyApplyService,
    OrganizationTopologyPatchDocument,
)
from tests.organization_support import organization_limits


def _unit(unit_id: str, kind: str, parent_id: str | None = None):
    return SimpleNamespace(
        id=unit_id,
        unit_key=unit_id,
        unit_kind=kind,
        parent_unit_id=parent_id,
        lifecycle="active",
        team_blueprint_key=None,
        team_blueprint_version=None,
    )


def _slot(slot_id: str, unit_id: str):
    return SimpleNamespace(
        id=slot_id,
        unit_id=unit_id,
        slot_key=slot_id,
        required=True,
        default_count=1,
        max_count=4,
        assignment_policy={
            "principal_kinds": ["agent"],
            "required_capabilities": ["code"],
            "forbidden_capabilities": [],
            "write_access_required": False,
        },
        separation_of_duties={
            "enforcement": "none",
            "independent_from_slot_ids": [],
            "independent_from_external_duties": [],
        },
        lifecycle="active",
    )


def _agent(*, status: str = "online", execution_limits: dict | None = None):
    return SimpleNamespace(
        url="agent-a",
        registration_validated=True,
        status=status,
        authorized_capabilities=["code"],
        capabilities=[],
        execution_limits=execution_limits or {"max_assignments": 4},
    )


def _state(
    *,
    role_slots=(),
    assignments=(),
    relations=(),
    agent=None,
    global_assignment_count: int = 0,
    activity_by_unit=None,
):
    units = (
        _unit("root", "coordination_unit"),
        _unit("stream-a", "value_stream", "root"),
        _unit("stream-b", "value_stream", "root"),
        _unit("team-a", "team", "stream-a"),
    )
    return OrganizationPatchState(
        organization=SimpleNamespace(
            definition_revision="revision-1",
            effective_limit_profile_ref="organization_limits@1",
            effective_limit_profile_revision=1,
            tenant_id="tenant-a",
            project_id="project-a",
        ),
        snapshot=SimpleNamespace(snapshot_hash="snapshot-1"),
        units=units,
        team_links=(),
        role_slots=tuple(role_slots),
        assignments=tuple(assignments),
        relations=tuple(relations),
        team_blueprints={},
        team_blueprint_rows={},
        role_template_refs=frozenset(),
        workflow_steps={},
        agents={"agent-a": agent} if agent is not None else {},
        global_assignment_count_by_agent={"agent-a": global_assignment_count},
        activity_by_unit=activity_by_unit
        or {
            unit.id: {
                "tasks": 0,
                "leases": 0,
                "open_gates": 0,
                "handoffs": 0,
                "assignments": 0,
            }
            for unit in units
        },
        effective_policy_hash="policy-hash",
        budget_policy_hash="budget-hash",
        handoff_definition_refs=frozenset(),
    )


def _preview(state, operations):
    service = OrganizationTopologyApplyService(
        reader=SimpleNamespace(),
        limit_profiles=SimpleNamespace(),
        clock=lambda: 100.0,
    )
    document = OrganizationTopologyPatchDocument.model_validate(
        {"expected_revision": "revision-1", "operations": operations}
    )
    return service._evaluate(  # noqa: SLF001 - focused ordered-interpreter contract test
        state=state,
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        principal_id="admin-a",
        document=document,
        limits=organization_limits(),
        expires_at_epoch=200.0,
    ).preview


def _reason_codes(preview) -> set[str]:
    return {item["reason_code"] for item in preview.diagnostics}


def test_dry_run_reserves_accepted_stable_keys_for_later_operations() -> None:
    operation = {
        "op": "add",
        "node_kind": "value_stream",
        "parent_id": "root",
        "value": {"stable_key": "new-stream", "name": "New stream"},
    }

    preview = _preview(_state(), [operation, operation])

    assert preview.applicable is False
    assert "ORGANIZATION_PATCH_STABLE_KEY_CONFLICT" in _reason_codes(preview)


def test_dry_run_blocks_duplicate_relation_identity_even_with_distinct_keys() -> None:
    preview = _preview(
        _state(),
        [
            {
                "op": "connect",
                "namespace": "organization",
                "edge_kind": "declared_dependency",
                "source_id": "stream-a",
                "target_id": "stream-b",
                "relation_key": "dependency-one",
            },
            {
                "op": "connect",
                "namespace": "organization",
                "edge_kind": "declared_dependency",
                "source_id": "stream-a",
                "target_id": "stream-b",
                "relation_key": "dependency-two",
            },
        ],
    )

    assert preview.applicable is False
    assert "ORGANIZATION_RELATION_IDENTITY_DUPLICATE" in _reason_codes(preview)


def test_reparent_aggregates_descendant_activity_and_plans_hub_migration() -> None:
    activity = {
        unit_id: {
            "tasks": int(unit_id == "team-a"),
            "leases": 0,
            "open_gates": 0,
            "handoffs": 0,
            "assignments": 0,
        }
        for unit_id in ("root", "stream-a", "stream-b", "team-a")
    }
    preview = _preview(
        _state(activity_by_unit=activity),
        [
            {
                "op": "reparent",
                "node_id": "stream-a",
                "parent_id": "stream-b",
                "lifecycle_strategy": "migrate",
            }
        ],
    )

    assert preview.applicable is True
    assert "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED" not in _reason_codes(preview)
    assert "active_work:migrate:stream-a" in preview.planned_writes
    assert any(write.startswith("unit:reparent:") for write in preview.planned_writes)


def test_remove_plans_hub_drain_with_descendant_activity() -> None:
    activity = {
        unit_id: {
            "tasks": 0,
            "leases": int(unit_id == "team-a"),
            "open_gates": 0,
            "handoffs": 0,
            "assignments": 0,
        }
        for unit_id in ("root", "stream-a", "stream-b", "team-a")
    }
    preview = _preview(
        _state(activity_by_unit=activity),
        [{"op": "remove", "node_id": "stream-a", "lifecycle_strategy": "drain"}],
    )

    assert preview.applicable is True
    assert "active_work:drain:stream-a" in preview.planned_writes
    assert "unit:archive:stream-a" in preview.planned_writes


def test_remove_migration_requires_an_explicit_successor_binding() -> None:
    try:
        OrganizationTopologyPatchDocument.model_validate(
            {
                "expected_revision": "revision-1",
                "operations": [
                    {
                        "op": "remove",
                        "node_id": "stream-a",
                        "lifecycle_strategy": "migrate",
                    }
                ],
            }
        )
    except ValueError as exc:
        assert "organization_patch_migration_target_required" in str(exc)
    else:  # pragma: no cover - contract must stay closed
        raise AssertionError("missing migration target was accepted")


def test_assignment_blocks_on_agent_global_active_organization_capacity() -> None:
    target_slot = _slot("target-slot", "team-a")
    existing_slot = _slot("existing-slot", "team-a")
    existing_assignment = SimpleNamespace(
        id="existing-assignment",
        role_slot_id=existing_slot.id,
        agent_url="agent-a",
        lifecycle="active",
    )
    preview = _preview(
        _state(
            role_slots=(target_slot, existing_slot),
            assignments=(existing_assignment,),
            agent=_agent(execution_limits={"max_assignments": 1}),
            global_assignment_count=1,
        ),
        [{"op": "assign", "role_slot_id": target_slot.id, "agent_id": "agent-a"}],
    )

    assert preview.applicable is False
    assert "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED" in _reason_codes(preview)


def test_assignment_draft_capacity_is_updated_after_each_accepted_operation() -> None:
    first_slot = _slot("first-slot", "team-a")
    second_slot = _slot("second-slot", "team-a")
    preview = _preview(
        _state(
            role_slots=(first_slot, second_slot),
            agent=_agent(execution_limits={"max_assignments": 1}),
        ),
        [
            {"op": "assign", "role_slot_id": first_slot.id, "agent_id": "agent-a"},
            {"op": "assign", "role_slot_id": second_slot.id, "agent_id": "agent-a"},
        ],
    )

    assert preview.applicable is False
    assert "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED" in _reason_codes(preview)
    assert sum(write.startswith("assignment:create:") for write in preview.planned_writes) == 1


def test_assignment_uses_same_online_fail_closed_status_as_candidate_projection() -> None:
    slot = _slot("target-slot", "team-a")
    preview = _preview(
        _state(role_slots=(slot,), agent=_agent(status="degraded")),
        [{"op": "assign", "role_slot_id": slot.id, "agent_id": "agent-a"}],
    )

    assert preview.applicable is False
    assert "ORGANIZATION_ASSIGNMENT_AGENT_INELIGIBLE" in _reason_codes(preview)


def test_assignment_rejects_invalid_execution_limit_instead_of_guessing_capacity() -> None:
    slot = _slot("target-slot", "team-a")
    preview = _preview(
        _state(
            role_slots=(slot,),
            agent=_agent(execution_limits={"max_assignments": "unknown"}),
        ),
        [{"op": "assign", "role_slot_id": slot.id, "agent_id": "agent-a"}],
    )

    assert preview.applicable is False
    assert "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED" in _reason_codes(preview)

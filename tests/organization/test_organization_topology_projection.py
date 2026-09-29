from agent.services.organization_projection_service import OrganizationProjectionService
from tests.organization_support import organization_limits


class BatchTopologyReader:
    def __init__(self):
        self.calls = []

    def load_topology_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "organization_id": "org-a",
            "name": "Reference Organization",
            "definition_revision": "d" * 64,
            "snapshot_hash": "s" * 64,
            "units": [
                {
                    "id": "portfolio",
                    "unit_key": "portfolio",
                    "unit_kind": "coordination_unit",
                    "parent_unit_key": None,
                },
                {
                    "id": "delivery",
                    "unit_key": "delivery",
                    "unit_kind": "value_stream",
                    "parent_unit_key": "portfolio",
                },
                {
                    "id": "team-a-unit",
                    "unit_key": "team-a",
                    "unit_kind": "team",
                    "parent_unit_key": "delivery",
                    "team_id": "team-a",
                },
                {
                    "id": "team-b-unit",
                    "unit_key": "team-b",
                    "unit_kind": "team",
                    "parent_unit_key": "delivery",
                    "team_id": "team-b",
                },
            ],
            "role_slots": [
                {"id": "slot-a", "unit_id": "team-a-unit", "slot_key": "developer", "default_count": 2},
            ],
            "assignments": [
                {"id": "assignment-a", "role_slot_id": "slot-a", "agent_url": "worker-a"},
            ],
            "relations": [
                {
                    "id": "declared-a-b",
                    "kind": "declared_dependency",
                    "source_unit_key": "team-a",
                    "target_unit_key": "team-b",
                    "definition_relation_ref": "declared-a-b",
                }
            ],
            "runtime_edges": [
                {
                    "id": "runtime-a-b",
                    "kind": "runtime_task_dependency",
                    "source_node_id": "team-a-unit",
                    "target_node_id": "team-b-unit",
                    "runtime_ref": "dependency-1",
                    "provenance_count": 2,
                    "drill_down_refs": ["task-1", "task-2"],
                }
            ],
            "diagnostics": [],
            "next_cursor": None,
        }


def test_projection_derives_hierarchy_and_keeps_runtime_graph_read_only():
    reader = BatchTopologyReader()
    result = OrganizationProjectionService(topology_reader=reader).project(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="org-a",
        limits=organization_limits(),
        include_runtime_overlay=True,
    )

    assert len(reader.calls) == 1
    hierarchy = [edge for edge in result["edges"] if edge["namespace"] == "hierarchy"]
    declared = next(edge for edge in result["edges"] if edge["id"] == "declared-a-b")
    runtime = result["runtime_overlay"]["edges"][0]
    assert len(hierarchy) == len(result["nodes"]) - 1
    assert all("id" in node and "node_id" not in node for node in result["nodes"])
    assert all("source_id" in edge and "target_id" in edge for edge in [*result["edges"], runtime])
    role_slot = next(node for node in result["nodes"] if node["kind"] == "role_slot")
    assert role_slot["metadata"]["default_count"] == 2
    assert declared["namespace"] == "organization"
    assert declared["kind"] == "declared_dependency"
    assert runtime["namespace"] == "runtime"
    assert runtime["kind"] == "runtime_task_dependency"
    assert runtime["read_only"] is True
    assert result["runtime_overlay"]["definition_revision"] == result["definition_revision"]
    assert result["next_cursor"] is None
    assert result["truncated"] is False


class StaticTopologyReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def load_topology_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return self.snapshot


def _filtered_snapshot(**overrides):
    snapshot = {
        "organization_id": "org-a",
        "name": "Reference Organization",
        "definition_revision": "d" * 64,
        "snapshot_hash": "s" * 64,
        "units": [],
        "role_slots": [],
        "assignments": [],
        "relations": [],
        "runtime_edges": [],
        "diagnostics": [],
        "next_cursor": None,
    }
    snapshot.update(overrides)
    return snapshot


def test_exact_role_slot_filter_keeps_only_the_organization_boundary_and_slot():
    reader = StaticTopologyReader(
        _filtered_snapshot(
            role_slots=[
                {
                    "id": "slot-a",
                    "unit_id": "filtered-team-a",
                    "slot_key": "developer",
                }
            ]
        )
    )

    result = OrganizationProjectionService(topology_reader=reader).project(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="org-a",
        limits=organization_limits(),
        filters={"kinds": ["role_slot"]},
    )

    assert {node["kind"] for node in result["nodes"]} == {
        "organization",
        "role_slot",
    }
    root = next(node for node in result["nodes"] if node["kind"] == "organization")
    slot = next(node for node in result["nodes"] if node["kind"] == "role_slot")
    assert root["metadata"]["projection_boundary"] is True
    assert slot["parent_id"] is None
    assert slot["metadata"]["hierarchy_boundary"]["omitted_parent_id"] == "filtered-team-a"
    assert result["edges"] == []
    assert result["diagnostics"][-1]["reason_code"] == "ORGANIZATION_HIERARCHY_PARENT_OMITTED"
    assert reader.calls[0]["filters"] == {"kinds": ["role_slot"]}


def test_cursor_page_omits_and_diagnoses_parent_edge_outside_the_page():
    reader = StaticTopologyReader(
        _filtered_snapshot(
            units=[
                {
                    "id": "team-child",
                    "unit_key": "team-child",
                    "unit_kind": "team",
                    "parent_unit_id": "stream-on-previous-page",
                    "parent_unit_key": "stream-on-previous-page",
                    "depth": 3,
                }
            ],
            next_cursor="team-child",
        )
    )

    result = OrganizationProjectionService(topology_reader=reader).project(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="org-a",
        limits=organization_limits(),
        cursor="stream-on-previous-page",
        page_size=1,
    )

    node_ids = {node["id"] for node in result["nodes"]}
    team = next(node for node in result["nodes"] if node["id"] == "team-child")
    assert team["parent_id"] is None
    assert team["depth"] == 3
    assert all(edge["source_id"] in node_ids and edge["target_id"] in node_ids for edge in result["edges"])
    assert result["diagnostics"][-1]["node_ids"] == ["team-child"]
    assert result["next_cursor"] == "team-child"
    assert result["truncated"] is True

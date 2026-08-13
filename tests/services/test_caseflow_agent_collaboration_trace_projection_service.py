from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA,
    MAX_CASEFLOW_EDGE_MESSAGES,
    MAX_CASEFLOW_EDGE_TELEMETRY,
    MAX_CASEFLOW_TRACE_EVENTS,
    CaseflowAgentCollaborationTraceProjectionService,
    CaseflowEdgeTraceProjectionError,
)
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
)
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.edge_catalog_contract import (
    CASEFLOW_EDGE_CATALOG_METADATA_KEY,
    CASEFLOW_EDGE_CATALOG_SCHEMA,
    MAX_CASEFLOW_EDGE_CATALOG_SIZE,
    build_caseflow_edge_catalog,
)
from agent.visual_process.models import (
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)


class _History:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.calls = 0

    def list_workflow_events(self, _workflow_id: str) -> list[dict]:
        self.calls += 1
        return deepcopy(self.events)


def _catalog(*edges: tuple[str, str, str]) -> dict:
    return build_caseflow_edge_catalog(
        [
            {
                "edge_id": edge_id,
                "source_step_id": source,
                "target_step_id": target,
            }
            for edge_id, source, target in edges
        ]
    )


def _binding(
    catalog: dict | None,
    *,
    tenant_id: str = "tenant-a",
    subject_id: str = "owner-a",
    workflow_id: str = "workflow-a",
    run_id: str = "run-a",
) -> WorkflowControlRunBinding:
    metadata = (
        {CASEFLOW_EDGE_CATALOG_METADATA_KEY: catalog}
        if catalog is not None
        else {}
    )
    catalog_edges = list(catalog.get("edges") or []) if catalog else []
    step_ids = sorted(
        {
            str(value)
            for edge in catalog_edges
            for value in (edge.get("source_step_id"), edge.get("target_step_id"))
            if value
        }
        or {"agent-a"}
    )
    dependencies = {
        step_id: tuple(sorted({
            str(edge["source_step_id"])
            for edge in catalog_edges
            if edge.get("target_step_id") == step_id
        }))
        for step_id in step_ids
    }
    request = WorkflowRequest(
        workflow_id=workflow_id,
        steps=tuple(
            WorkflowStepRequest(
                step_id=step_id,
                depends_on=dependencies[step_id],
                policy_scope={"source": "caseflow-test"},
            )
            for step_id in step_ids
        ),
        policy_scope={"source": "caseflow-test"},
        metadata=metadata,
    )
    return WorkflowControlRunBinding(
        tenant_id=tenant_id,
        subject_id=subject_id,
        workflow_id=workflow_id,
        run_id=run_id,
        runtime_id="native",
        plan_hash="plan-hash",
        policy_version="policy-v1",
        checkpoint_id="checkpoint-a",
        request=request,
    )


def _service(binding: WorkflowControlRunBinding):
    store = InMemoryWorkflowControlBindingStore()
    store.put(binding)
    return CaseflowAgentCollaborationTraceProjectionService(store)


def test_graph_mapper_preserves_exact_directional_edge_catalog_without_replacing_metadata() -> None:
    graph = VisualProcessGraph(
        id="workflow-a",
        name="Agent collaboration",
        metadata={"existing_extension": {"enabled": True}},
        steps=[
            VisualProcessStep(id="agent-a", label="Agent A"),
            VisualProcessStep(id="agent-b", label="Agent B"),
        ],
        edges=[
            VisualProcessEdge(
                id="edge-b-a",
                source="agent-b",
                target="agent-a",
                condition={
                    "kind": "back_edge",
                    "loop_policy": {"kind": "fixed", "max_iterations": 1},
                },
            ),
            VisualProcessEdge(id="edge-a-b", source="agent-a", target="agent-b"),
        ],
    )

    workflow = graph_to_workflow_request(
        graph,
        policy_scope={"source": "caseflow-test"},
    )

    assert workflow.metadata["existing_extension"] == {"enabled": True}
    assert workflow.metadata[CASEFLOW_EDGE_CATALOG_METADATA_KEY] == {
        "schema": CASEFLOW_EDGE_CATALOG_SCHEMA,
        "complete": True,
        "edges": [
            {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
                "edge_kind": "dependency",
            },
            {
                "edge_id": "edge-b-a",
                "source_step_id": "agent-b",
                "target_step_id": "agent-a",
                "edge_kind": "back_edge",
            },
        ],
    }
    assert {step.step_id: step.depends_on for step in workflow.steps} == {
        "agent-a": (),
        "agent-b": ("agent-a",),
    }
    binding = replace(_binding(None), request=workflow)
    projection = _service(binding).project(binding=binding, raw_events=[])
    assert projection["catalog_verification_status"] == "verified"
    assert [edge["edge_id"] for edge in projection["edges"]] == [
        "edge-a-b",
        "edge-b-a",
    ]


def test_edge_catalog_rejects_lossy_or_ambiguous_identity() -> None:
    with pytest.raises(ValueError, match="caseflow_edge_id_invalid"):
        build_caseflow_edge_catalog(
            [{"edge_id": " edge-a-b", "source": "agent-a", "target": "agent-b"}]
        )
    with pytest.raises(ValueError, match="caseflow_edge_catalog_duplicate_edge_id"):
        build_caseflow_edge_catalog(
            [
                {"edge_id": "edge-a-b", "source": "agent-a", "target": "agent-b"},
                {"edge_id": "edge-a-b", "source": "agent-b", "target": "agent-a"},
            ]
        )
    with pytest.raises(ValueError, match="caseflow_edge_catalog_limit_exceeded"):
        build_caseflow_edge_catalog(
            [
                {
                    "edge_id": f"edge-{index}",
                    "source": "agent-a",
                    "target": "agent-b",
                }
                for index in range(MAX_CASEFLOW_EDGE_CATALOG_SIZE + 1)
            ]
        )


def test_directional_projection_uses_only_explicit_existing_evidence_and_redacts_messages() -> None:
    binding = _binding(
        _catalog(
            ("edge-a-b", "agent-a", "agent-b"),
            ("edge-b-a", "agent-b", "agent-a"),
        )
    )
    events = [
        {
            "schema": "ananta.workflow_event.v1",
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": "event-b-a-message",
            "event_type": "workflow.edge.message.sent",
            "sequence": 2,
            "step_id": "agent-b",
            "correlation_id": "correlation-existing",
            "payload": {
                "edge_id": "edge-b-a",
                "source_step_id": "agent-b",
                "target_step_id": "agent-a",
                "trace_id": "trace-existing",
                "message": "api_key=do-not-return",
                "provider": "local-provider",
                "token_usage": {
                    "input_tokens": 3,
                    "api_key": "plain-leaked-value",
                },
            },
        },
        {
            "workflow_id": "workflow-a",
            "event_type": "workflow.status.updated",
            "details": {"current_step_ids": ["agent-b"]},
            "timestamp": 1,
        },
    ]

    projection = _service(binding).project(binding=binding, raw_events=events)
    edges = {edge["edge_id"]: edge for edge in projection["edges"]}

    assert projection["schema"] == CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA
    assert edges["edge-b-a"]["activity_status"] == "active"
    assert edges["edge-b-a"]["verification_status"] == "verified"
    assert edges["edge-b-a"]["event_refs"] == ["event-b-a-message"]
    assert edges["edge-b-a"]["trace_refs"] == ["trace-existing"]
    assert edges["edge-b-a"]["messages"][0]["correlation_ref"] == "trace-existing"
    assert "do-not-return" not in str(edges["edge-b-a"])
    assert "api_key=***" in edges["edge-b-a"]["messages"][0]["content"]
    assert edges["edge-b-a"]["telemetry"][0]["token_usage"] == {
        "input_tokens": 3
    }
    assert "plain-leaked-value" not in str(projection)
    assert "api_key" not in str(edges["edge-b-a"]["telemetry"])
    assert edges["edge-a-b"]["activity_status"] == "unknown"
    assert edges["edge-a-b"]["verification_status"] == "unverified"
    assert edges["edge-a-b"]["event_refs"] == []
    assert edges["edge-a-b"]["trace_refs"] == []


def test_unique_incoming_dependency_requires_source_and_target_events_not_current_steps() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    source_completed = {
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "event_id": "event-source-completed",
        "event_type": "workflow.step.completed",
        "step_id": "agent-a",
        "sequence": 1,
        "payload": {},
    }
    target_started = {
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "event_id": "event-target-delegated",
        "event_type": "workflow.step.delegated",
        "step_id": "agent-b",
        "sequence": 2,
        "payload": {},
    }

    missing_source = _service(binding).project(
        binding=binding,
        raw_events=[
            target_started,
            {
                "workflow_id": "workflow-a",
                "run_id": "run-a",
                "event_type": "workflow.status.updated",
                "payload": {"current_step_ids": ["agent-b"]},
            },
        ],
    )
    correlated = _service(binding).project(
        binding=binding,
        raw_events=[target_started, source_completed],
    )

    assert missing_source["edges"][0]["activity_status"] == "unknown"
    assert missing_source["edges"][0]["verification_status"] == "unverified"
    assert correlated["edges"][0]["activity_status"] == "active"
    assert correlated["edges"][0]["correlation_basis"] == (
        "unique_dependency_event_sequence"
    )
    assert correlated["edges"][0]["event_refs"] == [
        "event-source-completed",
        "event-target-delegated",
    ]


def test_conflicting_direction_is_unknown_and_unverified() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    projection = _service(binding).project(
        binding=binding,
        raw_events=[
            {
                "workflow_id": "workflow-a",
                "run_id": "run-a",
                "event_id": "event-conflict",
                "event_type": "workflow.edge.message.sent",
                "payload": {
                    "edge_id": "edge-a-b",
                    "source_step_id": "agent-b",
                    "target_step_id": "agent-a",
                    "message": "wrong direction",
                },
            }
        ],
    )

    edge = projection["edges"][0]
    assert edge["activity_status"] == "unknown"
    assert edge["verification_status"] == "unverified"
    assert edge["reason_code"] == "caseflow_edge_correlation_conflicting"
    assert edge["messages"] == []


def test_projection_order_and_output_limits_are_deterministic() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    events = [
        {
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": f"event-{index:03d}",
            "event_type": "workflow.edge.message.sent",
            "sequence": index + 1,
            "payload": {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
                "message": f"message-{index:03d}",
            },
        }
        for index in range(MAX_CASEFLOW_EDGE_TELEMETRY + 5)
    ]

    forward = _service(binding).project(binding=binding, raw_events=events)
    reverse = _service(binding).project(
        binding=binding,
        raw_events=list(reversed(events)),
    )

    assert forward == reverse
    edge = forward["edges"][0]
    assert len(edge["messages"]) == MAX_CASEFLOW_EDGE_MESSAGES
    assert len(edge["telemetry"]) == MAX_CASEFLOW_EDGE_TELEMETRY
    assert edge["messages"][0]["content"] == "message-000"
    assert edge["limits"]["messages_truncated"] == (
        len(events) - MAX_CASEFLOW_EDGE_MESSAGES
    )
    assert edge["limits"]["telemetry_truncated"] == 5


def test_equal_event_order_keys_are_content_deterministic() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    events = [
        {
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": "event-tie",
            "event_type": "workflow.edge.message.sent",
            "sequence": 1,
            "timestamp": 10,
            "payload": {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
                "message": message,
            },
        }
        for message in ("first", "second")
    ]

    forward = _service(binding).project(binding=binding, raw_events=events)
    reverse = _service(binding).project(binding=binding, raw_events=list(reversed(events)))

    assert forward == reverse
    assert sorted(
        item["content"] for item in forward["edges"][0]["messages"]
    ) == ["first", "second"]


@pytest.mark.parametrize(
    "event",
    [
        {
            "edge_id": "edge-a-b",
            "source_step_id": " agent-a",
            "target_step_id": "agent-b",
        },
        {
            "edge_id": "edge-a-b",
            "source_step_id": "agent-a",
            "target_step_id": "agent-b",
            "payload_source_step_id": "agent-other",
        },
    ],
)
def test_malformed_or_conflicting_direction_is_rejected(event: dict) -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    top_level = dict(event)
    payload_source = top_level.pop("payload_source_step_id", top_level["source_step_id"])
    payload = {
        "edge_id": top_level["edge_id"],
        "source_step_id": payload_source,
        "target_step_id": top_level["target_step_id"],
        "message": "must not correlate",
    }
    projection = _service(binding).project(
        binding=binding,
        raw_events=[
            {
                "workflow_id": "workflow-a",
                "run_id": "run-a",
                "event_type": "workflow.edge.message.sent",
                **top_level,
                "payload": payload,
            }
        ],
    )

    assert projection["edges"][0]["verification_status"] == "unverified"
    assert projection["edges"][0]["activity_status"] == "unknown"
    assert projection["telemetry"]["rejected_event_count"] == 1


def test_later_unique_target_terminal_event_overrides_explicit_active_edge() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    events = [
        {
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": "event-message",
            "event_type": "workflow.edge.message.sent",
            "sequence": 1,
            "payload": {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
                "message": "delegated",
            },
        },
        {
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": "event-target-completed",
            "event_type": "workflow.step.completed",
            "step_id": "agent-b",
            "sequence": 2,
            "payload": {},
        },
    ]

    forward = _service(binding).project(binding=binding, raw_events=events)
    reverse = _service(binding).project(binding=binding, raw_events=list(reversed(events)))

    assert forward == reverse
    edge = forward["edges"][0]
    assert edge["verification_status"] == "verified"
    assert edge["activity_status"] == "inactive"
    assert edge["event_refs"] == ["event-message", "event-target-completed"]


def test_catalog_must_match_bound_workflow_steps_and_dependencies() -> None:
    catalog = _catalog(("edge-a-b", "agent-a", "agent-b"))
    unknown_endpoint = _binding(catalog)
    unknown_endpoint = replace(
        unknown_endpoint,
        request=WorkflowRequest(
            workflow_id="workflow-a",
            steps=(WorkflowStepRequest(step_id="agent-a"),),
            metadata={CASEFLOW_EDGE_CATALOG_METADATA_KEY: catalog},
        ),
    )
    missing_dependency = _binding(catalog)
    missing_dependency = replace(
        missing_dependency,
        request=WorkflowRequest(
            workflow_id="workflow-a",
            steps=(
                WorkflowStepRequest(step_id="agent-a"),
                WorkflowStepRequest(step_id="agent-b"),
            ),
            metadata={CASEFLOW_EDGE_CATALOG_METADATA_KEY: catalog},
        ),
    )

    for binding in (unknown_endpoint, missing_dependency):
        projection = _service(binding).project(binding=binding, raw_events=[])
        assert projection["verification_status"] == "unverified"
        assert projection["reason_code"] == "caseflow_edge_catalog_topology_mismatch"
        assert projection["edges"] == []


def test_history_is_windowed_before_projection_work() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    events = [
        {
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "event_id": f"event-{index:05d}",
            "event_type": "workflow.edge.message.sent",
            "sequence": index + 1,
            "payload": {
                "edge_id": "edge-a-b",
                "source_step_id": "agent-a",
                "target_step_id": "agent-b",
                "message": f"message-{index:05d}",
            },
        }
        for index in range(MAX_CASEFLOW_EDGE_TELEMETRY + 2_100)
    ]

    projection = _service(binding).project(binding=binding, raw_events=events)

    assert projection["telemetry"]["source_event_count"] == len(events)
    assert projection["telemetry"]["processed_event_count"] == MAX_CASEFLOW_TRACE_EVENTS
    assert projection["telemetry"]["truncated_event_count"] == (
        len(events) - MAX_CASEFLOW_TRACE_EVENTS
    )


def test_read_validates_tenant_subject_workflow_and_run_before_history_access() -> None:
    binding = _binding(_catalog(("edge-a-b", "agent-a", "agent-b")))
    service = _service(binding)
    history = _History([])
    owner = WorkflowRoutePrincipal(tenant_id="tenant-a", subject="owner-a")

    allowed = service.read(
        principal=owner,
        workflow_id="workflow-a",
        run_id="run-a",
        history=history,
    )
    assert allowed["workflow_id"] == "workflow-a"
    assert allowed["run_id"] == "run-a"
    assert history.calls == 1

    for principal, run_id in (
        (WorkflowRoutePrincipal("tenant-b", "owner-a"), "run-a"),
        (WorkflowRoutePrincipal("tenant-a", "other"), "run-a"),
        (owner, "run-unknown"),
    ):
        with pytest.raises(
            CaseflowEdgeTraceProjectionError,
            match="caseflow_workflow_run_not_found",
        ):
            service.read(
                principal=principal,
                workflow_id="workflow-a",
                run_id=run_id,
                history=history,
            )
    assert history.calls == 1


def test_missing_catalog_and_missing_evidence_never_create_references() -> None:
    binding = _binding(None)
    projection = _service(binding).project(binding=binding, raw_events=[])

    assert projection["verification_status"] == "unverified"
    assert projection["reason_code"] == "caseflow_edge_catalog_unavailable"
    assert projection["edges"] == []
    assert projection["telemetry"]["processed_event_count"] == 0

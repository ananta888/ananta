from __future__ import annotations

from typing import TypedDict

import pytest

langgraph_graph = pytest.importorskip("langgraph.graph")

from langchain_core.messages import HumanMessage  # noqa: E402

from agent.services.langgraph_checkpoint_gateway_service import (  # noqa: E402
    LangGraphCheckpointGatewayService,
)
from agent.services.workflow_runtime import (  # noqa: E402
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryExecutionOwnershipStore,
    RuntimeAuthorizationEnvelope,
)
from ananta_contracts.langgraph_checkpoint import (  # noqa: E402
    LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
    LangGraphCheckpointBinding,
    LangGraphCheckpointSnapshot,
)
from worker.adapters.langgraph_checkpoint_adapter import (  # noqa: E402
    LangGraphHubOwnedCheckpointer,
)


class _State(TypedDict):
    count: int
    messages: list[object]


class _ServiceGateway:
    def __init__(
        self,
        *,
        service: LangGraphCheckpointGatewayService,
        binding: LangGraphCheckpointBinding,
    ) -> None:
        self._service = service
        self._binding = binding

    def _execute(self, operation: str, **values):
        return self._service.execute(
            {
                "schema": LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
                "operation": operation,
                "binding": self._binding.to_dict(),
                **values,
            }
        )

    def get(self, *, binding, config):
        del binding
        raw = self._execute("get", config=dict(config))["snapshot"]
        return LangGraphCheckpointSnapshot.from_mapping(raw) if raw else None

    def list(self, *, binding, config, metadata_filter, before_config, limit):
        del binding
        raw = self._execute(
            "list",
            config=dict(config),
            metadata_filter=dict(metadata_filter),
            before_config=dict(before_config) if before_config else None,
            limit=limit,
        )["snapshots"]
        return tuple(LangGraphCheckpointSnapshot.from_mapping(item) for item in raw)

    def put(self, *, binding, config, checkpoint, metadata, expected_revision):
        del binding
        raw = self._execute(
            "put",
            config=dict(config),
            checkpoint=dict(checkpoint),
            metadata=dict(metadata),
            expected_revision=expected_revision,
        )["snapshot"]
        return LangGraphCheckpointSnapshot.from_mapping(raw)

    def put_writes(self, *, binding, config, pending_writes, expected_revision):
        del binding
        raw = self._execute(
            "put_writes",
            config=dict(config),
            pending_writes=[list(item) for item in pending_writes],
            expected_revision=expected_revision,
        )["snapshot"]
        return LangGraphCheckpointSnapshot.from_mapping(raw)


def test_real_langgraph_0_2_saver_persists_and_recovers_through_hub() -> None:
    key_ring = HmacKeyRing({"runtime-v1": "k" * 32}, active_key_id="runtime-v1")
    ownership = InMemoryExecutionOwnershipStore()
    claim = ownership.claim(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        owner_id="worker-a",
        lease_seconds=600,
        maximum_retries=3,
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        plan_hash="plan-a",
        policy_version="policy-a",
        ttl_seconds=600,
    )
    binding = LangGraphCheckpointBinding.from_mapping(
        {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "step_id": "step-a",
            "task_id": "step-a",
            "plan_hash": "plan-a",
            "policy_version": "policy-a",
            "fencing_token": claim.ownership.fencing_token,
            "authorization_envelope": envelope.to_dict(),
        }
    )
    service = LangGraphCheckpointGatewayService(
        checkpoints=InMemoryCheckpointStore(),
        ownership=ownership,
        key_ring=key_ring,
        authorization=AuthorizationVerifier(key_ring),
    )
    gateway = _ServiceGateway(service=service, binding=binding)

    builder = langgraph_graph.StateGraph(_State)
    builder.add_node("increment", lambda state: {"count": state["count"] + 1})
    builder.set_entry_point("increment")
    builder.add_edge("increment", langgraph_graph.END)
    config = {"configurable": {"thread_id": "step-a", "checkpoint_ns": ""}}
    first = builder.compile(
        checkpointer=LangGraphHubOwnedCheckpointer(
            gateway=gateway,
            binding=binding,
        )
    )

    message = HumanMessage(content="checkpoint me")
    assert first.invoke({"count": 0, "messages": [message]}, config=config) == {
        "count": 1,
        "messages": [message],
    }

    recovered = builder.compile(
        checkpointer=LangGraphHubOwnedCheckpointer(
            gateway=gateway,
            binding=binding,
        )
    )
    recovered_state = recovered.get_state(config)
    assert recovered_state.values == {"count": 1, "messages": [message]}
    assert recovered.invoke(None, config=config) == {
        "count": 1,
        "messages": [message],
    }

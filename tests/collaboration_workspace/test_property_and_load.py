from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from pathlib import Path

import jsonschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent.services.collaboration_search_service import CollaborationSearchService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_resources import AgentIntentV1, SharedResourceOfferV1
from ananta_contracts.collaboration_workspace import CollaborationWorkspaceV1, canonical_digest
from tests.collaboration_workspace.helpers import actor, build_event, room, service

_EXAMPLE = count()


def _setup(database: Path):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Properties",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    return workspaces


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    st.lists(
        st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=50),
        min_size=1,
        max_size=15,
    )
)
def test_generated_event_replays_preserve_monotonic_sequence(tmp_path: Path, texts: list[str]) -> None:
    workspaces = _setup(tmp_path / f"state-{next(_EXAMPLE)}.sqlite3")
    events = []
    for index, text in enumerate(texts):
        event = build_event(
            workspace_id="workspace-a",
            room_id="room-main",
            actor_binding_id="human-user-a",
            event_type="message.posted",
            payload={"text": text},
            idempotency_key=f"generated-{index}",
        )
        first = workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=event,
        )
        replay = workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=event,
        )
        assert replay["event_id"] == first["event_id"]
        assert replay["replayed"] is True
        events.append(first)
    assert [event["sequence"] for event in events] == list(range(1, len(texts) + 1))


def test_parallel_appends_have_unique_gap_free_workspace_sequence(tmp_path: Path) -> None:
    database = tmp_path / "parallel.sqlite3"
    _setup(database)

    def append(index: int) -> int:
        workspaces = service(database)
        result = workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=build_event(
                workspace_id="workspace-a",
                room_id="room-main",
                actor_binding_id="human-user-a",
                event_type="message.posted",
                payload={"text": f"parallel-{index}"},
                idempotency_key=f"parallel-{index}",
            ),
        )
        return result["sequence"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(40)))
    assert sorted(sequences) == list(range(1, 41))


def test_local_projection_load_is_bounded_and_deterministic(tmp_path: Path) -> None:
    database = tmp_path / "load.sqlite3"
    workspaces = _setup(database)
    for index in range(100):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=build_event(
                workspace_id="workspace-a",
                room_id="room-main",
                actor_binding_id="human-user-a",
                event_type="message.posted",
                payload={"text": f"load-marker-{index}"},
                idempotency_key=f"load-{index}",
            ),
        )
    store = CollaborationWorkspaceStore(database)
    search = CollaborationSearchService(store, policy=CollaborationWorkspacePolicy())
    full = search.rebuild("tenant-a", "workspace-a", mode="full")
    incremental = search.rebuild("tenant-a", "workspace-a", mode="incremental")
    assert full["document_count"] == 100
    assert full["checkpoint"] == 100
    assert full["index_digest"] == incremental["index_digest"]


def test_python_contracts_validate_against_public_json_schemas() -> None:
    workspace = {
        "schema": "ananta.collaboration-workspace.v1",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "project_id": None,
        "title": "Workspace",
        "state": "active",
        "retention": "standard",
        "revision": 1,
        "created_by": "actor-a",
        "created_at": 100.0,
        "native_core": True,
        "bridge_required": False,
        "human_intervention_required": False,
    }
    offer = {
        "schema": "ananta.collaboration-resource-offer.v1",
        "offer_id": "offer-a",
        "workspace_id": "workspace-a",
        "owner_actor_binding_id": "actor-a",
        "resource_id": "resource-a",
        "capability_category": "compute",
        "capacity_class": "small",
        "scopes": ["compute.run"],
        "expires_at": 100.0,
        "sensitivity": "workspace",
        "attestation_status": "verified",
        "metadata": {},
    }
    intent_payload = {"request": "help"}
    intent = {
        "schema": "ananta.collaboration-agent-intent.v1",
        "intent_id": "intent-a",
        "workspace_id": "workspace-a",
        "room_id": "room-a",
        "actor_binding_id": "actor-a",
        "intent_type": "mention",
        "target_actor_binding_id": "agent-a",
        "task_id": None,
        "correlation_id": "correlation-a",
        "causation_id": None,
        "hop_count": 0,
        "payload": intent_payload,
        "payload_digest": canonical_digest(intent_payload),
    }
    for filename, value in (
        ("workspace.v1.json", workspace),
        ("resource-offer.v1.json", offer),
        ("agent-intent.v1.json", intent),
    ):
        schema = json.loads(Path("schemas/collaboration", filename).read_text())
        jsonschema.Draft202012Validator(schema).validate(value)
    assert SharedResourceOfferV1.from_mapping(offer).to_dict() == offer
    assert AgentIntentV1.from_mapping(intent).to_dict() == intent
    assert CollaborationWorkspaceV1.from_mapping(workspace).to_dict() == workspace

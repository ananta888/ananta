from __future__ import annotations

from agent.services.semantic_fanout_coordination_service import (
    ReceiverRouteRequest,
    SemanticFanoutCoordinationService,
)


def request(receiver: str, path: str, **overrides):
    values = {
        "receiver_id": receiver,
        "requested_path": path,
        "sfu_authorized": True,
        "ordinary_authorized": True,
        "semantic_authorized": True,
        "semantic_capable": True,
        "semantic_contract_active": True,
    }
    values.update(overrides)
    return ReceiverRouteRequest(**values)


def test_three_receivers_share_one_upload_but_keep_independent_effective_paths() -> None:
    plan = SemanticFanoutCoordinationService().plan(
        publication_id="camera-alice",
        receivers=(
            request("bob", "semantic"),
            request("carol", "ordinary"),
            request("dave", "semantic", semantic_capable=False),
        ),
        private_recovery_audience={"bob": True},
    )
    assert plan.upload_count == 1
    assert [(row.receiver_id, row.path) for row in plan.routes] == [
        ("bob", "semantic_sfu"), ("carol", "ordinary_sfu"), ("dave", "ordinary_sfu")
    ]
    assert plan.routes[0].private_recovery_authorized is True
    assert all(not row.private_recovery_authorized for row in plan.routes[1:])


def test_weak_receiver_neither_expands_rights_nor_reduces_other_receiver() -> None:
    plan = SemanticFanoutCoordinationService().plan(
        publication_id="microphone-alice",
        receivers=(
            request("bob", "semantic"),
            request("carol", "semantic", sfu_authorized=False, semantic_authorized=False),
        ),
    )
    routes = {row.receiver_id: row for row in plan.routes}
    assert routes["bob"].path == "semantic_sfu"
    assert routes["carol"].path == "ordinary_direct"
    assert routes["carol"].reason_code == "semantic_fanout_safe_fallback"

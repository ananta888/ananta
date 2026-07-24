from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from ananta_contracts.kanban import (
    AssignCardCommand,
    BlockCardCommand,
    CommentCardCommand,
    CompleteCardCommand,
    CreateBoardCommand,
    CreateCardCommand,
    KanbanCard,
    KanbanCapability,
    KanbanColumnId,
    KanbanComment,
    MoveCardCommand,
    SetDependenciesCommand,
)
from ananta_contracts.model_catalog import (
    MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
    ModelAvailability,
    ModelCatalog,
    ModelHealth,
    ModelRuntime,
    ModelSummary,
)
from agent.database import engine
from agent.db_models.planning import GoalDB
from agent.services.kanban_authorization_service import (
    KanbanAuthorizationService,
    KanbanPrincipal,
)
from agent.services.surface_rate_limit_policy import surface_rate_limit_policy


def _enable_surfaces(app) -> None:
    app.config["KANBAN_API_ENABLED"] = True
    app.config["KANBAN_WRITE_ENABLED"] = True
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG", {}) or {}),
        "feature_angular_model_dashboard_enabled": True,
    }


@pytest.fixture(autouse=True)
def _clear_surface_limits():
    surface_rate_limit_policy.clear()
    yield
    surface_rate_limit_policy.clear()


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            CreateCardCommand,
            {
                "title": "<script>alert(1)</script>",
                "idempotency_key": "create-xss",
            },
        ),
        (
            CommentCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "comment-xss",
                "body": "<img src=x onerror=alert(1)>",
            },
        ),
        (
            BlockCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "block-url",
                "reason": "java\nscript:alert(1)",
            },
        ),
        (
            CompleteCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "complete-url",
                "outcome": "data:text/html,<svg onload=alert(1)>",
            },
        ),
    ],
)
def test_executable_html_and_url_payloads_are_rejected(command, payload) -> None:
    with pytest.raises(ValidationError):
        command.model_validate(payload)


def test_legacy_response_models_roundtrip_unsafe_historic_text() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    card = KanbanCard(
        id="legacy-card",
        board_id="hub",
        title="<b>historic title</b>",
        description="javascript:historic()",
        status="created",
        column_id=KanbanColumnId.TODO,
        position=0,
        revision=3,
        priority="Medium",
        created_at=timestamp,
        updated_at=timestamp,
    )
    comment = KanbanComment(
        id="legacy-comment",
        card_id=card.id,
        author_id="legacy-user",
        body="<script>historic()</script>",
        created_at=timestamp,
    )

    assert KanbanCard.model_validate(card.model_dump()).title == card.title
    assert KanbanComment.model_validate(comment.model_dump()).body == comment.body


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            CreateBoardCommand,
            {"scope_type": "hub", "idempotency_key": "board"},
        ),
        (
            CreateCardCommand,
            {"title": "card", "idempotency_key": "card"},
        ),
        (
            MoveCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "move",
                "column_id": "in_progress",
                "position": 0,
            },
        ),
        (
            AssignCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "assign",
                "assignee_id": "worker",
            },
        ),
        (
            CommentCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "comment",
                "body": "safe",
            },
        ),
        (
            SetDependenciesCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "dependencies",
                "dependencies": [],
            },
        ),
        (
            BlockCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "block",
                "reason": "safe",
            },
        ),
        (
            CompleteCardCommand,
            {
                "board_id": "hub",
                "expected_revision": 1,
                "idempotency_key": "complete",
                "outcome": "safe",
            },
        ),
    ],
)
def test_every_write_contract_rejects_free_transport_fields(
    command,
    payload,
) -> None:
    with pytest.raises(ValidationError):
        command.model_validate(
            {
                **payload,
                "callback_url": "http://127.0.0.1",
                "shell_args": ["sh", "-c", "id"],
            }
        )


def test_refresh_rejects_ssrf_path_and_shell_fields_before_discovery(
    app,
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    _enable_surfaces(app)
    called = False

    def forbidden_discovery():
        nonlocal called
        called = True
        raise AssertionError("discovery must not run for invalid input")

    monkeypatch.setattr(
        "agent.routes.config.providers._model_catalog_service",
        forbidden_discovery,
    )

    for payload in (
        {"base_url": "http://169.254.169.254/latest/meta-data"},
        {"path": "/etc/passwd"},
        {"shell_args": ["sh", "-c", "id"]},
    ):
        response = client.post(
            "/models/catalog/v1/refresh",
            headers=admin_auth_header,
            json=payload,
        )
        assert response.status_code == 400
        assert response.get_json()["message"] == (
            "model_catalog_refresh_command_invalid"
        )
    assert called is False


def test_catalog_rejects_free_url_query_parameter(
    app,
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    _enable_surfaces(app)
    called = False

    def forbidden_discovery():
        nonlocal called
        called = True
        raise AssertionError("discovery must not run for invalid input")

    monkeypatch.setattr(
        "agent.routes.config.providers._model_catalog_service",
        forbidden_discovery,
    )
    response = client.get(
        "/models/catalog/v1?callback_url=http://127.0.0.1",
        headers=admin_auth_header,
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "model_catalog_query_invalid"
    assert called is False


def test_kanban_contract_rejects_free_path_and_shell_fields(
    app,
    client,
    admin_auth_header,
) -> None:
    _enable_surfaces(app)
    board_response = client.post(
        "/api/v1/kanban/boards",
        headers=admin_auth_header,
        json={"scope_type": "hub", "idempotency_key": "security-board"},
    )
    assert board_response.status_code == 201
    board_id = board_response.get_json()["data"]["id"]

    response = client.post(
        f"/api/v1/kanban/boards/{board_id}/cards",
        headers=admin_auth_header,
        json={
            "title": "must not execute",
            "idempotency_key": "bad-card-extras",
            "callback_url": "http://127.0.0.1",
            "path": "/etc/passwd",
            "shell_args": ["sh", "-c", "id"],
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "kanban_request_invalid"


def test_auth_disabled_kanban_write_is_denied(
    app,
    client,
    monkeypatch,
) -> None:
    _enable_surfaces(app)
    app.config["AGENT_TOKEN"] = ""
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    response = client.post(
        "/api/v1/kanban/boards",
        json={"scope_type": "hub", "idempotency_key": "anonymous-board"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "kanban_auth_required"


def test_auth_disabled_model_write_is_denied(
    app,
    client,
    monkeypatch,
) -> None:
    _enable_surfaces(app)
    app.config["AGENT_TOKEN"] = ""
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    response = client.post(
        "/models/catalog/v1/refresh",
        json={},
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == (
        "model_catalog_capability_required"
    )


def _create_board(client, headers, key: str) -> str:
    response = client.post(
        "/api/v1/kanban/boards",
        headers=headers,
        json={"scope_type": "hub", "idempotency_key": key},
    )
    assert response.status_code == 201
    return response.get_json()["data"]["id"]


def _create_card(client, headers, board_id: str, title: str, key: str) -> dict:
    response = client.post(
        f"/api/v1/kanban/boards/{board_id}/cards",
        headers=headers,
        json={"title": title, "idempotency_key": key},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def test_hub_board_is_concealed_from_non_admin_identity(
    app,
    client,
    admin_auth_header,
    user_auth_header,
) -> None:
    _enable_surfaces(app)
    board_id = _create_board(
        client,
        admin_auth_header,
        "idor-hub-board",
    )

    response = client.get(
        f"/api/v1/kanban/boards/{board_id}",
        headers=user_auth_header,
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "kanban_board_not_found"


def test_revision_replay_cycle_and_invalid_transition_are_rejected(
    app,
    client,
    admin_auth_header,
) -> None:
    _enable_surfaces(app)
    board_id = _create_board(client, admin_auth_header, "mutation-board")
    first = _create_card(
        client,
        admin_auth_header,
        board_id,
        "first",
        "mutation-first",
    )
    second = _create_card(
        client,
        admin_auth_header,
        board_id,
        "second",
        "mutation-second",
    )

    move_payload = {
        "board_id": board_id,
        "expected_revision": first["revision"],
        "idempotency_key": "move-first",
        "column_id": "in_progress",
        "position": 0,
    }
    moved = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/move",
        headers=admin_auth_header,
        json=move_payload,
    )
    assert moved.status_code == 200
    replay = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/move",
        headers=admin_auth_header,
        json=move_payload,
    )
    assert replay.status_code == 200
    assert replay.get_json()["data"]["revision"] == (
        moved.get_json()["data"]["revision"]
    )

    stale = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/move",
        headers=admin_auth_header,
        json={
            **move_payload,
            "idempotency_key": "stale-first",
            "column_id": "blocked",
        },
    )
    assert stale.status_code == 409
    assert stale.get_json()["error"]["code"] == "kanban_revision_conflict"

    first_revision = moved.get_json()["data"]["revision"]
    first_dependencies = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/set-dependencies",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": first_revision,
            "idempotency_key": "first-depends-second",
            "dependencies": [second["id"]],
        },
    )
    assert first_dependencies.status_code == 200
    current_second = client.get(
        (
            f"/api/v1/kanban/boards/{board_id}/cards/"
            f"{second['id']}"
        ),
        headers=admin_auth_header,
    )
    assert current_second.status_code == 200
    cycle = client.post(
        f"/api/v1/kanban/cards/{second['id']}/commands/set-dependencies",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": current_second.get_json()["data"]["revision"],
            "idempotency_key": "second-depends-first",
            "dependencies": [first["id"]],
        },
    )
    assert cycle.status_code == 409
    assert cycle.get_json()["error"]["code"] == "kanban_dependency_cycle"

    completed = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/complete",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": first_dependencies.get_json()["data"][
                "revision"
            ],
            "idempotency_key": "complete-first",
            "outcome": "complete",
        },
    )
    assert completed.status_code == 200
    invalid = client.post(
        f"/api/v1/kanban/cards/{first['id']}/commands/move",
        headers=admin_auth_header,
        json={
            "board_id": board_id,
            "expected_revision": completed.get_json()["data"]["revision"],
            "idempotency_key": "reopen-first",
            "column_id": "in_progress",
            "position": 0,
        },
    )
    assert invalid.status_code == 409
    assert invalid.get_json()["error"]["code"] == "kanban_transition_invalid"


def test_foreign_goal_scope_is_concealed_and_capabilities_do_not_escalate(
    app,
    client,
    user_auth_header,
) -> None:
    _enable_surfaces(app)
    goal_id = "security-foreign-goal"
    goal = GoalDB(
        id=goal_id,
        goal="foreign goal",
        requested_by="different-user",
    )
    with Session(engine) as session:
        session.add(goal)
        session.commit()

    concealed = client.get(
        f"/api/v1/kanban/boards/goal:{goal_id}",
        headers=user_auth_header,
    )
    assert concealed.status_code == 404
    assert concealed.get_json()["error"]["code"] == "kanban_board_not_found"

    authorization = KanbanAuthorizationService()
    forged = KanbanPrincipal(
        subject="testuser",
        role="user",
        tenant_id="foreign-tenant",
        declared_capabilities=frozenset(
            {
                KanbanCapability.READ,
                KanbanCapability.WRITE,
                KanbanCapability.ASSIGN,
            }
        ),
    )
    capabilities = authorization.capabilities_for(forged)
    assert KanbanCapability.READ in capabilities
    assert KanbanCapability.WRITE not in capabilities
    assert KanbanCapability.ASSIGN not in capabilities


def test_provider_capability_injection_and_foreign_default_fail_closed(
    app,
    client,
    admin_auth_header,
    user_auth_header,
    monkeypatch,
) -> None:
    _enable_surfaces(app)

    class AllowlistedCatalog:
        def versioned_catalog(self, query):
            return ModelCatalog(
                models=(
                    ModelSummary(
                        provider_id="local",
                        runtime=ModelRuntime.LOCAL,
                        model_id="safe-model",
                        display_name="Safe model",
                        availability=ModelAvailability.AVAILABLE,
                        capabilities=("chat",),
                        health=ModelHealth.HEALTHY,
                    ),
                )
            )

    monkeypatch.setattr(
        "agent.routes.config.providers._model_catalog_service",
        lambda: AllowlistedCatalog(),
    )

    injected_refresh = client.post(
        "/models/catalog/v1/refresh",
        headers=user_auth_header,
        json={"capabilities": ["model_catalog.refresh"]},
    )
    injected_default = client.post(
        "/models/default/v1",
        headers=user_auth_header,
        json={
            "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            "provider_id": "local",
            "model_id": "safe-model",
            "capabilities": ["model_catalog.set_default"],
        },
    )
    assert injected_refresh.status_code == 403
    assert injected_default.status_code == 403
    assert injected_refresh.get_json()["data"]["reason_code"] == (
        "model_catalog_capability_required"
    )
    assert injected_default.get_json()["data"]["reason_code"] == (
        "model_catalog_capability_required"
    )

    foreign_default = client.post(
        "/models/default/v1",
        headers=admin_auth_header,
        json={
            "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            "provider_id": "foreign-provider",
            "model_id": "foreign-model",
        },
    )
    assert foreign_default.status_code == 404
    assert foreign_default.get_json()["message"] == (
        "model_default_selection_not_allowlisted"
    )

def test_kanban_api_fails_closed(client, admin_auth_header) -> None:
    response = client.get("/api/v1/kanban/boards", headers=admin_auth_header)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "kanban_feature_disabled"


def test_kanban_api_commands(client, admin_auth_header, app) -> None:
    app.config["KANBAN_FEATURE_FLAGS"] = {"kanban_api": True, "kanban_write": True}
    created = client.post(
        "/api/v1/kanban/boards/hub/cards",
        headers=admin_auth_header,
        json={"title": "API task", "idempotency_key": "api-create"},
    )
    assert created.status_code == 201
    card = created.get_json()["data"]
    listed = client.get("/api/v1/kanban/boards/hub/cards", headers=admin_auth_header)
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"][0]["id"] == card["id"]
    moved = client.post(
        f"/api/v1/kanban/cards/{card['id']}/commands/move",
        headers=admin_auth_header,
        json={
            "board_id": "hub",
            "expected_revision": card["revision"],
            "idempotency_key": "api-move",
            "column_id": "in_progress",
            "position": 0,
        },
    )
    assert moved.status_code == 200
    stale = client.post(
        f"/api/v1/kanban/cards/{card['id']}/commands/comment",
        headers=admin_auth_header,
        json={
            "board_id": "hub",
            "expected_revision": card["revision"],
            "idempotency_key": "api-stale",
            "body": "stale",
        },
    )
    assert stale.status_code == 409


def test_hub_board_is_concealed_from_non_admin(client, user_auth_header, app) -> None:
    app.config["KANBAN_FEATURE_FLAGS"] = {"kanban_api": True, "kanban_write": True}
    response = client.get("/api/v1/kanban/boards/hub", headers=user_auth_header)
    assert response.status_code == 404

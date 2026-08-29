from agent.bootstrap.peer_overlay import initialize_peer_overlay


def _wire(app, tmp_path):
    app.config.update(
        ROLE="hub",
        ANANTA_PEER_OVERLAY_STATE=str(tmp_path / "overlay-api.sqlite3"),
        ANANTA_PEER_OVERLAY_DATA_ENABLED=True,
    )
    initialize_peer_overlay(app)


def _candidate(peer_id: str) -> dict[str, object]:
    return {
        "peer_id": peer_id,
        "relay_consent": True,
        "visible": True,
        "battery": "mains",
        "network": "fast",
        "self_capacity": 80,
        "observed_capacity": 75,
        "delivery_ratio": 0.99,
    }


def test_peer_overlay_api_completes_a_headless_authorized_flow(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    for revision, peer_id in enumerate(("source", "peer-1")):
        response = client.post(
            "/api/peer-overlay/memberships",
            headers=admin_auth_header,
            json={
                "tenant_id": "tenant-1",
                "room_id": "room-1",
                "action": "join",
                "subject_peer_id": peer_id,
                "expected_revision": revision,
            },
        )
        assert response.status_code == 200
    plan_response = client.post(
        "/api/peer-overlay/plans",
        headers=admin_auth_header,
        json={
            "tenant_id": "tenant-1",
            "room_id": "room-1",
            "publication_id": "publication-1",
            "source_peer_id": "source",
            "candidates": [_candidate("source"), _candidate("peer-1")],
        },
    )
    assert plan_response.status_code == 200
    plan = plan_response.get_json()["data"]
    assert plan["media_forwarding_allowed"] is False
    lease = plan["leases"][0]
    ticket_response = client.post(
        "/api/peer-overlay/tickets",
        headers=admin_auth_header,
        json={
            "tenant_id": "tenant-1",
            "room_id": "room-1",
            "publication_id": "publication-1",
            "lease_id": lease["lease_id"],
        },
    )
    ticket = ticket_response.get_json()["data"]
    consumed = client.post(
        "/api/peer-overlay/tickets/consume",
        headers=admin_auth_header,
        json={"ticket": ticket, "local_peer_id": "peer-1"},
    )
    assert consumed.status_code == 200
    replay = client.post(
        "/api/peer-overlay/tickets/consume",
        headers=admin_auth_header,
        json={"ticket": ticket, "local_peer_id": "peer-1"},
    )
    assert replay.status_code == 409
    overview = client.get(
        "/api/peer-overlay/overview?tenant_id=tenant-1&room_id=room-1",
        headers=admin_auth_header,
    ).get_json()["data"]
    assert overview["fallback"] == "livekit_e2ee"
    assert overview["human_intervention_required"] is False


def test_peer_overlay_mutations_require_admin(app, client, user_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    response = client.post(
        "/api/peer-overlay/memberships",
        headers=user_auth_header,
        json={
            "tenant_id": "tenant-1",
            "room_id": "room-1",
            "action": "join",
            "subject_peer_id": "peer-1",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 403

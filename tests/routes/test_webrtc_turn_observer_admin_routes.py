from flask import Flask

from agent.routes.webrtc_turn_observer_enrollment import build_turn_observer_admin_blueprint


def identity(function):
    return function


def test_admin_route_rejects_private_key_material_without_calling_handler():
    called = False

    def handler(operation, body, actor, key):
        nonlocal called
        called = True

    app = Flask(__name__)
    app.register_blueprint(
        build_turn_observer_admin_blueprint(
            admin_guard=identity,
            actor_resolver=lambda: "admin-subject",
            command_handler=handler,
            audit_logger=lambda event, fields: None,
        )
    )
    response = app.test_client().post(
        "/api/webrtc/turn-observers",
        headers={"Idempotency-Key": "request-1"},
        json={
            "identity_id": "observer-a",
            "pool_id": "pool-a",
            "instance_id": "turn-a",
            "public_key": "public",
            "private_key": "must-not-cross-boundary",
            "proof_nonce": "nonce",
            "proof_signature": "proof",
            "certificate_fingerprint_sha256": "sha256:" + "a" * 64,
            "expected_version": 0,
        },
    )

    assert response.status_code == 400
    assert called is False


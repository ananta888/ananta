from flask import Flask

from agent.routes.webrtc_turn_observations import build_turn_observation_blueprint


def app_with(resolver, handler):
    app = Flask(__name__)
    app.register_blueprint(
        build_turn_observation_blueprint(
            transport_identity_resolver=resolver,
            observation_handler=handler,
            audit_logger=lambda event, fields: None,
        )
    )
    return app


def test_public_certificate_headers_are_not_an_identity_source():
    called = False

    def handler(body, identity):
        nonlocal called
        called = True

    client = app_with(lambda environ: None, handler).test_client()
    response = client.post(
        "/api/webrtc/turn-observations",
        base_url="https://hub.example",
        headers={"X-SSL-Client-Cert": "caller-controlled"},
        json={"anything": "ignored-before-authentication"},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "turn_observation_mtls_identity_missing"
    assert called is False


def test_plain_http_is_rejected_before_transport_resolution():
    client = app_with(lambda environ: object(), lambda body, identity: {}).test_client()

    response = client.post("/api/webrtc/turn-observations", data=b"{}")

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "turn_observation_tls_required"


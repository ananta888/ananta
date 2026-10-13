"""Real Hub route/service/signing with explicit synthetic policy, not a browser loop."""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from flask import Flask

from agent.routes.meet import meet_bp
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from agent.services.meet_turn_service import MeetTurnService
from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
from worker.meet_media.contract import encode, signature

pytestmark = pytest.mark.timeout(15)
KEY = b"synthetic-companion-grant-worker-key"
PATH = "/api/meet/v1/internal/companion"
ISSUER = "https://synthetic-hub.example.test"


@pytest.fixture
def hub(tmp_path):
    private = Ed25519PrivateKey.generate()
    path = tmp_path / "hub-key.pem"
    path.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    path.chmod(0o600)
    issuer = MeetMachineGrantIssuer(ISSUER, path)
    principal = MeetTurnService._companion("synthetic", "synthetic")
    binding = SyntheticMeetBinding("https://meet.synthetic.test", "room-aaaaaaaaaaaaaaaaaa", principal)
    # This grant endpoint never dispatches work: keep execution ports absent so
    # an accidental worker/task invocation is a real failure, not a permissive mock.
    service = MeetTurnService(binding, None, None, {("synthetic", "synthetic")}, grant_issuer=issuer)
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions.update(meet_binding_service=binding, meet_turn_service=service, meet_media_worker_key=KEY)
    return app, service, private.public_key()


def post(app, identity=None, *, key=KEY):
    raw = encode({"project_id": "synthetic", "capabilities": ["chat.read", "chat.send"], **(identity or {})})
    return app.test_client().post(PATH, data=raw, headers={"Content-Type": "application/json",
                                                        "X-Ananta-Task-Signature": signature(key, raw)})


def claims(response, public_key):
    assert response.status_code == 200
    body = response.get_json()
    assert body["schema"] == "ananta.meet-companion-grant.v1"
    value = jwt.decode(body["grant"], public_key, algorithms=["EdDSA"], issuer=ISSUER,
                       audience="ananta-meet-machine-v2")
    assert value["roomId"] == body["room_id"] == "room-aaaaaaaaaaaaaaaaaa"
    assert value["tenantId"] == value["projectId"] == "synthetic"
    assert value["capabilities"] == ["chat.read", "chat.send"]
    assert 0 < value["exp"] - value["iat"] <= 300
    assert jwt.get_unverified_header(body["grant"])["typ"] == "ananta-meet-machine-v2+jwt"
    assert response.headers["Cache-Control"] == "no-store"
    return value


def test_actual_hub_issues_and_renews_the_same_closed_companion_binding(hub):
    app, _service, public = hub
    first = claims(post(app), public)
    identity = {"task_id": first["taskId"], "runtime_id": first["runtimeId"], "session_id": first["sessionId"]}
    renewed = claims(post(app, identity), public)
    for field in ("taskId", "runtimeId", "sessionId", "roomId", "tenantId", "projectId"):
        assert renewed[field] == first[field]
    assert renewed["jti"] != first["jti"]


def test_scope_withdrawal_denies_renewal_under_previous_identity(hub):
    app, service, public = hub
    first = claims(post(app), public)
    service.allowed_scopes = frozenset()
    response = post(app, {
        "task_id": first["taskId"], "runtime_id": first["runtimeId"], "session_id": first["sessionId"],
    })
    assert response.status_code == 403
    assert "grant" not in response.get_json()


def test_worker_role_cannot_use_the_hub_grant_endpoint(hub):
    app, _service, _public = hub
    app.config["ROLE"] = "worker"
    assert post(app).status_code == 403


def test_forged_worker_signature_cannot_request_a_grant(hub):
    app, _service, _public = hub
    assert post(app, key=b"untrusted-worker").status_code == 401


def test_missing_signing_authority_has_no_unsigned_or_legacy_fallback(hub):
    app, service, _public = hub
    service.grant_issuer = None
    response = post(app)
    assert response.status_code == 403
    assert response.get_json() == {"error": {"code": "meet_machine_publication_disabled"}}

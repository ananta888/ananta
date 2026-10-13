"""Real Task authority/signing over synthetic task storage; no runtime dispatch."""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agent.services.meet_companion_task_grants import MeetCompanionTaskGrants
from agent.services.meet_contract import MeetError
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from tests.test_meet_dialog_authority import fixture


@pytest.fixture
def assigned(tmp_path):
    f = fixture()
    f.context["capabilities"] = ["chat.read", "chat.send"]
    f.context["companion_execution"] = True
    f.authority.policies[("tenant", "project")] = frozenset(f.context["capabilities"])
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "key.pem"
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    path.chmod(0o600)
    issuer = MeetMachineGrantIssuer("https://hub.example.test", path)
    service = MeetCompanionTaskGrants(f.authority, issuer, clock=lambda: f.now)
    request = {"project_id": "project", "task_id": "task", "lease_id": "dispatch",
               "runtime_id": "runtime", "session_id": "hub-session", "capabilities": ["chat.read", "chat.send"]}
    return f, service, request, key


def test_registered_task_binding_is_signed_without_caller_identity_issuance(assigned):
    f, service, request, key = assigned
    result = service.issue(request)
    claims = jwt.decode(result["grant"], key.public_key(), algorithms=["EdDSA"],
                        issuer="https://hub.example.test", audience="ananta-meet-machine-v2")
    assert (claims["taskId"], claims["runtimeId"], claims["sessionId"]) == ("task", "runtime", "hub-session")
    assert claims["capabilities"] == request["capabilities"]
    assert claims["exp"] <= min(f.now + 120, f.context["deadline"])
    assert result["controls"] == f.context["controls"]
    f.task.status = "cancelled"
    with pytest.raises(MeetError):
        service.issue(request)


@pytest.mark.parametrize("patch", [
    {"project_id": "foreign"}, {"session_id": "invented"}, {"runtime_id": "invented"},
    {"lease_id": "stale"}, {"capabilities": ["chat.read"]},
    {"capabilities": ["chat.read", "chat.send", "avatar.publish"]},
    {"capabilities": ["chat.read", "chat.read"]}, {"capabilities": "chat.read"},
    {"capabilities": [True]}, {"source_id": "SRC_invented"}, {"task_id": ""},
])
def test_closed_scope_cannot_be_forged_broadened_or_replaced(assigned, patch):
    _f, service, request, _key = assigned
    with pytest.raises(MeetError):
        service.issue(request | patch)


def test_signer_reread_rejects_session_rebinding_before_return(assigned, monkeypatch):
    f, service, request, _key = assigned
    issue = service.issuer.issue_dialog

    def changed(*args):
        f.context["session_id"] = "replacement"
        return issue(*args)

    monkeypatch.setattr(service.issuer, "issue_dialog", changed)
    with pytest.raises(MeetError, match="assignment_mismatch"):
        service.issue(request)


def test_post_signing_control_change_does_not_return_stale_authority(assigned, monkeypatch):
    f, service, request, _key = assigned
    issue = service.issuer.issue_dialog

    def changed(*args):
        grant = issue(*args)
        f.context["controls"]["chat"]["enabled"] = False
        return grant

    monkeypatch.setattr(service.issuer, "issue_dialog", changed)
    with pytest.raises(MeetError, match="assignment_changed"):
        service.issue(request)


def test_normal_dialog_task_cannot_be_used_as_companion_execution_authority(assigned):
    f, service, request, _key = assigned
    del f.context["companion_execution"]
    with pytest.raises(MeetError, match="execution_not_bound"):
        service.issue(request)

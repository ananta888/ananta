"""Synthetic task/policy/signature observations, not production release evidence."""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from agent.services.meet_contract import MeetError, MeetProfile
from agent.services.meet_dialog_authority import MeetDialogAuthority
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from agent.services.meet_authorization_client import validate_authorization
from agent.services.meet_dialog_controls import initial_controls


def fixture():
    now = int(time.time())
    context = {"lease_id": "dispatch", "runtime_id": "runtime", "session_id": "hub-session",
        "room_id": "room-" + "a" * 18, "owner_subject": "owner", "binding_task_id": "",
        "deadline": now + 600, "capabilities": ["audio.receive", "chat.read", "chat.send"], "chat_mode": "mention",
        "audio_mode": "off", "audio_job": None, "audio_count": 0}
    context["controls"] = initial_controls(context["capabilities"], "mention", "off", now * 1000)
    task = SimpleNamespace(task_kind="meet_dialog_session", status="in_progress", archived=False,
        tenant_id="tenant", project_id="project", worker_execution_context={"meet_dialog": context})
    tasks = Mock(); tasks.get_by_id.return_value = task
    profile = MeetProfile("https://meet.example.test")
    binding = Mock(profile=profile); binding.read.return_value = {"invite_url": profile.invite(context["room_id"])}
    authority = MeetDialogAuthority(tasks, binding, {("tenant", "project"): context["capabilities"]}, clock=lambda: now)
    return SimpleNamespace(now=now, task=task, context=context, tasks=tasks, binding=binding, authority=authority)


def test_current_requires_live_hub_task_exact_runtime_project_policy_and_room():
    f = fixture()
    scope = f.authority.current("task", "dispatch", "runtime")
    assert scope.task_id == "task" and scope.lease_id == "dispatch"
    for change in ({"status": "completed"}, {"archived": True}, {"project_id": "other"}):
        original = {key: getattr(f.task, key) for key in change}
        for key, value in change.items(): setattr(f.task, key, value)
        with pytest.raises(MeetError): f.authority.current("task", "dispatch", "runtime")
        for key, value in original.items(): setattr(f.task, key, value)
    with pytest.raises(MeetError): f.authority.current("task", "other", "runtime")
    with pytest.raises(MeetError): f.authority.current("task", "dispatch", "other")
    f.binding.read.return_value = {"invite_url": f.binding.profile.invite("room-" + "b" * 18)}
    with pytest.raises(MeetError): f.authority.current("task", "dispatch", "runtime")


def test_signer_rechecks_authority_and_keeps_dispatch_lease_out_of_meet_identity(tmp_path):
    f = fixture(); key = Ed25519PrivateKey.generate(); path = tmp_path / "key.pem"
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())); path.chmod(0o600)
    signer = MeetMachineGrantIssuer("https://hub.example.test", path)
    first = signer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)
    second = signer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)
    claims = jwt.decode(first["grant"], key.public_key(), algorithms=["EdDSA"], audience="ananta-meet-machine-v2")
    assert claims["runtimeId"] == "runtime" and claims["sessionId"] == "hub-session"
    assert claims["exp"] - claims["iat"] == 120 and claims["jti"] != "dispatch"
    assert first["grant"] != second["grant"]
    f.task.status = "cancelled"
    with pytest.raises(MeetError): signer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)


def test_meet_backchannel_rejects_worker_claims_wrong_nonce_scope_and_stale_grants():
    f = fixture(); scope = f.authority.current("task", "dispatch", "runtime")
    session = "ms_" + "a" * 32; nonce = "b" * 32; issuer = "https://hub.example.test"
    value = {"schema": "ananta.meet-authorization.v1", "nonce": nonce,
        "lease": {"schema": "ananta.meet-session-lease.v1", "sessionId": session, "generation": 1,
                  "expiresAt": (f.now + 120) * 1000, "absoluteExpiresAt": (f.now + 7200) * 1000},
        "binding": {"issuer": issuer, "subject": "machine:ananta", "roomId": scope.room_id, "taskId": "task",
                    "tenantId": "tenant", "projectId": "project", "protocolVersion": "v2", "runtimeId": "runtime",
                    "hubSessionId": "hub-session", "capabilitySet": ",".join(scope.capabilities)},
        "peerId": "c" * 16, "roomId": scope.room_id, "membershipEpoch": 1, "receiveRevision": 0, "grants": [], "publications": []}
    assert validate_authorization(value, scope, issuer, session, nonce, f.now * 1000) is value
    for patch in ({"nonce": "wrong"}, {"worker_allowed": True}, {"receiveRevision": True},
                  {"binding": value["binding"] | {"tenantId": "other"}}, {"grants": [{"chatRead": True}]}):
        with pytest.raises(MeetError): validate_authorization(value | patch, scope, issuer, session, nonce, f.now * 1000)

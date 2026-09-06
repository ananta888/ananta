"""Headless policy and execution tests; GPU integration is a separate opt-in gate."""

import base64
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_media_transport import HttpMediaWorker, validate_result
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from worker.meet_media.contract import authenticate, encode, load_key, signature, validate_turn
from worker.meet_media.server import TurnExecutor


def turn():
    return {
        "schema": "ananta.meet-turn.v1",
        "task_id": "task",
        "lease_id": "lease",
        "tenant_id": "tenant",
        "project_id": "project",
        "deadline": int(time.time()) + 110,
        "text": "Hallo",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"schema": "v2"},
        {"text": ""},
        {"text": "x" * 2001},
        {"text": []},
        {"deadline": True},
        {"deadline": 0},
        {"deadline": 2**53},
        {"tenant_id": "../other"},
    ],
)
def test_closed_turn_contract(change):
    with pytest.raises(ValueError):
        validate_turn(turn() | change, time.time())


def test_hub_signature_binds_all_bytes():
    body, key = encode(turn()), b"k" * 32
    authenticate(key, body, signature(key, body))
    for other_body, other_key in [(body + b" ", key), (body, b"x" * 32)]:
        with pytest.raises(ValueError, match="unauthorized"):
            authenticate(other_key, other_body, signature(key, body))


def test_key_must_be_private(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(b"x" * 32)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        load_key(path)
    path.chmod(0o600)
    assert load_key(path) == b"x" * 32


def test_replay_survives_worker_restart_and_failed_execution(tmp_path):
    path = tmp_path / "leases.db"
    executor = TurnExecutor(path)
    executor._run = Mock(side_effect=ValueError("meet_execution_failed"))
    envelope = turn()
    with pytest.raises(ValueError, match="execution_failed"):
        executor.execute(envelope)
    with pytest.raises(ValueError, match="replayed"):
        TurnExecutor(path).execute(envelope)


def test_single_gpu_flight_and_expiry(tmp_path):
    executor = TurnExecutor(tmp_path / "leases.db")
    executor.lock.acquire()
    with pytest.raises(ValueError, match="busy"):
        executor.execute(turn())
    executor.lock.release()
    with pytest.raises(ValueError, match="expired"):
        executor.execute(turn() | {"deadline": 0})


def service():
    binding, worker, tasks = Mock(), Mock(), Mock()
    worker.execute.side_effect = lambda value: {"task_id": value["task_id"], "lease_id": value["lease_id"]}
    tasks.finish.return_value = True
    return MeetTurnService(binding, worker, tasks, [("tenant", "project")]), binding, worker, tasks


PRINCIPAL = SimpleNamespace(tenant_id="tenant", subject_id="actor")


def test_hub_policy_is_explicit_and_not_user_controlled():
    runtime, binding, worker, tasks = service()
    with pytest.raises(MeetError, match="policy_denied"):
        runtime.execute(PRINCIPAL, "other", {"text": "hello"})
    with pytest.raises(MeetError, match="payload_invalid"):
        runtime.execute(PRINCIPAL, "project", {"text": "hello", "auto_approve": True})
    tasks.start.assert_not_called()
    worker.execute.assert_not_called()


def test_hub_queue_and_result_scope():
    runtime, binding, worker, tasks = service()
    result = runtime.execute(PRINCIPAL, "project", {"text": "Hallo"})
    envelope = worker.execute.call_args.args[0]
    assert result["task_id"] == envelope["task_id"]
    tasks.start.assert_called_once_with(envelope, "actor")
    tasks.finish.assert_called_once_with(envelope, "completed")
    assert binding.require_write_access.call_count == 2


@pytest.mark.parametrize("failure", ["stale", "cancel", "error", "revoke"])
def test_no_publication_after_failed_or_revoked_work(failure):
    runtime, binding, worker, tasks = service()
    if failure == "stale":
        worker.execute.side_effect = lambda value: {"task_id": value["task_id"], "lease_id": "old"}
    elif failure == "cancel":
        tasks.finish.return_value = False
    elif failure == "error":
        worker.execute.side_effect = TimeoutError()
    else:
        binding.require_write_access.side_effect = [None, MeetError("revoked", 403)]
    with pytest.raises((MeetError, TimeoutError)):
        runtime.execute(PRINCIPAL, "project", {"text": "Hallo"})
    assert tasks.finish.call_args.args[1] == "failed"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://host/v1/turns",
        "http://host/v1/turns",
        "http://user:pass@host:123/v1/turns",
        "http://host:123/v1/turns?token=a",
        "http://host:123/other",
    ],
)
def test_worker_endpoint_is_operator_configured_and_exact(endpoint):
    with pytest.raises(ValueError):
        HttpMediaWorker(endpoint, b"k" * 32)


def result():
    return {
        "schema": "ananta.meet-turn-result.v1",
        "task_id": "task",
        "lease_id": "lease",
        "text": "Hallo",
        "duration_seconds": 1,
        "engines": {"llm": "ollama", "speech": "piper-cuda", "video": "procedural-avatar-h264_nvenc"},
        "audio": {"mime": "audio/wav", "base64": base64.b64encode(b"RIFF0000WAVE0000").decode()},
        "video": {"mime": "video/mp4", "base64": base64.b64encode(b"0000ftyp00000000").decode()},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "v2"},
        {"text": ""},
        {"duration_seconds": float("nan")},
        {"duration_seconds": 41},
        {"engines": {"speech": "cpu"}},
        {"extra": True},
        {"video": {"mime": "text/html", "base64": "a"}},
    ],
)
def test_untrusted_worker_result_is_bounded(change):
    assert validate_result(result())
    with pytest.raises(MeetError):
        validate_result(result() | change)


def test_real_hub_queue_preserves_lease_and_does_not_store_content(app):
    import uuid

    from agent.repository import task_repo

    envelope = turn() | {"task_id": str(uuid.uuid4()), "text": "PRIVATE-MEETING-CONTENT"}
    with app.app_context():
        tasks = HubMediaTasks()
        tasks.start(envelope, "actor")
        assert tasks.finish(envelope | {"lease_id": "stale"}, "completed") is False
        assert tasks.finish(envelope, "completed") is True
        stored = task_repo.get_by_id(envelope["task_id"])
        assert stored.status == "completed"
        assert "PRIVATE-MEETING-CONTENT" not in json.dumps(stored.model_dump(), default=str)


@pytest.mark.parametrize(
    "meeting",
    [
        None,
        {},
        {"origin": "http://evil", "room_id": "room", "grant": "x"},
        {"origin": "https://meet.test/path", "room_id": "room-0123456789abcdef01", "grant": "a.b.c"},
        {"origin": "https://meet.test", "room_id": "room-0123456789abcdef01", "grant": "a.b.c", "human": True},
    ],
)
def test_publication_contract_cannot_capture_or_expand_origin(meeting):
    with pytest.raises(ValueError):
        validate_turn(turn() | {"meeting": meeting}, time.time())


def test_machine_publication_has_a_separate_opt_in():
    runtime, _, worker, tasks = service()
    with pytest.raises(MeetError, match="publication_disabled"):
        runtime.execute(PRINCIPAL, "project", {"text": "hello", "publish_to_meet": True})
    with pytest.raises(MeetError, match="payload_invalid"):
        runtime.execute(PRINCIPAL, "project", {"text": "hello", "publish_to_meet": "true"})
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()


def test_machine_grant_is_exact_and_worker_never_receives_private_key(tmp_path):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agent.services.meet_contract import MeetProfile
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer

    key = Ed25519PrivateKey.generate()
    path = tmp_path / "key.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    path.chmod(0o600)
    issuer = MeetMachineGrantIssuer("https://hub.test", path)
    binding = Mock(profile=MeetProfile("https://meet.test"))
    binding.read.return_value = {"invite_url": "https://meet.test/?room=room-0123456789abcdef01&mode=room"}
    request = turn()
    meeting = issuer.issue(request, binding, PRINCIPAL, time.time())
    validate_turn(request | {"meeting": meeting}, time.time())
    claims = jwt.decode(
        meeting["grant"],
        key.public_key(),
        algorithms=["EdDSA"],
        issuer="https://hub.test",
        audience="ananta-meet-machine-v1",
    )
    assert claims["taskId"] == request["task_id"] and claims["jti"] == request["lease_id"]
    assert claims["roomId"] == meeting["room_id"] and claims["exp"] == request["deadline"]
    assert set(meeting) == {"origin", "room_id", "grant"}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        MeetMachineGrantIssuer("https://hub.test", path)


def test_lease_fails_closed_on_task_cancellation_policy_and_access(monkeypatch):
    from agent.services import repository_registry

    runtime, binding, _, _ = service()
    request = turn()
    task = SimpleNamespace(
        task_kind="meet_media_turn",
        status="in_progress",
        tenant_id="tenant",
        project_id="project",
        worker_execution_context={
            "meet_media": {"lease_id": "lease", "deadline": request["deadline"], "owner_subject": "actor"}
        },
    )
    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(task_repo=SimpleNamespace(get_by_id=lambda _: task)),
    )
    assert runtime.lease_allowed("task", "lease") is True
    assert runtime.lease_allowed("task", "old") is False
    task.status = "cancelled"
    assert runtime.lease_allowed("task", "lease") is False
    task.status = "in_progress"
    binding.require_write_access.side_effect = PermissionError()
    assert runtime.lease_allowed("task", "lease") is False


def test_publication_lease_uses_the_actual_active_task_model(app):
    runtime, _, _, _ = service()
    request = turn() | {"task_id": str(uuid.uuid4()), "lease_id": str(uuid.uuid4())}
    tasks = HubMediaTasks()
    with app.app_context():
        tasks.start(request, "actor")
        assert runtime.lease_allowed(request["task_id"], request["lease_id"]) is True
        assert tasks.finish(request, "completed") is True
        assert runtime.lease_allowed(request["task_id"], request["lease_id"]) is False


@pytest.mark.parametrize("signed", [True, False])
def test_worker_result_requires_integrity(monkeypatch, signed):
    from unittest.mock import MagicMock

    import agent.services.meet_media_transport as transport

    raw, key = encode(result()), b"k" * 32
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = raw
    response.headers = {"X-Ananta-Result-Signature": signature(key, b"result-v1\0" + raw) if signed else "bad"}
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(transport, "pin_private_container_address", lambda *_: "172.18.0.2")
    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *_: opener)
    worker = HttpMediaWorker("http://worker:8094/v1/turns", key)
    if signed:
        assert worker.execute(turn())["text"] == "Hallo"
    else:
        with pytest.raises(MeetError, match="unauthorized"):
            worker.execute(turn())


@pytest.mark.parametrize(
    "body", [b'{"allowed":true}', b'{"allowed":false}', b'{"allowed":1}', b'{"allowed":true,"scope":"all"}', b"bad"]
)
def test_worker_cannot_refresh_or_broaden_lease(monkeypatch, tmp_path, body):
    from unittest.mock import MagicMock

    import worker.meet_media.lease_guard as guard_module
    from worker.meet_media.lease_guard import HubLeaseGuard

    path = tmp_path / "key"
    path.write_bytes(b"k" * 32)
    path.chmod(0o600)
    monkeypatch.setenv("MEET_HUB_LEASE_URL", "http://hub:5000/api/meet/v1/internal/lease")
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(path))
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = body
    response.headers = {"X-Ananta-Lease-Signature": signature(b"k" * 32, b"lease-v1\0" + body)}
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(guard_module.urllib.request, "build_opener", lambda *_: opener)
    if body == b'{"allowed":true}':
        HubLeaseGuard("task", "lease").require()
    else:
        with pytest.raises(ValueError, match="revoked_or_unavailable"):
            HubLeaseGuard("task", "lease").require()


def test_task_bound_request_never_uses_project_room():
    runtime, binding, _, _ = service()
    runtime.grant_issuer = Mock()
    runtime.grant_issuer.issue.return_value = {
        "origin": "https://meet.test",
        "room_id": "room-0123456789abcdef01",
        "grant": "a.b.c",
    }
    runtime.execute(PRINCIPAL, "project", {"text": "hello", "publish_to_meet": True}, task="scoped-task")
    assert runtime.grant_issuer.issue.call_args.kwargs == {"task": "scoped-task"}
    assert runtime.grant_issuer.issue.call_args.args[0]["binding_task_id"] == "scoped-task"
    binding.require_write_access.assert_called_with(PRINCIPAL, "project", "scoped-task")

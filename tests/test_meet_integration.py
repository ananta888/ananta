"""Deterministic user/project isolation and Meet adapter contract tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import create_engine

from agent.repositories.meet_bindings import SqlMeetingStore
from agent.routes.meet import meet_bp
from agent.services.meet_binding_service import MeetBindingService
from agent.services.meet_contract import MeetError, MeetProfile
from agent.services.meet_health_probe import MeetHealthProbe
from agent.services.project_access_authority import ProjectAccessError, ProjectCapability
from agent.services.source_control_access_policy import HubSourcePrincipal

ORIGIN = "https://webrtc.ananta.de"
INVITE = ORIGIN + "/?room=room-0123456789abcdef01&mode=room"


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'meet.db'}")
    store = SqlMeetingStore(engine, ORIGIN)
    store.initialize()
    access = Mock()
    task_access = Mock()
    service = MeetBindingService(MeetProfile(ORIGIN), store, access, task_access)
    principal = HubSourcePrincipal("alice", "tenant-a", "project-a", frozenset({"user"}))
    return service, principal, store, access, task_access


@pytest.mark.parametrize(
    "origin",
    [
        "http://webrtc.ananta.de",
        "https://user:pass@webrtc.ananta.de",
        "https://webrtc.ananta.de/",
        "https://webrtc.ananta.de/path",
        "https://webrtc.ananta.de?secret=a",
        "https://webrtc.ananta.de#fragment",
        "https://webrtc.ananta.de:8443",
        "https://webrtc.ananta.de:443",
        "https://webrtc.ananta.de\\evil",
        "https://webrtc.ananta.de\n",
        "https://",
        "https://[",
    ],
)
def test_origin_rejects_ambiguous_or_credentialed_urls(origin):
    with pytest.raises(MeetError, match="meet_origin_invalid"):
        MeetProfile(origin)


@pytest.mark.parametrize(
    "invite",
    [
        INVITE.replace("webrtc.ananta.de", "evil.test"),
        INVITE + "&token=secret",
        INVITE + "&room=room-0123456789abcdef01",
        INVITE + "#secret",
        INVITE.replace("mode=room", "mode=pair"),
        INVITE.replace("/?", "/api/?"),
        INVITE.replace("room-", "pair-"),
        INVITE.replace("https://", "https://user@"),
        INVITE + "\n",
        None,
        {},
        "a" * 513,
    ],
)
def test_invite_is_exact_room_capability(invite):
    with pytest.raises(MeetError, match="meet_invite_invalid"):
        MeetProfile(ORIGIN).parse_invite(invite)


def test_associate_persist_idempotent_unlink_and_origin_isolation(runtime):
    service, principal, store, access, _ = runtime
    payload = {"expected_revision": 0, "invite_url": INVITE}
    bound = service.change(principal, "project-a", "", payload)
    assert bound["revision"] == 1
    assert bound["invite_url"] == INVITE
    assert bound["membership_granted"] is False
    assert bound["room_verified"] is False
    assert service.change(principal, "project-a", "", dict(payload, expected_revision=1)) == bound
    reopened = SqlMeetingStore(store.engine, ORIGIN)
    assert reopened.get("tenant-a", "project-a", "").room_id
    assert SqlMeetingStore(store.engine, "https://other.test").get("tenant-a", "project-a", "").revision == 0
    assert reopened.get("tenant-b", "project-a", "").room_id is None
    assert reopened.get("tenant-a", "project-a", "task-a").room_id is None
    removed = service.change(principal, "project-a", "", {"expected_revision": 1}, unlink=True)
    assert removed["invite_url"] is None and removed["revision"] == 2
    with pytest.raises(MeetError, match="meet_binding_conflict"):
        service.change(principal, "project-a", "", payload)
    assert access.require.call_args.kwargs["capability"] == ProjectCapability.WRITE


def test_store_rejects_concurrent_insert_and_update(runtime):
    _, _, store, _, _ = runtime
    store.replace("tenant", "project", "", 0, "room-0123456789abcdef01", "alice")
    for expected in (0, 2):
        with pytest.raises(MeetError, match="meet_binding_conflict"):
            store.replace("tenant", "project", "", expected, None, "alice")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_revision": True, "invite_url": INVITE},
        {"expected_revision": -1, "invite_url": INVITE},
        {"expected_revision": 0, "invite_url": INVITE, "tenant_id": "evil"},
        {"expected_revision": 0, "invite_url": INVITE, "auto_approve": True},
    ],
)
def test_closed_input_contract(runtime, payload):
    service, principal, *_ = runtime
    with pytest.raises(MeetError):
        service.change(principal, "project-a", "", payload)


def test_authorization_is_rechecked_on_every_read_and_write(runtime):
    service, principal, _, access, task_access = runtime
    service.read(principal, "project-a", "task-a")
    task_access.assert_called_once_with(principal, "project-a", "task-a")
    access.require.side_effect = ProjectAccessError(
        reason_code="project_not_found", public_status=404, tenant_id="tenant-a", project_id="project-a"
    )
    for action in [
        lambda: service.read(principal, "project-a"),
        lambda: service.change(principal, "project-a", "", {"expected_revision": 0}, unlink=True),
    ]:
        with pytest.raises(ProjectAccessError):
            action()


@pytest.mark.parametrize(
    "principal,project",
    [
        (HubSourcePrincipal("worker", "tenant-a", "project-a", frozenset({"worker"})), "project-a"),
        (HubSourcePrincipal("service", "tenant-a", "project-a", frozenset({"service", "admin"})), "project-a"),
        (HubSourcePrincipal("alice", None, "project-a", frozenset()), "project-a"),
        (HubSourcePrincipal("alice", "tenant-a", "project-a", frozenset()), "project-b"),
    ],
)
def test_no_scope_or_worker_escalation(runtime, principal, project):
    service, _, _, access, _ = runtime
    with pytest.raises(MeetError):
        service.read(principal, project)
    access.require.assert_not_called()


@pytest.fixture
def meet_client(runtime, monkeypatch):
    import agent.auth as auth

    service = runtime[0]
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_binding_service"] = service
    app.extensions["meet_health_probe"] = Mock(inspect=lambda: {"status": "available"})
    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: {
            "sub": "alice",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "role": "user",
        }
        if token == "test-user"
        else None,
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda payload: True)
    return app.test_client(), app


def test_http_user_only_no_store_closed_fields_and_disabled(meet_client):
    client, app = meet_client
    path = "/api/meet/v1/projects/project-a/binding"
    headers = {"Authorization": "Bearer test-user"}
    for token in (None, "worker-token", "meet-oidc-token"):
        response = client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {})
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "no-store"
    assert client.get(path, headers=headers).status_code == 200
    for malformed in ("Bearer ", "Bearer", "Bearer a b", "Basic abc", "Bearer " + "a" * 8193):
        assert client.get(path, headers={"Authorization": malformed}).status_code == 401
    assert client.get(path + "?tenant_id=evil", headers=headers).status_code == 400
    response = client.put(path, headers=headers, json={"invite_url": INVITE, "expected_revision": 0})
    assert response.status_code == 200
    assert response.json["invite_url"] == INVITE
    assert client.put(path, headers=headers, json={"large": "x" * 2100}).status_code == 413
    assert client.delete(path, headers=headers, json={"expected_revision": 1}).status_code == 200
    app.config["ROLE"] = "worker"
    assert client.get(path, headers=headers).status_code == 403
    app.config["ROLE"] = "hub"
    del app.extensions["meet_binding_service"]
    assert client.get(path, headers=headers).json["error"]["code"] == "meet_disabled"


def test_bootstrap_default_off_has_no_store(monkeypatch):
    from agent.bootstrap.meet import configure_meet

    app = Flask(__name__)
    app.config.update(ROLE="hub", ANANTA_MEET_ENABLED=False)
    configure_meet(app)
    assert "meet" in app.blueprints
    assert "meet_binding_service" not in app.extensions


def test_turn_endpoint_uses_user_authority_and_separate_worker_lease_auth(meet_client):
    from worker.meet_media.contract import encode, signature

    client, app = meet_client
    path = "/api/meet/v1/projects/project-a/turns"
    headers = {"Authorization": "Bearer test-user"}
    assert client.post(path, json={"text": "hello"}).status_code == 401
    assert client.post(path, headers=headers, json={"text": "hello"}).status_code == 404
    runtime = Mock()
    runtime.execute.return_value = {"text": "synthetic answer"}
    runtime.lease_allowed.return_value = True
    app.extensions["meet_turn_service"] = runtime
    app.extensions["meet_media_worker_key"] = b"k" * 32
    assert client.post(path, headers=headers, json={"text": "hello"}).status_code == 200
    assert client.post(path + "?approve=true", headers=headers, json={"text": "hello"}).status_code == 400
    lease_path = "/api/meet/v1/internal/lease"
    body = encode({"task_id": "task", "lease_id": "lease"})
    assert client.post(lease_path, headers=headers, data=body).status_code == 401
    lease = client.post(lease_path, data=body, headers={"X-Ananta-Task-Signature": signature(b"k" * 32, body)})
    assert lease.status_code == 200 and lease.json == {"allowed": True}
    assert lease.headers["Cache-Control"] == "no-store"
    runtime.lease_allowed.assert_called_once_with("task", "lease")


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (302, b"{}", "unavailable"),
        (200, b"[]", "unavailable"),
        (200, b"x" * 32769, "unavailable"),
        (200, b"bad-json", "unavailable"),
        (200, b'{"status":"ok","auth":{"mode":"required"},"mediaE2ee":{"mode":"required"}}', "available"),
        (200, b'{"status":"ok","auth":{"mode":"disabled"}}', "incompatible"),
    ],
)
def test_probe_does_not_follow_redirects_or_claim_media(status, body, expected):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = status
    response.iter_content.side_effect = lambda **kwargs: iter(bytes([byte]) for byte in body)
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = response
    result = MeetHealthProbe(MeetProfile(ORIGIN), lambda: session).inspect()
    assert result == {"status": expected, "room_verified": False, "media_verified": False}
    assert session.trust_env is False
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert "Authorization" not in session.get.call_args.kwargs["headers"]


def test_task_adapter_checks_real_scope_before_read_authority(monkeypatch):
    from agent.bootstrap.meet import _task_access
    from agent.services import repository_registry

    task = SimpleNamespace(project_id="other", tenant_id="tenant-a", archived=False)
    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(task_repo=SimpleNamespace(get_by_id=lambda _: task)),
    )
    with pytest.raises(MeetError, match="meet_task_not_found"):
        _task_access(SimpleNamespace(tenant_id="tenant-a"), "project-a", "task-a")


def test_binding_changes_and_audit_events_commit_together(runtime):
    from sqlalchemy import select

    from agent.repositories.meet_bindings import events

    service, principal, store, *_ = runtime
    service.change(principal, "project-a", "", {"expected_revision": 0, "invite_url": INVITE})
    service.change(principal, "project-a", "", {"expected_revision": 1}, unlink=True)
    with store.engine.connect() as connection:
        history = list(connection.execute(select(events).order_by(events.c.revision)).mappings())
    assert [row["action"] for row in history] == ["attach", "unlink"]
    assert [row["revision"] for row in history] == [1, 2]
    assert all(row["actor"] == "alice" for row in history)
    assert "room_id" not in events.c and "invite_url" not in events.c


def test_enabled_bootstrap_uses_production_composition(monkeypatch, tmp_path):
    import agent.database
    from agent.bootstrap.meet import configure_meet

    monkeypatch.setattr(agent.database, "engine", create_engine(f"sqlite:///{tmp_path / 'hub.db'}"))
    app = Flask(__name__)
    app.config.update(ROLE="hub", ANANTA_MEET_ENABLED=True, ANANTA_MEET_ORIGIN=ORIGIN)
    app.extensions["project_access_authority"] = Mock()
    configure_meet(app)
    service = app.extensions["meet_binding_service"]
    principal = HubSourcePrincipal("alice", "tenant-a", "project-a", frozenset({"user"}))
    assert service.read(principal, "project-a")["revision"] == 0
    assert isinstance(app.extensions["meet_health_probe"], MeetHealthProbe)


def test_real_project_authority_rejects_viewer_mutation_and_archived_reads(runtime):
    from sqlmodel import Session, SQLModel

    from agent.models.project_models import ProjectCreateCommand, ProjectMembershipUpsertCommand
    from agent.services.project_access_authority import SqlProjectAccessAuthority
    from agent.services.project_lifecycle_service import ProjectLifecycleService

    service, _, store, *_ = runtime
    SQLModel.metadata.create_all(store.engine)

    def sessions():
        return Session(store.engine)

    projects = ProjectLifecycleService(session_factory=sessions)
    authority = SqlProjectAccessAuthority(session_factory=sessions)
    project = projects.create_project(
        ProjectCreateCommand(tenant_id="tenant-a", name="Meet policy test", owner_subject_id="alice")
    )
    owner = authority.require(
        tenant_id="tenant-a", project_id=project.id, subject_id="alice", capability=ProjectCapability.MANAGE_MEMBERS
    )
    projects.upsert_member(owner, ProjectMembershipUpsertCommand(subject_id="bob", role="viewer"))
    service.access = authority
    alice = HubSourcePrincipal("alice", "tenant-a", project.id, frozenset({"user"}))
    bob = HubSourcePrincipal("bob", "tenant-a", project.id, frozenset({"user"}))
    service.change(alice, project.id, "", {"expected_revision": 0, "invite_url": INVITE})
    assert service.read(bob, project.id)["invite_url"] == INVITE
    with pytest.raises(ProjectAccessError):
        service.change(bob, project.id, "", {"expected_revision": 1}, unlink=True)
    projects.archive_project(
        authority.require(
            tenant_id="tenant-a", project_id=project.id, subject_id="alice", capability=ProjectCapability.ARCHIVE
        )
    )
    with pytest.raises(ProjectAccessError):
        service.read(alice, project.id)

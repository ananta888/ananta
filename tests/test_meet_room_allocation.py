"""Deterministic policy/configuration/SQL tests; no external room or authority."""

import json
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.bootstrap.meet_room_allocation import allocation_scopes, configure_meet_room_allocation
from agent.services.meet_contract import MeetError
from agent.services.meet_room_allocation import MeetRoomAllocation, random_room
from agent.services.project_access_authority import ProjectAccessError
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests import test_meet_integration as binding_fixtures
from tests.test_meet_integration import INVITE

runtime = binding_fixtures.runtime
pytestmark = pytest.mark.timeout(45)


def allocation(runtime, *, scopes=(("tenant-a", "project-a"),), factory=None):
    binding = runtime[0]
    factory = factory or Mock(return_value="room-" + "a" * 18)
    return MeetRoomAllocation(binding, scopes, invite=binding.profile.invite, room_factory=factory), factory


def test_allocation_persists_without_claiming_membership_and_reuses_exact_binding(runtime):
    service, factory = allocation(runtime)
    principal = runtime[1]
    first = service.allocate(principal, "project-a", "", {"expected_revision": 0})
    assert first["revision"] == 1 and first["invite_url"].endswith("room-" + "a" * 18 + "&mode=room")
    assert first["membership_granted"] is False and first["room_verified"] is False
    assert first["profile"]["creation_mode"] == "meet_ui_then_attach"
    assert service.allocate(principal, "project-a", "", {"expected_revision": 1}) == first
    with pytest.raises(MeetError, match="meet_binding_conflict"):
        service.allocate(principal, "project-a", "", {"expected_revision": 0})
    factory.assert_called_once()
    assert runtime[2].get("tenant-a", "project-a", "").revision == 1


def test_existing_manual_invite_is_not_replaced_and_task_has_no_project_fallback(runtime):
    binding, principal, store, _, task_access = runtime
    binding.change(principal, "project-a", "", {"expected_revision": 0, "invite_url": INVITE})
    service, factory = allocation(runtime)
    assert service.allocate(principal, "project-a", "", {"expected_revision": 1})["invite_url"] == INVITE
    factory.assert_not_called()
    task = service.allocate(principal, "project-a", "task-a", {"expected_revision": 0})
    assert task["task_id"] == "task-a" and task["invite_url"] != INVITE
    assert store.get("tenant-a", "project-a", "").room_id == "room-0123456789abcdef01"
    task_access.assert_called_with(principal, "project-a", "task-a")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": 2**31 - 1},
        {"expected_revision": 0.0},
        {"expected_revision": "0"},
        {"expected_revision": 0, "auto_approve": True},
        {"expected_revision": 0, "invite_url": INVITE},
    ],
)
def test_closed_payload_never_generates_a_room(runtime, payload):
    service, factory = allocation(runtime)
    with pytest.raises(MeetError, match="payload_invalid"):
        service.allocate(runtime[1], "project-a", "", payload)
    factory.assert_not_called()


@pytest.mark.parametrize("scopes", [(), (("tenant-b", "project-a"),), (("tenant-a", "project-b"),)])
def test_media_permissions_do_not_imply_allocation_policy(runtime, scopes):
    service, factory = allocation(runtime, scopes=scopes)
    with pytest.raises(MeetError, match="allocation_denied"):
        service.allocate(runtime[1], "project-a", "", {"expected_revision": 0})
    factory.assert_not_called()


@pytest.mark.parametrize("role", ["worker", "service"])
def test_worker_and_service_principals_cannot_allocate(runtime, role):
    service, factory = allocation(runtime)
    principal = HubSourcePrincipal("automation", "tenant-a", "project-a", frozenset({role, "admin"}))
    with pytest.raises(MeetError, match="user_required"):
        service.allocate(principal, "project-a", "", {"expected_revision": 0})
    factory.assert_not_called()


def test_current_permission_is_checked_again_before_write(runtime):
    binding, principal, store, access, _ = runtime

    def revoked():
        access.require.side_effect = ProjectAccessError(
            reason_code="project_write_denied", public_status=403, tenant_id="tenant-a", project_id="project-a"
        )
        return "room-" + "a" * 18

    service, _ = allocation(runtime, factory=revoked)
    with pytest.raises(ProjectAccessError):
        service.allocate(principal, "project-a", "", {"expected_revision": 0})
    assert store.get("tenant-a", "project-a", "").revision == 0


def test_intervening_manual_attach_wins_without_overwrite(runtime):
    binding, principal, store, *_ = runtime

    def concurrent_attach():
        binding.change(principal, "project-a", "", {"expected_revision": 0, "invite_url": INVITE})
        return "room-" + "a" * 18

    service, _ = allocation(runtime, factory=concurrent_attach)
    with pytest.raises(MeetError, match="binding_conflict"):
        service.allocate(principal, "project-a", "", {"expected_revision": 0})
    assert store.get("tenant-a", "project-a", "").room_id == "room-0123456789abcdef01"


@pytest.mark.parametrize("failure", [OSError("private path"), ValueError("private data"), None, "https://foreign.test"])
def test_bad_entropy_or_invalid_generated_identifier_fails_without_write(runtime, failure):
    factory = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    service, _ = allocation(runtime, factory=factory)
    with pytest.raises(MeetError, match="^meet_room_allocation_unavailable$"):
        service.allocate(runtime[1], "project-a", "", {"expected_revision": 0})
    assert runtime[2].get("tenant-a", "project-a", "").revision == 0


def test_random_factory_requests_nine_cryptographic_bytes(monkeypatch):
    entropy = Mock(return_value="a" * 18)
    monkeypatch.setattr("agent.services.meet_room_allocation.secrets.token_hex", entropy)
    assert random_room() == "room-" + "a" * 18
    entropy.assert_called_once_with(9)


def test_unlink_tombstone_cannot_be_reversed_by_old_allocation_retry(runtime):
    service, factory = allocation(runtime)
    binding, principal, store, *_ = runtime
    service.allocate(principal, "project-a", "", {"expected_revision": 0})
    binding.change(principal, "project-a", "", {"expected_revision": 1}, unlink=True)
    for expected in (0, 1):
        with pytest.raises(MeetError, match="binding_conflict"):
            service.allocate(principal, "project-a", "", {"expected_revision": expected})
    assert store.get("tenant-a", "project-a", "").revision == 2
    assert store.get("tenant-a", "project-a", "").room_id is None
    factory.assert_called_once()


@pytest.mark.parametrize(
    "raw", ["[" * 2000 + "0" + "]" * 2000, json.dumps([["tenant", "p" + str(n)] for n in range(129)])]
)
def test_operator_policy_nested_or_excessive_scopes_has_a_fixed_error(raw):
    with pytest.raises(ValueError, match="^meet_room_allocation_policy_invalid$"):
        allocation_scopes(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        "null",
        '["tenant"]',
        '[["tenant"]]',
        '[["tenant",false]]',
        '[["tenant","project"],["tenant","project"]]',
        '[["tenant","../project"]]',
        "[" * 17000,
    ],
)
def test_operator_scope_policy_is_closed_and_bounded(raw):
    with pytest.raises(ValueError, match="^meet_room_allocation_policy_invalid$"):
        allocation_scopes(raw)


def test_bootstrap_is_independent_opt_in_and_composes_only_a_hub(runtime, monkeypatch):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["meet_binding_service"] = runtime[0]
    monkeypatch.delenv("ANANTA_MEET_ROOM_ALLOCATION_ENABLED", raising=False)
    configure_meet_room_allocation(app)
    assert "meet_room_allocation" not in app.extensions
    app.config.update(
        ANANTA_MEET_ROOM_ALLOCATION_ENABLED=True, ANANTA_MEET_ROOM_ALLOCATION_SCOPES='[["tenant-a","project-a"]]'
    )
    configure_meet_room_allocation(app)
    service = app.extensions["meet_room_allocation"]
    assert service.binding is runtime[0] and service.allowed_scopes == {("tenant-a", "project-a")}
    app.config["ROLE"] = "worker"
    with pytest.raises(ValueError, match="hub_required"):
        configure_meet_room_allocation(app)


def test_main_meet_bootstrap_wires_allocation_without_media_or_worker_start(runtime, monkeypatch):
    from agent.bootstrap.meet import configure_meet

    app = Flask(__name__)
    app.config.update(
        ROLE="hub",
        TESTING=True,
        ANANTA_MEET_ENABLED=True,
        ANANTA_MEET_ORIGIN=runtime[0].profile.origin,
        ANANTA_MEET_ROOM_ALLOCATION_ENABLED=True,
        ANANTA_MEET_ROOM_ALLOCATION_SCOPES='[["tenant-a","project-a"]]',
    )
    app.extensions["project_access_authority"] = runtime[3]
    monkeypatch.setenv("ANANTA_MEET_MEDIA_ENABLED", "0")
    monkeypatch.setattr("agent.repositories.meet_bindings.SqlMeetingStore", Mock(return_value=runtime[2]))
    configure_meet(app)
    allocated = app.extensions["meet_room_allocation"].allocate(runtime[1], "project-a", "", {"expected_revision": 0})
    assert allocated["revision"] == 1 and allocated["membership_granted"] is False
    assert "meet_dialog_service" not in app.extensions and "meet_turn_service" not in app.extensions

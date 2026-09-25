"""Worker-key self-heal of the public room: same publication, no human session."""

import json
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_public_room_binding import MeetPublicRoomPublication
from agent.services.meet_public_room_service import InMemoryPublicRooms, MeetPublicRoomService
from agent.services.meet_turn_service import MeetTurnService
from tests import test_meet_integration as binding_fixtures
from tests.test_meet_public_room import CREATED, INVITE, ORIGIN, OTHER, ROOM, TOKEN_OK, FakeOpener, _room, config
from worker.meet_media.contract import encode, signature

runtime = binding_fixtures.runtime
meet_client = binding_fixtures.meet_client
pytestmark = pytest.mark.timeout(45)

KEY = b"synthetic-public-room-worker-key-0000"
PATH = "/api/meet/v1/internal/public-room"
SCOPES = [("tenant-a", "project-a")]


def turns(runtime, scopes=SCOPES):
    return MeetTurnService(runtime[0], Mock(), Mock(), scopes)


def wire(app, runtime, rooms=None, *, scopes=SCOPES, public_scopes=None):
    rooms = rooms or Mock(ensure_public_room=Mock(return_value=_room()))
    app.extensions["meet_public_room_publication"] = MeetPublicRoomPublication(
        runtime[0], rooms, allowed_scopes=public_scopes
    )
    app.extensions["meet_turn_service"] = turns(runtime, scopes)
    app.extensions["meet_media_worker_key"] = KEY
    return rooms


def post(client, payload, *, key=KEY, signed=True, path=PATH):
    body = payload if isinstance(payload, bytes) else encode(payload)
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Ananta-Task-Signature"] = signature(key, body)
    return client.post(path, data=body, headers=headers)


def test_worker_key_ensures_the_public_room_and_binds_the_project(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime)
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 200
    assert response.json["roomId"] == ROOM and response.json["inviteUrl"] == INVITE
    assert response.json["visibility"] == "public" and response.json["revision"] == 1
    assert response.headers["Cache-Control"] == "no-store"
    rooms.ensure_public_room.assert_called_once_with("project-a", "")
    stored = runtime[2].get("tenant-a", "project-a", "")
    assert stored.room_id == ROOM
    # A repeated self-heal is idempotent and burns no revision.
    assert post(client, {"project_id": "project-a"}).json["revision"] == 1


def test_missing_directory_entry_is_recreated_and_the_binding_follows(meet_client, runtime):
    """Room server forgot the entry (restart without store): new id, rebinding."""
    client, app = meet_client
    runtime[0].change(
        runtime[1], "project-a", "", {"expected_revision": 0, "invite_url": f"{ORIGIN}/?room={OTHER}&mode=room"}
    )
    memory = InMemoryPublicRooms()
    memory.remember(ORIGIN, "project-a", "", OTHER)
    opener = FakeOpener([TOKEN_OK, (200, {"publicRooms": [], "ownRooms": []}), CREATED])
    wire(app, runtime, MeetPublicRoomService(config(), memory, opener=opener))
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 200
    assert (response.json["roomId"], response.json["reused"], response.json["revision"]) == (ROOM, False, 2)
    assert runtime[2].get("tenant-a", "project-a", "").room_id == ROOM
    assert memory.get(ORIGIN, "project-a", "") == ROOM
    assert json.loads(opener.requests[-1][2]) == {"mode": "room", "title": "Ananta ai-snake", "visibility": "public"}


def test_listed_entry_is_reused_without_touching_the_binding(meet_client, runtime):
    client, app = meet_client
    runtime[0].change(runtime[1], "project-a", "", {"expected_revision": 0, "invite_url": INVITE})
    memory = InMemoryPublicRooms()
    memory.remember(ORIGIN, "project-a", "", ROOM)
    opener = FakeOpener([TOKEN_OK, (200, {"publicRooms": [{"roomId": ROOM}], "ownRooms": []})])
    wire(app, runtime, MeetPublicRoomService(config(), memory, opener=opener))
    response = post(client, {"project_id": "project-a"})
    assert (response.json["roomId"], response.json["reused"], response.json["revision"]) == (ROOM, True, 1)
    assert [request[0] for request in opener.requests] == ["POST", "GET"]


@pytest.mark.parametrize(
    "key, signed",
    [(b"wrong-worker-key-000000000000000000000", True), (KEY, False)],
)
def test_wrong_or_missing_worker_signature_is_401(meet_client, runtime, key, signed):
    client, app = meet_client
    rooms = wire(app, runtime)
    response = post(client, {"project_id": "project-a"}, key=key, signed=signed)
    assert response.status_code == 401
    assert response.json == {"error": {"code": "meet_public_room_unauthorized"}}
    rooms.ensure_public_room.assert_not_called()


def test_a_user_bearer_does_not_open_the_worker_path(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime)
    body = encode({"project_id": "project-a"})
    response = client.post(PATH, data=body, headers={"Authorization": "Bearer test-user"})
    assert response.status_code == 401
    rooms.ensure_public_room.assert_not_called()


def test_disabled_public_room_or_media_is_404(meet_client, runtime):
    client, app = meet_client
    app.extensions["meet_media_worker_key"] = KEY
    app.extensions["meet_turn_service"] = turns(runtime)
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 404 and response.json == {"error": {"code": "meet_public_room_disabled"}}

    wire(app, runtime)
    del app.extensions["meet_media_worker_key"]
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 404 and response.json == {"error": {"code": "meet_media_disabled"}}


def test_project_outside_the_media_scope_gets_no_companion_identity(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime, scopes=[("tenant-a", "project-b")])
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 403 and response.json == {"error": {"code": "meet_media_policy_denied"}}
    rooms.ensure_public_room.assert_not_called()
    assert runtime[2].get("tenant-a", "project-a", "").room_id is None


def test_public_room_scope_list_still_bounds_the_worker_path(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime, public_scopes=[("tenant-a", "project-b")])
    response = post(client, {"project_id": "project-a"})
    assert response.status_code == 403 and response.json == {"error": {"code": "meet_public_room_denied"}}
    rooms.ensure_public_room.assert_not_called()


def test_task_scope_is_forwarded(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime)
    response = post(client, {"project_id": "project-a", "task_id": "task-a"})
    assert response.status_code == 200 and response.json["task_id"] == "task-a"
    rooms.ensure_public_room.assert_called_once_with("project-a", "task-a")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"project_id": ""},
        {"project_id": 7},
        {"project_id": "project-a", "task_id": 3},
        {"project_id": "project-a", "title": "other"},
        {"project_id": "project-a", "decision": "allow"},
        ["project-a"],
    ],
)
def test_payload_is_closed(meet_client, runtime, payload):
    client, app = meet_client
    rooms = wire(app, runtime)
    response = post(client, payload)
    assert response.status_code == 400
    assert response.json == {"error": {"code": "meet_public_room_payload_invalid"}}
    rooms.ensure_public_room.assert_not_called()


def test_query_oversize_and_role_are_rejected(meet_client, runtime):
    client, app = meet_client
    rooms = wire(app, runtime)
    assert post(client, {"project_id": "project-a"}, path=PATH + "?force=1").status_code == 400
    assert post(client, {"project_id": "p" * 600}).status_code == 400
    app.config["ROLE"] = "worker"
    assert post(client, {"project_id": "project-a"}).status_code == 403
    rooms.ensure_public_room.assert_not_called()


def test_operator_route_is_unchanged_and_still_user_only(meet_client, runtime):
    client, app = meet_client
    wire(app, runtime)
    body = encode({"project_id": "project-a"})
    signed = client.post(
        "/api/meet/v1/projects/project-a/public-room",
        data=body,
        headers={"X-Ananta-Task-Signature": signature(KEY, body)},
    )
    assert signed.status_code == 401
    user = client.post("/api/meet/v1/projects/project-a/public-room", headers={"Authorization": "Bearer test-user"})
    assert user.status_code == 200 and user.json["roomId"] == ROOM


def test_companion_principal_is_deterministic_and_scope_bound(runtime):
    service = turns(runtime, [("tenant-b", "project-a"), ("tenant-a", "project-a"), ("tenant-c", "project-c")])
    principal = service.companion_principal("project-a")
    assert (principal.subject_id, principal.tenant_id, principal.project_id) == (
        "ananta-companion",
        "tenant-a",
        "project-a",
    )
    assert principal.is_admin and not principal.roles & {"worker", "service"}
    with pytest.raises(MeetError, match="meet_media_policy_denied"):
        service.companion_principal("project-z")

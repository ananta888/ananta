"""Deterministic public-room tests against a fake opener; no real credentials."""

import json
import urllib.error
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import create_engine

from agent.bootstrap.meet_public_room import configure_meet_public_room
from agent.repositories.meet_public_rooms import SqlPublicRoomStore
from agent.services.meet_contract import MeetError
from agent.services.meet_public_room_binding import MeetPublicRoomPublication
from agent.services.meet_public_room_service import (
    InMemoryPublicRooms,
    MeetPublicRoomService,
    PublicRoomConfig,
    load_config,
)
from tests import test_meet_integration as binding_fixtures

runtime = binding_fixtures.runtime
meet_client = binding_fixtures.meet_client
pytestmark = pytest.mark.timeout(45)

ORIGIN = "https://webrtc.ananta.de"
TOKEN_URL = "https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/token"
ROOM = "room-" + "ab12cd34ef" + "5678ab12"[:8]
OTHER = "room-" + "f" * 18
INVITE = f"{ORIGIN}/?room={ROOM}&mode=room"
ENV = {
    "ANANTA_MEET_PUBLIC_ROOM": "1",
    "MEET_ROOM_USERNAME": "snake-operator",
    "MEET_ROOM_PASSWORD": "not-a-real-secret",
}


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self, _limit=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeOpener:
    """Records every request so tests can assert the exact wire contract."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, request.data, dict(request.headers), timeout))
        if not self.responses:
            raise AssertionError("unexpected request " + request.full_url)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(*outcome)


def config(**overrides):
    fields = {
        "server_url": ORIGIN,
        "token_url": TOKEN_URL,
        "client_id": "webrtc-browser",
        "username": "snake-operator",
        "password": "not-a-real-secret",
    }
    return PublicRoomConfig(**(fields | overrides))


def service(responses, memory=None, *, remembered=None, **overrides):
    memory = memory or InMemoryPublicRooms()
    if remembered:
        memory.remember(ORIGIN, "project-a", "", remembered)
    opener = FakeOpener(responses)
    return MeetPublicRoomService(config(**overrides), memory, opener=opener), opener


TOKEN_OK = (200, {"access_token": "operator-access-token", "expires_in": 300})
CREATED = (
    201,
    {"roomId": ROOM, "title": "Ananta ai-snake", "visibility": "public", "owned": True, "inviteUrl": INVITE},
)


def test_creates_public_room_and_remembers_it_for_the_next_call():
    memory = InMemoryPublicRooms()
    rooms, opener = service([TOKEN_OK, CREATED], memory)
    result = rooms.ensure_public_room("project-a")
    assert (result.room_id, result.visibility, result.reused) == (ROOM, "public", False)
    assert result.invite_url == INVITE
    assert memory.get(ORIGIN, "project-a", "") == ROOM

    token_request = opener.requests[0]
    assert token_request[0] == "POST" and token_request[1] == TOKEN_URL
    body = dict(pair.split("=", 1) for pair in token_request[2].decode().split("&"))
    assert body["grant_type"] == "password" and body["client_id"] == "webrtc-browser"
    create = opener.requests[-1]
    assert create[0] == "POST" and create[1] == ORIGIN + "/api/rooms"
    assert json.loads(create[2]) == {"mode": "room", "title": "Ananta ai-snake", "visibility": "public"}
    assert create[3]["Authorization"] == "Bearer operator-access-token"


def test_remembered_room_still_listed_public_is_reused_without_creating():
    memory = InMemoryPublicRooms()
    memory.remember(ORIGIN, "project-a", "", ROOM)
    rooms, opener = service([TOKEN_OK, (200, {"publicRooms": [{"roomId": ROOM}], "ownRooms": []})], memory)
    result = rooms.ensure_public_room("project-a")
    assert (result.room_id, result.reused, result.invite_url) == (ROOM, True, INVITE)
    assert [request[0] for request in opener.requests] == ["POST", "GET"]


def test_remembered_room_turned_private_is_republished_not_recreated():
    memory = InMemoryPublicRooms()
    memory.remember(ORIGIN, "project-a", "", ROOM)
    rooms, opener = service(
        [TOKEN_OK, (200, {"publicRooms": [], "ownRooms": [{"roomId": ROOM, "visibility": "private"}]}), (200, {})],
        memory,
    )
    assert rooms.ensure_public_room("project-a").reused is True
    assert opener.requests[-1][0] == "PATCH"
    assert opener.requests[-1][1] == f"{ORIGIN}/api/rooms/{ROOM}"
    assert json.loads(opener.requests[-1][2]) == {"visibility": "public"}


def test_forgotten_room_absent_from_the_directory_is_recreated():
    memory = InMemoryPublicRooms()
    memory.remember(ORIGIN, "project-a", "", OTHER)
    rooms, _ = service([TOKEN_OK, (200, {"publicRooms": [{"roomId": ROOM}], "ownRooms": []}), CREATED], memory)
    result = rooms.ensure_public_room("project-a")
    assert (result.room_id, result.reused) == (ROOM, False)
    assert memory.get(ORIGIN, "project-a", "") == ROOM


def test_project_and_task_keep_separate_directory_entries():
    memory = InMemoryPublicRooms()
    rooms, _ = service([TOKEN_OK, CREATED], memory)
    rooms.ensure_public_room("project-a")
    task_rooms, _ = service([TOKEN_OK, (201, {"roomId": OTHER, "visibility": "public"})], memory)
    assert task_rooms.ensure_public_room("project-a", "task-a").room_id == OTHER
    assert memory.get(ORIGIN, "project-a", "") == ROOM


@pytest.mark.parametrize(
    "responses, remembered, code",
    [
        ([(200, {"access_token": ""})], None, "meet_public_room_token_unavailable"),
        ([(400, {"error": "invalid_grant"})], None, "meet_public_room_token_unavailable"),
        ([(401, {})], None, "meet_public_room_unauthorized"),
        ([(200, b"not-json")], None, "meet_public_room_response_invalid"),
        ([urllib.error.URLError("dns")], None, "meet_public_room_token_unavailable"),
        ([TOKEN_OK, (500, {})], OTHER, "meet_public_room_directory_unavailable"),
        ([TOKEN_OK, urllib.error.URLError("reset")], OTHER, "meet_public_room_directory_unavailable"),
        ([TOKEN_OK, (403, {})], None, "meet_public_room_unauthorized"),
        ([TOKEN_OK, (201, {"roomId": "nope"})], None, "meet_public_room_unavailable"),
        ([TOKEN_OK, (200, {})], None, "meet_public_room_unavailable"),
        ([TOKEN_OK, (201, {"roomId": ROOM, "visibility": "private"})], None, "meet_public_room_visibility_denied"),
    ],
)
def test_failures_surface_reason_codes_and_never_fall_back(responses, remembered, code):
    memory = InMemoryPublicRooms()
    rooms, _ = service(responses, memory, remembered=remembered)
    with pytest.raises(MeetError, match=code):
        rooms.ensure_public_room("project-a")
    assert memory.get(ORIGIN, "project-a", "") == remembered


@pytest.mark.parametrize(
    "project, task", [("", ""), ("a b", ""), ("project-a", "task/../a"), (None, ""), ("x" * 161, "")]
)
def test_scope_input_is_rejected_before_any_request(project, task):
    rooms, opener = service([])
    with pytest.raises(MeetError, match="meet_public_room_scope_invalid"):
        rooms.ensure_public_room(project, task)
    assert opener.requests == []


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"server_url": "http://webrtc.ananta.de"}, "meet_public_room_server_invalid"),
        ({"server_url": "https://webrtc.ananta.de/"}, "meet_public_room_server_invalid"),
        ({"server_url": "https://user:pw@webrtc.ananta.de"}, "meet_public_room_server_invalid"),
        ({"token_url": "http://keycloak.ananta.de/x"}, "meet_public_room_token_url_invalid"),
        ({"token_url": TOKEN_URL + "?a=b"}, "meet_public_room_token_url_invalid"),
        ({"client_id": "bad client"}, "meet_public_room_client_invalid"),
        ({"title": ""}, "meet_public_room_title_invalid"),
        ({"title": "a\nb"}, "meet_public_room_title_invalid"),
        ({"timeout": 0}, "meet_public_room_timeout_invalid"),
        ({"username": "", "password": ""}, "meet_public_room_credentials_missing"),
        ({"password": ""}, "meet_public_room_credentials_missing"),
    ],
)
def test_configuration_is_validated_up_front(overrides, code):
    with pytest.raises(MeetError, match=code):
        config(**overrides)


def test_client_credentials_grant_is_used_when_no_user_is_configured():
    rooms, opener = service([TOKEN_OK, CREATED], username="", password="", client_secret="shhh")
    rooms.ensure_public_room("project-a")
    body = dict(pair.split("=", 1) for pair in opener.requests[0][2].decode().split("&"))
    assert body["grant_type"] == "client_credentials" and body["client_secret"] == "shhh"


def test_bearer_is_never_replayed_across_a_redirect():
    from agent.services.meet_public_room_service import NoRedirect

    with pytest.raises(MeetError, match="meet_public_room_redirect_denied"):
        NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.test/")


def test_sql_store_persists_and_overwrites_per_project_and_task(tmp_path):
    store = SqlPublicRoomStore(create_engine(f"sqlite:///{tmp_path / 'rooms.db'}"))
    store.initialize()
    assert store.get(ORIGIN, "project-a", "") is None
    store.remember(ORIGIN, "project-a", "", ROOM)
    store.remember(ORIGIN, "project-a", "task-a", OTHER)
    assert (store.get(ORIGIN, "project-a", ""), store.get(ORIGIN, "project-a", "task-a")) == (ROOM, OTHER)
    store.remember(ORIGIN, "project-a", "", OTHER)
    assert store.get(ORIGIN, "project-a", "") == OTHER
    assert store.get("https://other.test", "project-a", "") is None


# --- binding publication --------------------------------------------------


def publication(runtime, room=None, **kwargs):
    rooms = Mock(ensure_public_room=Mock(return_value=room or _room()))
    return MeetPublicRoomPublication(runtime[0], rooms, **kwargs), rooms


def _room(room_id=ROOM, reused=False):
    from agent.services.meet_public_room_service import PublicRoom

    return PublicRoom(room_id, f"{ORIGIN}/?room={room_id}&mode=room", "public", reused)


def test_publication_writes_the_public_room_into_the_binding(runtime):
    service, rooms = publication(runtime)
    result = service.publish(runtime[1], "project-a")
    assert result["roomId"] == ROOM and result["visibility"] == "public"
    assert result["inviteUrl"] == INVITE and result["revision"] == 1
    assert runtime[2].get("tenant-a", "project-a", "").room_id == ROOM
    rooms.ensure_public_room.assert_called_once_with("project-a", "")


def test_publication_is_idempotent_and_does_not_burn_a_revision(runtime):
    service, _ = publication(runtime)
    first = service.publish(runtime[1], "project-a")
    assert service.publish(runtime[1], "project-a") == first


def test_publication_repoints_a_binding_that_names_another_room(runtime):
    runtime[0].change(runtime[1], "project-a", "", {"expected_revision": 0, "invite_url": binding_fixtures.INVITE})
    service, _ = publication(runtime)
    assert service.publish(runtime[1], "project-a")["revision"] == 2
    assert runtime[2].get("tenant-a", "project-a", "").room_id == ROOM


def test_publication_honours_the_task_scope_and_the_operator_allow_list(runtime):
    service, rooms = publication(runtime, allowed_scopes=[("tenant-a", "project-a")])
    service.publish(runtime[1], "project-a", "task-a")
    rooms.ensure_public_room.assert_called_once_with("project-a", "task-a")
    runtime[4].assert_called_with(runtime[1], "project-a", "task-a")
    denied, blocked = publication(runtime, allowed_scopes=[("tenant-a", "project-b")])
    with pytest.raises(MeetError, match="meet_public_room_denied"):
        denied.publish(runtime[1], "project-a")
    blocked.ensure_public_room.assert_not_called()


def test_publication_leaves_the_binding_untouched_when_the_room_server_fails(runtime):
    rooms = Mock(ensure_public_room=Mock(side_effect=MeetError("meet_public_room_unavailable", 502)))
    service = MeetPublicRoomPublication(runtime[0], rooms)
    with pytest.raises(MeetError, match="meet_public_room_unavailable"):
        service.publish(runtime[1], "project-a")
    assert runtime[2].get("tenant-a", "project-a", "").room_id is None


# --- configuration and HTTP adapter --------------------------------------


def test_service_stays_disabled_without_the_flag_or_credentials():
    assert load_config({}) is None
    assert load_config(ENV | {"ANANTA_MEET_PUBLIC_ROOM": "0"}) is None
    with pytest.raises(MeetError, match="meet_public_room_credentials_missing"):
        load_config({"ANANTA_MEET_PUBLIC_ROOM": "1"})
    loaded = load_config(ENV)
    assert loaded.title == "Ananta ai-snake" and loaded.server_url == ORIGIN
    assert loaded.token_url == TOKEN_URL and loaded.client_id == "webrtc-browser"


def bootstrap_app(runtime):
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.extensions["meet_binding_service"] = runtime[0]
    return app


def test_bootstrap_opt_in_scopes_and_origin_agreement(runtime):
    app = bootstrap_app(runtime)
    configure_meet_public_room(app, {})
    assert "meet_public_room_publication" not in app.extensions

    configure_meet_public_room(app, ENV)
    assert app.extensions["meet_public_room_publication"].allowed_scopes is None

    app = bootstrap_app(runtime)
    configure_meet_public_room(app, ENV | {"ANANTA_MEET_PUBLIC_ROOM_SCOPES": '[["tenant-a","project-a"]]'})
    assert app.extensions["meet_public_room_publication"].allowed_scopes == frozenset({("tenant-a", "project-a")})

    with pytest.raises(ValueError, match="meet_public_room_policy_invalid"):
        configure_meet_public_room(bootstrap_app(runtime), ENV | {"ANANTA_MEET_PUBLIC_ROOM_SCOPES": "nope"})
    with pytest.raises(ValueError, match="meet_public_room_origin_mismatch"):
        configure_meet_public_room(bootstrap_app(runtime), ENV | {"MEET_ROOM_SERVER_URL": "https://other.test"})
    with pytest.raises(ValueError, match="meet_public_room_hub_required"):
        configure_meet_public_room(Flask(__name__), ENV)


PATH = "/api/meet/v1/projects/project-a/public-room"
HEADERS = {"Authorization": "Bearer test-user"}


def test_route_requires_user_auth_opt_in_and_an_empty_body(meet_client, runtime):
    client, app = meet_client
    assert client.post(PATH).status_code == 401
    disabled = client.post(PATH, headers=HEADERS)
    assert disabled.status_code == 404 and disabled.json == {"error": {"code": "meet_public_room_disabled"}}

    service, rooms = publication(runtime)
    app.extensions["meet_public_room_publication"] = service
    response = client.post(PATH, headers=HEADERS)
    assert response.status_code == 200 and response.json["roomId"] == ROOM
    assert response.json["inviteUrl"] == INVITE and response.json["visibility"] == "public"
    assert response.headers["Cache-Control"] == "no-store"

    assert client.post(PATH + "?force=1", headers=HEADERS).status_code == 400
    assert client.post(PATH, headers=HEADERS, json={"title": "other"}).status_code == 400
    task = client.post("/api/meet/v1/projects/project-a/tasks/task-a/public-room", headers=HEADERS)
    assert task.status_code == 200 and task.json["task_id"] == "task-a"
    rooms.ensure_public_room.assert_called_with("project-a", "task-a")

    app.config["ROLE"] = "worker"
    assert client.post(PATH, headers=HEADERS).status_code == 403


def test_route_maps_room_server_failure_to_a_reason_code(meet_client, runtime):
    client, app = meet_client
    rooms = Mock(ensure_public_room=Mock(side_effect=MeetError("meet_public_room_token_unavailable", 502)))
    app.extensions["meet_public_room_publication"] = MeetPublicRoomPublication(runtime[0], rooms)
    response = client.post(PATH, headers=HEADERS)
    assert response.status_code == 502
    assert response.json == {"error": {"code": "meet_public_room_token_unavailable"}}

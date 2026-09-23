"""Operator-owned public directory entry on the configured room server.

The Hub never mints a public room id itself: the room server generates it and
owns the directory. This module only carries an operator identity to that
server, remembers the id it returned and hands it back. There is deliberately
no cloud or local fallback — a room server that cannot be reached is an error,
never a silently degraded room.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from agent.services.meet_contract import MeetError

ROOM_ID = re.compile(r"room-[a-f0-9]{18}")
MAX_BODY = 262_144
MAX_TITLE = 120


def _origin(value, code):
    """Accept only a bare https origin, so no bearer can leak into a path."""
    try:
        url = urlsplit(value)
    except ValueError:
        raise MeetError(code) from None
    if (
        not isinstance(value, str)
        or len(value) > 255
        or url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path
        or url.query
        or url.fragment
        or url.port is not None
        or re.search(r"[\s\\%]", value)
    ):
        raise MeetError(code)
    return value


def _token_url(value):
    try:
        url = urlsplit(value)
    except ValueError:
        raise MeetError("meet_public_room_token_url_invalid") from None
    if (
        not isinstance(value, str)
        or len(value) > 512
        or url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or not url.path.startswith("/")
        or url.query
        or url.fragment
        or re.search(r"[\s\\]", value)
    ):
        raise MeetError("meet_public_room_token_url_invalid")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would replay the operator bearer against a foreign origin."""

    def redirect_request(self, *_args, **_kwargs):
        raise MeetError("meet_public_room_redirect_denied", 502)


@dataclass(frozen=True)
class PublicRoom:
    room_id: str
    invite_url: str
    visibility: str
    reused: bool


class PublicRoomMemory(Protocol):
    def get(self, server_origin: str, project: str, task: str) -> str | None: ...
    def remember(self, server_origin: str, project: str, task: str, room_id: str) -> None: ...


class InMemoryPublicRooms:
    """Process-local memory; a restart re-creates the directory entry once."""

    def __init__(self):
        self._rooms: dict[tuple[str, str, str], str] = {}

    def get(self, server_origin, project, task):
        return self._rooms.get((server_origin, project, task))

    def remember(self, server_origin, project, task, room_id):
        self._rooms[(server_origin, project, task)] = room_id


@dataclass(frozen=True)
class PublicRoomConfig:
    """Fully explicit operator configuration; credentials come from the env."""

    server_url: str
    token_url: str
    client_id: str
    title: str = "Ananta ai-snake"
    username: str = ""
    password: str = ""
    client_secret: str = ""
    scope: str = "openid"
    timeout: float = 10.0

    def __post_init__(self):
        object.__setattr__(self, "server_url", _origin(self.server_url, "meet_public_room_server_invalid"))
        object.__setattr__(self, "token_url", _token_url(self.token_url))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", self.client_id or ""):
            raise MeetError("meet_public_room_client_invalid")
        if not self.title or len(self.title) > MAX_TITLE or re.search(r"[\x00-\x1f\x7f]", self.title):
            raise MeetError("meet_public_room_title_invalid")
        if len(self.scope) > 160 or re.search(r"[^A-Za-z0-9 _.:-]", self.scope):
            raise MeetError("meet_public_room_scope_invalid")
        if not 0 < self.timeout <= 60:
            raise MeetError("meet_public_room_timeout_invalid")
        if not self.grant:
            raise MeetError("meet_public_room_credentials_missing")

    @property
    def grant(self):
        """Password grant wins: it yields a user identity the server can own."""
        if self.username and self.password:
            return "password"
        return "client_credentials" if self.client_secret else ""

    def token_form(self):
        fields = {"grant_type": self.grant, "client_id": self.client_id}
        if self.grant == "password":
            fields |= {"username": self.username, "password": self.password}
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        if self.scope:
            fields["scope"] = self.scope
        return urllib.parse.urlencode(fields).encode("ascii")


def load_config(environ, *, issuer="https://keycloak.ananta.de/realms/ananta"):
    """Return the configured service, or None when the operator left it off."""
    if str(environ.get("ANANTA_MEET_PUBLIC_ROOM", "0")).lower() not in {"1", "true"}:
        return None
    server = environ.get("MEET_ROOM_SERVER_URL") or "https://webrtc.ananta.de"
    return PublicRoomConfig(
        server_url=server,
        token_url=environ.get("MEET_ROOM_TOKEN_URL") or f"{issuer}/protocol/openid-connect/token",
        client_id=environ.get("MEET_ROOM_CLIENT_ID") or "webrtc-browser",
        title=environ.get("MEET_PUBLIC_ROOM_TITLE") or "Ananta ai-snake",
        username=environ.get("MEET_ROOM_USERNAME", ""),
        password=environ.get("MEET_ROOM_PASSWORD", ""),
        client_secret=environ.get("MEET_ROOM_CLIENT_SECRET", ""),
        scope=environ.get("MEET_ROOM_SCOPE", "openid"),
        timeout=float(environ.get("MEET_ROOM_TIMEOUT", "10")),
    )


class MeetPublicRoomService:
    def __init__(self, config: PublicRoomConfig, memory: PublicRoomMemory | None = None, *, opener=None):
        self.config = config
        self.memory = memory if memory is not None else InMemoryPublicRooms()
        self.opener = opener or urllib.request.build_opener(NoRedirect)

    # --- transport -----------------------------------------------------

    def _send(self, request, code):
        try:
            with self.opener.open(request, timeout=self.config.timeout) as response:
                body = response.read(MAX_BODY + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                body = error.read(MAX_BODY + 1)
            except OSError:
                body = b""
        except MeetError:
            raise
        except (urllib.error.URLError, OSError, ValueError):
            raise MeetError(code, 502) from None
        if len(body) > MAX_BODY:
            raise MeetError("meet_public_room_response_invalid", 502)
        if status in (401, 403):
            raise MeetError("meet_public_room_unauthorized", 502)
        try:
            return status, json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeError):
            raise MeetError("meet_public_room_response_invalid", 502) from None

    def _token(self):
        request = urllib.request.Request(
            self.config.token_url,
            data=self.config.token_form(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        status, payload = self._send(request, "meet_public_room_token_unavailable")
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if status != 200 or not isinstance(token, str) or not token or len(token) > 8192:
            raise MeetError("meet_public_room_token_unavailable", 502)
        return token

    def _room_request(self, path, token, *, method="GET", payload=None):
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(self.config.server_url + path, data=data, method=method, headers=headers)

    # --- directory -----------------------------------------------------

    @staticmethod
    def _entries(payload, key):
        rows = payload.get(key) if isinstance(payload, dict) else None
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _invite(self, payload, room_id):
        invite = payload.get("inviteUrl") if isinstance(payload, dict) else None
        if isinstance(invite, str) and invite.startswith(self.config.server_url + "/?room=" + room_id):
            return invite
        return f"{self.config.server_url}/?room={room_id}&mode=room"

    @staticmethod
    def _room_id(entry):
        value = entry.get("roomId") or entry.get("id") or ""
        return value if isinstance(value, str) and ROOM_ID.fullmatch(value) else ""

    def _lookup(self, token, room_id):
        """Return "public", "owned" or "" for the remembered directory entry."""
        status, payload = self._send(self._room_request("/api/rooms", token), "meet_public_room_directory_unavailable")
        if status != 200:
            raise MeetError("meet_public_room_directory_unavailable", 502)
        if any(self._room_id(entry) == room_id for entry in self._entries(payload, "publicRooms")):
            return "public"
        for entry in self._entries(payload, "ownRooms"):
            if self._room_id(entry) == room_id:
                return "public" if entry.get("visibility") == "public" else "owned"
        return ""

    def _publish(self, token, room_id):
        """Re-publish an owned room the operator previously made private."""
        status, _ = self._send(
            self._room_request(f"/api/rooms/{room_id}", token, method="PATCH", payload={"visibility": "public"}),
            "meet_public_room_unavailable",
        )
        return status in (200, 204)

    def _create(self, token):
        status, payload = self._send(
            self._room_request(
                "/api/rooms",
                token,
                method="POST",
                payload={"mode": "room", "title": self.config.title, "visibility": "public"},
            ),
            "meet_public_room_unavailable",
        )
        room_id = self._room_id(payload) if isinstance(payload, dict) else ""
        if status not in (200, 201) or not room_id:
            raise MeetError("meet_public_room_unavailable", 502)
        if payload.get("visibility") not in (None, "public"):
            raise MeetError("meet_public_room_visibility_denied", 502)
        return room_id, self._invite(payload, room_id)

    # --- public API ----------------------------------------------------

    def ensure_public_room(self, project, task="") -> PublicRoom:
        """Idempotently keep one public directory entry per project/task."""
        for value in (project, task):
            if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)):
                raise MeetError("meet_public_room_scope_invalid")
        if not project:
            raise MeetError("meet_public_room_scope_invalid")
        token = self._token()
        remembered = self.memory.get(self.config.server_url, project, task)
        if remembered and ROOM_ID.fullmatch(remembered):
            state = self._lookup(token, remembered)
            if state == "public" or (state == "owned" and self._publish(token, remembered)):
                return PublicRoom(remembered, self._invite({}, remembered), "public", True)
        room_id, invite = self._create(token)
        self.memory.remember(self.config.server_url, project, task, room_id)
        return PublicRoom(room_id, invite, "public", False)

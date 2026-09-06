"""Transport-neutral meeting binding and strictly configured Meet links."""

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit


class MeetError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class MeetingBinding:
    revision: int = 0
    room_id: str | None = None


class MeetingStore(Protocol):
    def get(self, tenant: str, project: str, task: str) -> MeetingBinding: ...
    def replace(
        self, tenant: str, project: str, task: str, expected: int, room_id: str | None, actor: str
    ) -> MeetingBinding: ...


@dataclass(frozen=True)
class MeetProfile:
    origin: str

    def __post_init__(self):
        try:
            url = urlsplit(self.origin)
            valid = (
                len(self.origin) <= 255
                and url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and url.path == ""
                and not url.query
                and not url.fragment
                and url.port is None
                and self.origin == f"https://{url.hostname}"
                and not re.search(r"[\s\\%]", self.origin)
            )
        except ValueError:
            valid = False
        if not valid:
            raise MeetError("meet_origin_invalid")

    def parse_invite(self, invite: object) -> str:
        if not isinstance(invite, str) or len(invite) > 512:
            raise MeetError("meet_invite_invalid")
        try:
            url = urlsplit(invite)
            fields = parse_qsl(url.query, strict_parsing=True)
            params = dict(fields)
            valid = (
                f"{url.scheme}://{url.netloc}" == self.origin
                and url.path == "/"
                and not url.fragment
                and not re.search(r"[\s\\]", invite)
                and len(fields) == 2
                and set(params) == {"room", "mode"}
                and params["mode"] == "room"
            )
        except ValueError:
            valid = False
            params = {}
        if not valid or not re.fullmatch(r"room-[a-f0-9]{18}", params.get("room", "")):
            raise MeetError("meet_invite_invalid")
        return params["room"]

    def invite(self, room_id: str) -> str:
        if not re.fullmatch(r"room-[a-f0-9]{18}", room_id):
            raise MeetError("meet_room_invalid")
        return f"{self.origin}/?room={room_id}&mode=room"

    def public(self) -> dict:
        return {
            "schema": "ananta.meet-profile.v1",
            "provider": "ananta_meet",
            "origin": self.origin,
            "create_url": self.origin + "/",
            "creation_mode": "meet_ui_then_attach",
            "join_authority": "meet_oidc_device_ticket",
            "room_status_supported": False,
            "media_bridge_supported": False,
        }

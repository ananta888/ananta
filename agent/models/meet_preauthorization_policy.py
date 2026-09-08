"""Explicit operator scope and ceilings; never inferred from meeting contents."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

from agent.services.meet_contract import MeetError, MeetProfile
from ananta_contracts.meet_source_profile import CAPABILITIES


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise MeetError("meet_preauthorization_invalid")
    return value


def integer(value, minimum=1, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MeetError("meet_preauthorization_invalid")
    return value


def timestamp(now):
    if type(now) not in {int, float} or not math.isfinite(now) or not 0 < now < (2**53) / 1000:
        raise MeetError("meet_preauthorization_clock_invalid", 503)
    return now


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


SCOPE_FIELDS = ("tenant_id", "project_id", "parent_task_id", "owner_subject", "origin", "room_id")


@dataclass(frozen=True)
class MeetPreauthorizationPolicy:
    policy_id: str
    tenant_id: str
    project_id: str
    parent_task_id: str
    owner_subject: str
    origin: str
    room_id: str
    capabilities: tuple[str, ...]
    valid_from: int
    expires_at: int
    max_duration_seconds: int
    max_dispatches: int

    @classmethod
    def parse(cls, value):
        if type(value) is not dict or set(value) != {"schema", *cls.__dataclass_fields__}:
            raise MeetError("meet_preauthorization_invalid")
        if value["schema"] != "ananta.meet-dialog-preauthorization-policy.v1":
            raise MeetError("meet_preauthorization_invalid")
        for key in ("policy_id", "tenant_id", "project_id", "parent_task_id", "owner_subject"):
            identifier(value[key])
        if value["policy_id"].startswith(("SRC_", "RUN_")):
            raise MeetError("meet_preauthorization_invalid")
        if type(value["origin"]) is not str or type(value["room_id"]) is not str:
            raise MeetError("meet_preauthorization_invalid")
        MeetProfile(value["origin"]).invite(value["room_id"])
        caps = value["capabilities"]
        if (
            type(caps) is not list
            or not caps
            or len(caps) > len(CAPABILITIES)
            or any(type(item) is not str for item in caps)
            or len(caps) != len(set(caps))
            or not set(caps) <= CAPABILITIES
        ):
            raise MeetError("meet_preauthorization_invalid")
        integer(value["valid_from"], maximum=2**42)
        integer(value["expires_at"], maximum=2**42)
        integer(value["expires_at"] - value["valid_from"], minimum=30, maximum=30 * 86400)
        integer(value["max_duration_seconds"], minimum=30, maximum=7200)
        integer(value["max_dispatches"], maximum=1000)
        return cls(**{k: tuple(sorted(caps)) if k == "capabilities" else value[k] for k in cls.__dataclass_fields__})

    def document(self):
        return asdict(self) | {
            "schema": "ananta.meet-dialog-preauthorization-policy.v1",
            "capabilities": list(self.capabilities),
        }

    def scope(self):
        return {key: getattr(self, key) for key in SCOPE_FIELDS}

    def require_assignment(self, assignment, now, *, starting=False):
        now = timestamp(now)
        if any(assignment.get(key) != value for key, value in self.scope().items()):
            raise MeetError("meet_preauthorization_scope_denied", 403)
        deadline = assignment["deadline"]
        if (
            not self.valid_from <= now < self.expires_at
            or not now < deadline <= self.expires_at
            or (starting and deadline > int(now) + self.max_duration_seconds)
            or not set(assignment["capabilities"]) <= set(self.capabilities)
        ):
            raise MeetError("meet_preauthorization_denied", 403)

"""Immutable operator rules keyed by verified Hub organization role, not input text."""

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType

from agent.models.meet_machine_principal import MeetMachinePrincipal
from ananta_contracts.meet_dialog import ID

_SCOPE = ("tenant_id", "project_id", "organization_id", "role_slot_id")


@dataclass(frozen=True)
class SpeakerDecision:
    priority: int
    barge_in: bool
    organization_id: str
    policy_digest: str


def _digest(rule):
    return hashlib.sha256(json.dumps(rule, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class MeetSpeakerPolicy:
    def __init__(self, rows):
        if type(rows) is not list or len(rows) > 64:
            raise ValueError("meet_speaker_policy_invalid")
        rules = {}
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != {*_SCOPE, "policy_id", "revision", "priority", "barge_in"}
                or any(type(row[k]) is not str or not ID.fullmatch(row[k]) for k in (*_SCOPE, "policy_id"))
                or row["policy_id"].startswith(("SRC_", "RUN_"))
                or type(row["revision"]) is not int
                or not 0 < row["revision"] < 2**31
                or type(row["priority"]) is not int
                or not 0 <= row["priority"] <= 2
                or type(row["barge_in"]) is not bool
                or row["barge_in"]
                and row["priority"] == 0
            ):
                raise ValueError("meet_speaker_policy_invalid")
            key = tuple(row[k] for k in _SCOPE)
            if key in rules:
                raise ValueError("meet_speaker_policy_duplicate")
            rules[key] = SpeakerDecision(row["priority"], row["barge_in"], row["organization_id"], _digest(row))
        self.rules = MappingProxyType(rules)

    def decide(self, scope):
        principal = scope.machine_principal
        if principal is not None and (
            not isinstance(principal, MeetMachinePrincipal)
            or principal.tenant_id != scope.tenant_id
            or principal.project_id != scope.project_id
        ):
            raise ValueError("meet_speaker_policy_scope_invalid")
        key = None if principal is None else tuple(getattr(principal, k) for k in _SCOPE)
        return self.rules.get(key) or SpeakerDecision(
            0,
            False,
            "" if principal is None else principal.organization_id,
            _digest({"schema": "ananta.meet-speaker-default-policy.v1", "priority": 0, "barge_in": False}),
        )

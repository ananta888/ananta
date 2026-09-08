"""Hub-issued, single-task admission grant for the separate Meet authority."""

import re
import secrets
from urllib.parse import urlsplit

import jwt

from agent.services.meet_contract import MeetError
from agent.services.meet_signing_key import load_meet_signing_key


class MeetMachineGrantIssuer:
    def __init__(self, issuer, key_path, *, key_loader=load_meet_signing_key, key_id=None):
        parsed = urlsplit(issuer)
        if parsed.scheme != "https" or parsed.netloc != parsed.hostname or issuer != f"https://{parsed.hostname}":
            raise ValueError("meet_machine_issuer_invalid")
        if key_id is not None and (
            not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id)
        ):
            raise ValueError("meet_machine_key_id_invalid")
        self.key = key_loader(key_path)
        self.issuer = issuer
        self._key_id = key_id

    def _headers(self, token_type):
        return {"typ": token_type, **({"kid": self._key_id} if self._key_id is not None else {})}

    def issue_dialog(self, authority, task_id, lease_id, runtime_id, now):
        """Only a fresh Hub task/policy lookup may produce this additive v2 grant."""
        scope = authority.current(task_id, lease_id, runtime_id)
        issued = int(now)
        if not issued < scope.deadline or scope.deadline > now + 7200:
            raise MeetError("meet_dialog_expired", 403)
        token = jwt.encode(
            {
                "iss": self.issuer,
                "aud": "ananta-meet-machine-v2",
                "sub": "ananta",
                "iat": issued,
                "exp": min(issued + 120, scope.deadline),
                "jti": secrets.token_hex(16),
                "roomId": scope.room_id,
                "taskId": scope.task_id,
                "tenantId": scope.tenant_id,
                "projectId": scope.project_id,
                "runtimeId": scope.runtime_id,
                "sessionId": scope.session_id,
                "capabilities": list(scope.capabilities),
            },
            self.key,
            algorithm="EdDSA",
            headers=self._headers("ananta-meet-machine-v2+jwt"),
        )
        return {"origin": scope.origin, "room_id": scope.room_id, "grant": token}

    def issue(self, turn, binding, principal, now, task=""):
        stored = binding.read(principal, turn["project_id"], task)
        if not stored["invite_url"]:
            raise MeetError("meet_room_binding_required", 409)
        room = binding.profile.parse_invite(stored["invite_url"])
        token = jwt.encode(
            {
                "iss": self.issuer,
                "aud": "ananta-meet-machine-v1",
                "sub": "ananta",
                "iat": int(now),
                "exp": turn["deadline"],
                "jti": turn["lease_id"],
                "roomId": room,
                "taskId": turn["task_id"],
                "tenantId": turn["tenant_id"],
                "projectId": turn["project_id"],
            },
            self.key,
            algorithm="EdDSA",
            headers=self._headers("ananta-meet-machine+jwt"),
        )
        return {"origin": binding.profile.origin, "room_id": room, "grant": token}

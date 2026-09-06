"""Hub-issued, single-task admission grant for the separate Meet authority."""

from pathlib import Path
from urllib.parse import urlsplit

import jwt

from agent.services.meet_contract import MeetError


class MeetMachineGrantIssuer:
    def __init__(self, issuer, key_path):
        parsed = urlsplit(issuer)
        if parsed.scheme != "https" or parsed.netloc != parsed.hostname or issuer != f"https://{parsed.hostname}":
            raise ValueError("meet_machine_issuer_invalid")
        key_file = Path(key_path)
        if key_file.stat().st_mode & 0o077:
            raise ValueError("meet_machine_key_permissions")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        self.key = load_pem_private_key(key_file.read_bytes(), password=None)
        if not isinstance(self.key, Ed25519PrivateKey):
            raise ValueError("meet_machine_key_type_invalid")
        self.issuer = issuer

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
            headers={"typ": "ananta-meet-machine+jwt"},
        )
        return {"origin": binding.profile.origin, "room_id": room, "grant": token}

from __future__ import annotations

from typing import Any

_DEFAULT_CLAIM_ROLE_MAP: dict[str, str] = {
    "ananta-admin": "admin",
    "ananta-user": "user",
    "ananta-viewer": "viewer",
}

_DEFAULT_TERMINAL_PERMISSION_MAP: dict[str, list[str]] = {
    "ananta-terminal-hub": [
        "terminal.hub.list",
        "terminal.hub.create",
        "terminal.hub.attach",
        "terminal.hub.read",
        "terminal.hub.write",
        "terminal.hub.kill",
        "terminal.hub_as_worker.create",
        "terminal.hub_as_worker.attach",
    ],
    "ananta-terminal-worker": [
        "terminal.worker.list",
        "terminal.worker.create",
        "terminal.worker.attach",
        "terminal.worker.read",
        "terminal.worker.write",
        "terminal.worker.kill",
    ],
}


def map_claims_to_auth(claims: dict[str, Any]) -> dict[str, Any]:
    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or claims.get("preferred_username") or sub)
    groups: list[str] = []
    raw_groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(raw_groups, list):
        groups = [str(g) for g in raw_groups if g]

    role = "viewer"
    for group in groups:
        mapped = _DEFAULT_CLAIM_ROLE_MAP.get(group)
        if mapped:
            role = mapped
            break

    terminal_permissions: list[str] = []
    for group in groups:
        perms = _DEFAULT_TERMINAL_PERMISSION_MAP.get(group) or []
        terminal_permissions.extend(perms)

    return {
        "sub": sub,
        "username": email,
        "role": role,
        "roles": [role],
        "groups": groups,
        "terminal_permissions": list(set(terminal_permissions)),
        "auth_source": "oidc",
        "email": email,
    }

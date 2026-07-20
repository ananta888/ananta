#!/usr/bin/env python3
"""Isolated Hub composition used by the real SFU failover browser gate.

This module does not emulate Hub decisions.  It boots the productive SFU
admission, group-key and semantic-compute HTTP blueprints against the same SQL
repositories used by a Hub.  The only fixture operation runs before the Hub is
started and creates bounded share-session memberships plus short-lived user
JWTs in an ephemeral descriptor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import jwt
from flask import Flask, jsonify

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENGINES = ("chromium", "firefox")
_IDENTITIES = ("publisher", "receiver-1", "receiver-2", "stale-key-probe")
_TENANT = "semantic-sfu-live-e2e"


class HubFixtureError(RuntimeError):
    """Fail-closed setup/configuration error without fixture secrets."""


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o600)


def _user_token(identity: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": identity,
            "username": identity,
            "tenant_id": _TENANT,
            "role": "user",
            "iat": now,
            "exp": now + 600,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def seed_fixture(output: Path) -> dict[str, Any]:
    """Create real Hub membership/epoch rows before starting the API."""

    from agent.database import init_db
    from agent.services.share_session_service import get_share_session_service
    from agent.services.webrtc_epoch_service import get_webrtc_epoch_service

    init_db()
    shares = get_share_session_service()
    permissions = {
        "chat": True,
        "view_tui": True,
        "remote_cursor": False,
        "artifact_share": True,
        # The live failover scenario exercises the productive semantic compute
        # scheduler.  Rights are still attenuated into short-lived, purpose-
        # bound Hub grants before a browser can call a compute route.
        "remote_control": True,
    }
    sessions: dict[str, dict[str, Any]] = {}
    for engine in _ENGINES:
        created = shares.create_session(
            owner_user_id=_IDENTITIES[0],
            owner_device_id=f"sfu-e2e-{engine}-owner",
            title="Semantic SFU failover fixture",
            mode="group",
            transport="semantic_sfu",
            permissions=permissions,
            expires_at=time.time() + 600,
            tenant_id=_TENANT,
        )
        session_id = str(created.get("id") or "")
        invite_code = str(created.get("invite_code") or "")
        if not session_id or not invite_code:
            raise HubFixtureError("share_session_seed_failed")
        for identity in _IDENTITIES[1:]:
            joined = shares.join_session(
                session_id=session_id,
                user_id=identity,
                device_id=f"sfu-e2e-{engine}-{identity}",
                public_key_fingerprint="",
                invite_code=invite_code,
                tenant_id=_TENANT,
            )
            if not joined.ok:
                raise HubFixtureError("share_participant_seed_failed")
        epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if not isinstance(epoch, int) or epoch < 1:
            raise HubFixtureError("share_session_epoch_seed_failed")
        sessions[engine] = {"session_id": session_id, "membership_epoch": epoch}
    descriptor = {
        "schema": "ananta.semantic-sfu-hub-fixture.v1",
        "tenant_id": _TENANT,
        "identities": list(_IDENTITIES),
        "tokens": {identity: _user_token(identity) for identity in _IDENTITIES},
        "sessions": sessions,
    }
    _atomic_json(output, descriptor)
    return descriptor


def create_hub_app() -> Flask:
    """Build the narrow production Hub composition required by this gate."""

    from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
    from agent.config import settings
    from agent.database import init_db
    from agent.routes.semantic_media_contracts import semantic_media_contracts_bp
    from agent.routes.semantic_sfu_admission import semantic_sfu_admission_bp

    init_db()
    app = Flask("ananta-semantic-sfu-live-hub")
    app.secret_key = settings.secret_key
    app.config.update(
        TESTING=False,
        PROPAGATE_EXCEPTIONS=False,
        SEMANTIC_COMPUTE_FALLBACK_HEALTHY=True,
    )
    initialize_semantic_media_services(app)
    app.register_blueprint(semantic_sfu_admission_bp)
    app.register_blueprint(semantic_media_contracts_bp)

    @app.get("/healthz")
    def healthz():
        flags = dict(app.extensions.get("semantic_media_feature_flags") or {})
        ready = bool(
            flags.get("semantic_media_sfu")
            and flags.get("semantic_visual_capture")
            and app.config.get("SEMANTIC_COMPUTE_SECURITY_CONFIRMED") is True
        )
        return jsonify(
            {
                "ok": ready,
                "authority": "productive_hub_composition",
                "persistent_repository": True,
            }
        ), (200 if ready else 503)

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed")
    seed.add_argument("--output", type=Path, required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.command == "seed":
        try:
            descriptor = seed_fixture(args.output)
        except (HubFixtureError, OSError) as exc:
            print(json.dumps({"ok": False, "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "session_count": len(descriptor["sessions"]),
                    "identity_count": len(descriptor["identities"]),
                },
                sort_keys=True,
            )
        )
        return 0
    if not 1 <= args.port <= 65_535:
        return 2
    create_hub_app().run(
        host="127.0.0.1",
        port=args.port,
        threaded=True,
        debug=False,
        use_reloader=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

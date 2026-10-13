"""Bounded test transport; no orchestration or runtime decisions here."""

from __future__ import annotations

import json
import os
import secrets
import urllib.request

from flask import abort, request

WORKER_ID = "bpmn-acceptance-worker"
WORKER_URL = os.environ.get("BPMN_WORKER_URL", "http://worker:8080")
HUB_URL = os.environ.get("BPMN_HUB_URL", "http://hub:8080")
OWNED_POSTGRES_URL = (
    'postgresql+psycopg2://ananta_fixture:synthetic-postgres-fixture-password@postgres:5432/ananta_bpmn_fixture'
)


def require_isolation(role: str) -> None:
    database = os.getenv('DATABASE_URL')
    database_allowed = database == 'sqlite:////tmp/runtime.sqlite' or (
        role == 'hub' and os.getenv('BPMN_POSTGRES_ENABLED') == '1'
        and os.getenv('BPMN_AUTOPILOT_ENABLED') == '1' and database == OWNED_POSTGRES_URL
    )
    if (
        os.getenv("BPMN_CONTAINER_ACCEPTANCE") != "1"
        or os.getenv("ROLE") != role
        or not database_allowed
        or not os.path.isfile("/.dockerenv")
    ):
        raise RuntimeError("bpmn_acceptance_requires_isolated_container")


def require_token(name: str) -> None:
    if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + os.environ[name]):
        abort(403)


def request_json(url: str, *, token: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    raw = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=raw, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                              **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read(1_048_577)
    if len(body) > 1_048_576:
        raise RuntimeError("bpmn_acceptance_response_too_large")
    return json.loads(body)

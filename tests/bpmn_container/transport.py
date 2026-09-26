"""Bounded test transport; no orchestration or runtime decisions here."""

from __future__ import annotations

import json
import os
import secrets
import urllib.request

from flask import abort, request

WORKER_ID = "bpmn-acceptance-worker"
WORKER_URL = "http://worker:8080"
HUB_URL = "http://hub:8080"


def require_isolation(role: str) -> None:
    if (
        os.getenv("BPMN_CONTAINER_ACCEPTANCE") != "1"
        or os.getenv("ROLE") != role
        or os.getenv("DATABASE_URL") != "sqlite:////tmp/runtime.sqlite"
        or not os.path.isfile("/.dockerenv")
    ):
        raise RuntimeError("bpmn_acceptance_requires_isolated_container")


def require_token(name: str) -> None:
    if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + os.environ[name]):
        abort(403)


def request_json(url: str, *, token: str, payload: dict | None = None) -> dict:
    raw = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=raw, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read(1_048_577)
    if len(body) > 1_048_576:
        raise RuntimeError("bpmn_acceptance_response_too_large")
    return json.loads(body)

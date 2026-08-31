"""Bounded authenticated HTTP surface for one delegated Dendritic assignment."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any, Protocol

from flask import Flask, jsonify, request

from ananta_contracts.dendritic_memory_worker import DENDRITIC_WORKER_BASE_PATH


class DendriticRunnerPort(Protocol):
    def run(self, *, job: Mapping[str, Any], records=(), packs=(), cancelled=lambda: False) -> dict[str, Any]: ...


def create_app(
    *,
    runner: DendriticRunnerPort,
    bearer_token: str,
    capability: Mapping[str, Any],
    max_request_bytes: int = 4 * 1024 * 1024,
) -> Flask:
    token = str(bearer_token or "").strip()
    if len(token) < 24 or any(character.isspace() for character in token):
        raise ValueError("dendritic_worker_token_invalid")
    if not 1024 <= max_request_bytes <= 16 * 1024**2:
        raise ValueError("dendritic_worker_request_limit_invalid")
    app = Flask("ananta-dendritic-memory-worker")
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes

    def authorized() -> bool:
        supplied = request.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {token}")

    @app.get(f"{DENDRITIC_WORKER_BASE_PATH}/capabilities")
    def capabilities():
        if not authorized():
            return jsonify({"error": {"code": "dendritic_worker_unauthorized"}}), 401
        return jsonify(dict(capability))

    @app.post(f"{DENDRITIC_WORKER_BASE_PATH}/jobs")
    def execute():
        if not authorized():
            return jsonify({"error": {"code": "dendritic_worker_unauthorized"}}), 401
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"assignment", "records", "packs"}:
            return jsonify({"error": {"code": "dendritic_worker_payload_invalid"}}), 422
        if (
            not isinstance(body["assignment"], dict)
            or not isinstance(body["records"], list)
            or not isinstance(body["packs"], list)
            or len(body["records"]) > 100_000
            or len(body["packs"]) > 16
        ):
            return jsonify({"error": {"code": "dendritic_worker_payload_invalid"}}), 422
        return jsonify(
            runner.run(job=body["assignment"], records=body["records"], packs=body["packs"])
        )

    return app


__all__ = ["DendriticRunnerPort", "create_app"]

from __future__ import annotations

import time

import jwt

from agent.config import settings


def _worker_jwt() -> str:
    payload = {"username": "worker-user", "role": "worker", "exp": time.time() + 3600}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def test_endpoint_role_matrix_for_protected_and_admin_routes(client, user_auth_header):
    cases = [
        ("anonymous", "/tasks", None, 401),
        ("anonymous", "/config", None, 401),
        ("user", "/tasks", user_auth_header, 200),
        ("user", "/config", user_auth_header, 403),
        ("worker", "/tasks", {"Authorization": f"Bearer {_worker_jwt()}"}, 200),
        ("worker", "/config", {"Authorization": f"Bearer {_worker_jwt()}"}, 403),
    ]

    for _role, path, headers, expected in cases:
        response = client.get(path, headers=headers) if path == "/tasks" else client.post(path, headers=headers, json={})
        assert response.status_code == expected


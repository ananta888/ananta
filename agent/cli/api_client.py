"""Thin HTTP client for CLI commands against the Ananta Hub."""
from __future__ import annotations

import os
import sys
from typing import Any

import requests

from agent.config import settings


def _base_url() -> str:
    configured = os.environ.get("ANANTA_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{settings.port}"


def _auth_token(base_url: str) -> str:
    username = (
        os.environ.get("ANANTA_USER")
        or os.environ.get("INITIAL_ADMIN_USER")
        or "admin"
    )
    password = (
        os.environ.get("ANANTA_PASSWORD")
        or os.environ.get("INITIAL_ADMIN_PASSWORD")
        or "admin"
    )
    try:
        resp = requests.post(
            f"{base_url}/login",
            json={"username": username, "password": password},
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"error: hub not reachable at {base_url} — {exc}", file=sys.stderr)
        print("hint: start the hub or set ANANTA_BASE_URL", file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 200:
        print(f"error: login failed ({resp.status_code}) — check ANANTA_USER/ANANTA_PASSWORD", file=sys.stderr)
        sys.exit(1)
    return (resp.json().get("data") or {}).get("access_token") or ""


class AnantaApiClient:
    def __init__(self) -> None:
        self._base = _base_url()
        self._token = _auth_token(self._base)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None, timeout: int = 30) -> dict:
        try:
            resp = requests.request(
                method=method,
                url=f"{self._base}{path}",
                headers=self._headers(),
                json=json,
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            print(f"error: request failed for {path} — {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, path: str, *, params: dict | None = None) -> dict:
        return self._call("GET", path, params=params)

    def post(self, path: str, *, json: Any = None) -> dict:
        return self._call("POST", path, json=json)

    def delete(self, path: str) -> dict:
        return self._call("DELETE", path)


_CLIENT: AnantaApiClient | None = None


def get_api_client() -> AnantaApiClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AnantaApiClient()
    return _CLIENT

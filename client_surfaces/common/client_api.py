from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from client_surfaces.common.degraded_state import is_retriable_state, map_status_to_degraded_state
from client_surfaces.common.types import ClientProfile, ClientResponse

TransportFn = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, str]]


class AnantaApiClient:
    def __init__(self, profile: ClientProfile, *, transport: TransportFn | None = None) -> None:
        self.profile = profile
        self._transport = transport or self._default_transport

    @staticmethod
    def _default_transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8", "replace")
                return status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return int(exc.code), raw
        except urllib.error.URLError as exc:
            raise ConnectionError(str(exc)) from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.profile.auth_token:
            headers["Authorization"] = f"Bearer {self.profile.auth_token}"
        return headers

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> ClientResponse:
        url = f"{self.profile.base_url.rstrip('/')}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            status_code, raw = self._transport(method, url, self._headers(), body, self.profile.timeout_seconds)
        except ConnectionError as exc:
            return ClientResponse(
                ok=False,
                status_code=None,
                state="backend_unreachable",
                data=None,
                error=str(exc),
                retriable=True,
            )
        parse_error = False
        parsed: Any = None
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parse_error = True

        state = map_status_to_degraded_state(status_code, parse_error=parse_error)
        ok = state == "healthy"
        return ClientResponse(
            ok=ok,
            status_code=status_code,
            state=state,
            data=parsed,
            error=None if ok else f"request_failed:{state}",
            retriable=is_retriable_state(state),
        )

    def get_health(self) -> ClientResponse:
        return self._request_json("GET", "/health")

    def get_capabilities(self) -> ClientResponse:
        return self._request_json("GET", "/capabilities")

    def list_tasks(self) -> ClientResponse:
        return self._request_json("GET", "/tasks")

    def list_artifacts(self) -> ClientResponse:
        return self._request_json("GET", "/artifacts")

    def list_approvals(self) -> ClientResponse:
        return self._request_json("GET", "/approvals")

    def list_repairs(self) -> ClientResponse:
        return self._request_json("GET", "/repairs")

    def submit_goal(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/goals", payload=payload)

    def analyze_context(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/analyze", payload={"context": context_payload})

    def review_context(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/review", payload={"context": context_payload})

    def patch_plan(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/patch-plan", payload={"context": context_payload})

    def create_project_new(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/projects/new", payload=payload)

    def create_project_evolve(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/projects/evolve", payload=payload)

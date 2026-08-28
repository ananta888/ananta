"""Hub-owned, bounded adapter for the official experimental Jules API."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Event
from typing import Any, Mapping, Protocol

_SESSION_NAME = re.compile(r"^sessions/[A-Za-z0-9_-]{1,200}$")
_SOURCE_NAME = re.compile(r"^sources/[A-Za-z0-9._/-]{1,300}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class JulesApiError(RuntimeError):
    pass


class JulesHttpPort(Protocol):
    def request(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...


class UrllibJulesHttp:
    """Fixed-host transport; the API key never appears in URLs or diagnostics."""

    def __init__(self, api_key: str, *, timeout_seconds: float = 15.0) -> None:
        if not api_key.strip():
            raise ValueError("jules_api_key_required")
        self._api_key = api_key
        self._timeout = min(60.0, max(1.0, timeout_seconds))

    def request(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if not path.startswith("/v1alpha/") or ".." in path or any(ord(value) < 32 for value in path):
            raise JulesApiError("jules_api_path_invalid")
        body = None if payload is None else json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            "https://jules.googleapis.com" + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": self._api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed HTTPS host
                raw = response.read(2_000_001)
        except (OSError, urllib.error.HTTPError) as exc:
            raise JulesApiError("jules_api_request_failed") from exc
        if len(raw) > 2_000_000:
            raise JulesApiError("jules_api_response_too_large")
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JulesApiError("jules_api_response_invalid") from exc
        if not isinstance(value, Mapping):
            raise JulesApiError("jules_api_response_invalid")
        return value


@dataclass(frozen=True, slots=True)
class JulesRunRequest:
    prompt: str
    source: str
    starting_branch: str
    timeout_seconds: float = 1800.0
    allow_plan_auto_approval: bool = True
    auto_create_pull_request: bool = False

    def __post_init__(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 100_000:
            raise ValueError("jules_prompt_invalid")
        if _SOURCE_NAME.fullmatch(self.source) is None:
            raise ValueError("jules_source_invalid")
        if _BRANCH.fullmatch(self.starting_branch) is None or ".." in self.starting_branch:
            raise ValueError("jules_branch_invalid")
        if not 1 <= self.timeout_seconds <= 86_400:
            raise ValueError("jules_timeout_invalid")


@dataclass(frozen=True, slots=True)
class JulesRunResult:
    status: str
    reason_code: str
    session_name: str | None
    state: str | None
    outputs: tuple[Mapping[str, Any], ...] = ()


class JulesCloudAgent:
    """Execute one cloud session under an explicit Hub auto-approval policy."""

    def __init__(self, http: JulesHttpPort, *, poll_seconds: float = 2.0) -> None:
        self._http = http
        self._poll = min(30.0, max(0.05, poll_seconds))

    def run(self, request: JulesRunRequest, *, cancellation: Event | None = None) -> JulesRunResult:
        created = self._http.request(
            "POST",
            "/v1alpha/sessions",
            payload={
                "prompt": request.prompt,
                "sourceContext": {
                    "source": request.source,
                    "githubRepoContext": {"startingBranch": request.starting_branch},
                },
                "requirePlanApproval": not request.allow_plan_auto_approval,
                "automationMode": (
                    "AUTO_CREATE_PR" if request.auto_create_pull_request else "AUTOMATION_MODE_UNSPECIFIED"
                ),
            },
        )
        session_name = str(created.get("name") or "")
        if _SESSION_NAME.fullmatch(session_name) is None:
            raise JulesApiError("jules_session_name_invalid")
        deadline = time.monotonic() + request.timeout_seconds
        approval_sent = False
        while time.monotonic() < deadline:
            if cancellation is not None and cancellation.is_set():
                return JulesRunResult("cancelled", "cancelled", session_name, None)
            session = self._http.request("GET", f"/v1alpha/{session_name}")
            state = str(session.get("state") or "STATE_UNSPECIFIED")
            if state == "COMPLETED":
                outputs = session.get("outputs")
                return JulesRunResult(
                    "completed",
                    "completed",
                    session_name,
                    state,
                    (
                        tuple(value for value in outputs if isinstance(value, Mapping))
                        if isinstance(outputs, list)
                        else ()
                    ),
                )
            if state == "FAILED":
                return JulesRunResult("failed", "cloud_agent_failed", session_name, state)
            if state in {"AWAITING_USER_FEEDBACK", "PAUSED"}:
                return JulesRunResult("blocked", "cloud_agent_input_required", session_name, state)
            if state == "AWAITING_PLAN_APPROVAL":
                if not request.allow_plan_auto_approval:
                    return JulesRunResult("blocked", "plan_auto_approval_not_authorized", session_name, state)
                if not approval_sent:
                    self._http.request("POST", f"/v1alpha/{session_name}:approvePlan", payload={})
                    approval_sent = True
            time.sleep(self._poll)
        return JulesRunResult("failed", "cloud_agent_timeout", session_name, None)


__all__ = [
    "JulesApiError",
    "JulesCloudAgent",
    "JulesHttpPort",
    "JulesRunRequest",
    "JulesRunResult",
    "UrllibJulesHttp",
]

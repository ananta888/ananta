from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings


class UrlOpenProtocol(Protocol):
    def open(self, req: request.Request, timeout: float | None = None): ...


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str


DEFAULT_ROUTE_MAP: dict[str, RouteSpec] = {
    "health": RouteSpec("GET", "/api/client-surfaces/freecad/health"),
    "capabilities": RouteSpec("GET", "/api/client-surfaces/freecad/capabilities"),
    "submit_goal": RouteSpec("POST", "/api/client-surfaces/freecad/goals"),
    "approval_decision": RouteSpec("POST", "/api/client-surfaces/freecad/approvals/decision"),
    "export_plan": RouteSpec("POST", "/api/client-surfaces/freecad/export-plans"),
    "macro_plan": RouteSpec("POST", "/api/client-surfaces/freecad/macro-plans"),
    "macro_execute": RouteSpec("POST", "/api/client-surfaces/freecad/macro-executions"),
}


class DefaultUrlOpener:
    def open(self, req: request.Request, timeout: float | None = None):
        return request.urlopen(req, timeout=timeout)


class HttpJsonTransport:
    def __init__(
        self,
        settings: FreecadWorkbenchSettings,
        *,
        route_map: dict[str, RouteSpec] | None = None,
        opener: UrlOpenProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.route_map = dict(route_map or DEFAULT_ROUTE_MAP)
        self.opener = opener or DefaultUrlOpener()

    def send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        route = self.route_map.get(action)
        if route is None:
            return {"status": "degraded", "reason": f"unknown_action:{action}"}
        req = self._build_request(route, payload)
        try:
            with self.opener.open(req, timeout=float(self.settings.request_timeout_seconds)) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                body = response.read().decode("utf-8") or "{}"
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    normalized = self._normalize_enveloped_response(parsed, status_code=status_code)
                    normalized.setdefault("http_status", status_code)
                    return normalized
                return {"status": "degraded", "reason": "non_object_response", "http_status": status_code}
        except error.HTTPError as exc:
            return self._http_error_payload(exc)
        except error.URLError as exc:
            return {"status": "degraded", "reason": f"transport_error:{exc.reason}"}
        except json.JSONDecodeError:
            return {"status": "degraded", "reason": "invalid_json_response"}

    def _build_request(self, route: RouteSpec, payload: dict[str, Any]) -> request.Request:
        url = f"{self.settings.endpoint}{route.path}"
        headers = {
            "Accept": "application/json",
            "X-Ananta-Profile": self.settings.profile,
        }
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        data: bytes | None = None
        if route.method != "GET":
            data = json.dumps(dict(payload or {})).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return request.Request(url, data=data, headers=headers, method=route.method)

    @staticmethod
    def _http_error_payload(exc: error.HTTPError) -> dict[str, Any]:
        if exc.code in {401, 403}:
            status = "unauthorized"
        elif exc.code == 409:
            status = "approval_required"
        elif exc.code == 429:
            status = "degraded"
        else:
            status = "degraded"
        try:
            body = exc.read().decode("utf-8") or "{}"
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                parsed.setdefault("status", status)
                parsed.setdefault("http_status", exc.code)
                return parsed
        except Exception:
            pass
        return {"status": status, "http_status": exc.code, "reason": exc.reason}

    @staticmethod
    def _normalize_enveloped_response(payload: dict[str, Any], *, status_code: int) -> dict[str, Any]:
        envelope_status = str(payload.get("status") or "").strip().lower()
        data = payload.get("data")
        if envelope_status == "success" and isinstance(data, dict):
            normalized = dict(data)
            normalized.setdefault("status", str(data.get("status") or "accepted"))
            normalized.setdefault("envelope_status", envelope_status)
            return normalized
        if envelope_status == "error":
            normalized = dict(data) if isinstance(data, dict) else {}
            normalized.setdefault("status", "degraded")
            normalized.setdefault("reason", str(payload.get("message") or normalized.get("reason") or "request_failed"))
            normalized.setdefault("envelope_status", envelope_status)
            return normalized
        if isinstance(data, dict):
            normalized = dict(data)
            normalized.setdefault("status", envelope_status or str(data.get("status") or "degraded"))
            return normalized
        return dict(payload)

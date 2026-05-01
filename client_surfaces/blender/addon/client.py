from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from client_surfaces.blender.addon.settings import BlenderAddonSettings

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class UrlOpenProtocol(Protocol):
    def open(self, req: request.Request, timeout: float | None = None): ...


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str


DEFAULT_ROUTE_MAP: dict[str, RouteSpec] = {
    "health": RouteSpec("GET", "/api/client-surfaces/blender/health"),
    "capabilities": RouteSpec("GET", "/api/client-surfaces/blender/capabilities"),
    "submit_goal": RouteSpec("POST", "/api/client-surfaces/blender/goals"),
    "list_tasks": RouteSpec("GET", "/api/client-surfaces/blender/tasks"),
    "get_task": RouteSpec("GET", "/api/client-surfaces/blender/tasks/{task_id}"),
    "list_artifacts": RouteSpec("GET", "/api/client-surfaces/blender/artifacts"),
    "get_artifact": RouteSpec("GET", "/api/client-surfaces/blender/artifacts/{artifact_id}"),
    "list_approvals": RouteSpec("GET", "/api/client-surfaces/blender/approvals"),
    "approval_decision": RouteSpec("POST", "/api/client-surfaces/blender/approvals/decision"),
    "export_plan": RouteSpec("POST", "/api/client-surfaces/blender/export-plans"),
    "render_plan": RouteSpec("POST", "/api/client-surfaces/blender/render-plans"),
    "mutation_plan": RouteSpec("POST", "/api/client-surfaces/blender/mutation-plans"),
    "execute_action": RouteSpec("POST", "/api/client-surfaces/blender/executions"),
    "events": RouteSpec("GET", "/api/client-surfaces/blender/events"),
}


class DefaultUrlOpener:
    def open(self, req: request.Request, timeout: float | None = None):
        return request.urlopen(req, timeout=timeout)


class HttpJsonTransport:
    def __init__(
        self,
        settings: BlenderAddonSettings,
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
        path = route.path.format(**{key: str(value) for key, value in payload.items()})
        url = f"{self.settings.endpoint}{path}"
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
        elif exc.code == 422:
            status = "policy_limited"
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
        if isinstance(data, list):
            return {"status": envelope_status or "ok", "items": data}
        if isinstance(data, dict):
            normalized = dict(data)
            normalized.setdefault("status", envelope_status or str(data.get("status") or "degraded"))
            return normalized
        return dict(payload)


class BlenderHubClient:
    def __init__(
        self,
        endpoint: str | BlenderAddonSettings,
        token: str | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        if isinstance(endpoint, BlenderAddonSettings):
            self.settings = endpoint
        else:
            self.settings = BlenderAddonSettings.from_mapping({"endpoint": endpoint, "token": token})
        self.endpoint = self.settings.endpoint
        self.token = self.settings.token or ""
        self._transport = transport or self._build_default_transport()

    @classmethod
    def with_http_transport(
        cls,
        settings: BlenderAddonSettings,
        *,
        route_map: dict[str, RouteSpec] | None = None,
        opener: Any | None = None,
    ) -> "BlenderHubClient":
        transport = HttpJsonTransport(settings, route_map=route_map, opener=opener)
        return cls(settings, transport=transport.send)

    def configuration_state(self) -> dict[str, Any]:
        problems = self.settings.validate()
        return {
            "status": "ready" if not problems else "degraded",
            "problems": problems,
            "settings": self.settings.to_redacted_dict(),
        }

    def health(self) -> dict[str, Any]:
        return self._transport("health", {"endpoint": self.settings.endpoint, "profile": self.settings.profile})

    def capabilities(self) -> dict[str, Any]:
        return self._transport("capabilities", {"profile": self.settings.profile})

    def submit_goal(self, *, goal: str, context: dict[str, Any], capability_id: str) -> dict[str, Any]:
        return self._transport(
            "submit_goal",
            {"goal": str(goal or "").strip(), "context": dict(context or {}), "capability_id": str(capability_id or "").strip()},
        )

    def list_tasks(self) -> dict[str, Any]:
        return self._transport("list_tasks", {})

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._transport("get_task", {"task_id": str(task_id or "").strip()})

    def list_artifacts(self) -> dict[str, Any]:
        return self._transport("list_artifacts", {})

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._transport("get_artifact", {"artifact_id": str(artifact_id or "").strip()})

    def list_approvals(self) -> dict[str, Any]:
        return self._transport("list_approvals", {})

    def submit_approval_decision(self, *, approval_id: str, decision: str) -> dict[str, Any]:
        return self._transport("approval_decision", {"approval_id": str(approval_id or "").strip(), "decision": str(decision or "").strip()})

    def request_export_plan(self, *, fmt: str, target_path: str, selection_only: bool = False) -> dict[str, Any]:
        return self._transport("export_plan", {"format": fmt, "target_path": target_path, "selection_only": bool(selection_only)})

    def request_render_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._transport("render_plan", dict(payload or {}))

    def request_mutation_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._transport("mutation_plan", dict(payload or {}))

    def execute_action(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._transport("execute_action", dict(envelope or {}))

    def _build_default_transport(self) -> Transport:
        if self.settings.transport_mode == "http":
            return HttpJsonTransport(self.settings).send
        return self._stub_transport

    def _stub_transport(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "health":
            status = "ok" if self.settings.endpoint else "degraded"
            state = "connected" if status == "ok" else "degraded"
            return {"status": status, "state": state, "surface": "blender", "endpoint": self.settings.endpoint}
        if action == "capabilities":
            return {
                "status": "ok" if self.settings.endpoint else "degraded",
                "capabilities": [
                    {"capability_id": "blender.scene.read", "approval_required": False},
                    {"capability_id": "blender.export.plan", "approval_required": False},
                    {"capability_id": "blender.script.execute", "approval_required": True},
                ],
            }
        if action == "submit_goal":
            goal = str(payload.get("goal") or "").strip()
            if not goal:
                return {"status": "degraded", "reason": "goal_missing"}
            return {"status": "accepted", "goal": goal, "capability_id": payload.get("capability_id"), "task_id": "blend-task-1"}
        if action == "approval_decision":
            approval_id = str(payload.get("approval_id") or "").strip()
            decision = str(payload.get("decision") or "").strip().lower()
            if not approval_id or decision not in {"approve", "reject"}:
                return {"status": "degraded", "reason": "invalid_approval_decision"}
            return {"status": "accepted", "approval_id": approval_id, "decision": decision}
        if action in {"list_tasks", "list_artifacts", "list_approvals"}:
            return {"status": "ok", "items": []}
        if action == "export_plan":
            return {"status": "accepted", "plan": {"format": str(payload.get("format") or "GLTF").upper(), "execution_mode": "plan_only"}}
        if action in {"render_plan", "mutation_plan"}:
            return {"status": "accepted", "plan": dict(payload or {}), "execution_mode": "plan_only"}
        if action == "execute_action":
            if not payload.get("approval_id"):
                return {"status": "blocked", "reason": "approval_required"}
            return {"status": "accepted", "execution": dict(payload)}
        return {"status": "degraded", "reason": f"unsupported_action:{action}"}

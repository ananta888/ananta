"""Offline ``--fixture`` transport of the TUI runtime.

:func:`build_fixture_transport` returns the transport callable
``transport(method, url, headers, body, timeout) -> (status, body)``. Requests
are answered by the first matching entry of an ordered route table (path
suffix plus optional HTTP method), then by the static endpoint payloads, and
finally with 404. The payloads live in ``fixture_payloads``; this module only
routes (SRP), and a new fixture endpoint is one more route row (OCP).
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from client_surfaces.tui_runtime.ananta_tui.fixture_payloads import FixturePayloads, build_fixture_payloads

Response = tuple[int, str]


@dataclass(frozen=True)
class FixtureRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: bytes | None

    def json_body(self) -> Any:
        return json.loads((self.body or b"{}").decode("utf-8", "replace"))


Responder = Callable[["FixtureTransport", FixtureRequest], Response]


@dataclass(frozen=True)
class FixtureRoute:
    """Answer requests whose path ends with ``suffix`` (and whose method is ``method``, if set)."""

    suffix: str
    method: str | None
    respond: Responder

    def matches(self, request: FixtureRequest) -> bool:
        return request.path.endswith(self.suffix) and (self.method is None or request.method == self.method)


def _merge_dict(target: dict, patch: dict) -> dict:
    merged = deepcopy(target)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _reply(status: int, payload: Any) -> Responder:
    """A fixed JSON response."""
    return lambda transport, request: (status, json.dumps(payload))


def _updated(action: str, **extra: Any) -> Responder:
    return _reply(200, {"updated": True, "action": action, **extra})


def _payload(field: str) -> Responder:
    """The payload named ``field`` of this transport's :class:`FixturePayloads`."""
    return lambda transport, request: (200, json.dumps(getattr(transport.payloads, field)))


def _create_goal(transport: "FixtureTransport", request: FixtureRequest) -> Response:
    payload = request.json_body()
    if isinstance(payload, dict) and payload.get("goal_text"):
        return 201, json.dumps({"goal_id": "G-3", "task_id": "T-3", "accepted": True, "mode": payload.get("mode")})
    return 201, json.dumps({"goal_id": "G-1", "task_id": "T-1", "accepted": True})


def _review_task(transport: "FixtureTransport", request: FixtureRequest) -> Response:
    action = str(request.json_body().get("action") or "").strip().lower()
    if action not in {"approve", "reject"}:
        return 400, json.dumps({"error": "invalid_review_action"})
    return 200, json.dumps({"updated": True, "action": action})


def _rag_preview(transport: "FixtureTransport", request: FixtureRequest) -> Response:
    limit = int((request.query.get("limit") or ["5"])[0])
    payload = deepcopy(transport.payloads.artifact_rag_preview)
    payload["items"] = payload["items"][: max(1, limit)]
    return 200, json.dumps(payload)


def _patch_config(transport: "FixtureTransport", request: FixtureRequest) -> Response:
    patch = request.json_body()
    if isinstance(patch, dict):
        transport.config = _merge_dict(transport.config, patch)
    return 200, json.dumps({"updated": True, "config": transport.config})


def _read_config(transport: "FixtureTransport", request: FixtureRequest) -> Response:
    return 200, json.dumps(transport.config)


def _route(suffix: str, respond: Responder, method: str | None = None) -> FixtureRoute:
    return FixtureRoute(suffix=suffix, method=method, respond=respond)


def _post(suffix: str, respond: Responder) -> FixtureRoute:
    return _route(suffix, respond, "POST")


# First match wins; the order mirrors the former if-chain (e.g. GET-or-any
# ``/tasks/T-1`` precedes the PATCH route of the same suffix).
FIXTURE_ROUTES: tuple[FixtureRoute, ...] = (
    _post("/goals", _create_goal),
    _route("/goals/G-1/detail", _payload("goal_detail")),
    _route("/goals/G-1/plan", _payload("goal_plan")),
    _route("/goals/G-1/governance-summary", _payload("goal_governance")),
    _route("/tasks/T-1", _payload("task_detail")),
    _route("/tasks/T-2", _payload("task_detail_stale")),
    _route("/tasks/T-3", _payload("task_detail_denied")),
    _route("/tasks/T-1/logs", _payload("task_logs")),
    _route("/tasks/T-2/logs", _reply(200, {"items": [{"ts": "2026-04-24T22:01:00Z", "line": "waiting for review"}]})),
    _route(
        "/tasks/T-3/logs", _reply(200, {"items": [{"ts": "2026-04-24T22:02:00Z", "line": "review gated by policy"}]})
    ),
    _post("/tasks/T-1/assign", _updated("assign")),
    _post("/tasks/T-1/review", _review_task),
    _post("/tasks/T-2/review", _reply(409, {"error": "stale_proposal"})),
    _post("/tasks/T-3/review", _reply(403, {"error": "policy_denied"})),
    _post("/tasks/T-1/step/propose", _updated("propose")),
    _post("/tasks/T-1/step/execute", _updated("execute")),
    _route("/tasks/T-1", _updated("patch"), "PATCH"),
    _post("/tasks/archived/TA-1/restore", _updated("restore")),
    _post("/tasks/archived/cleanup", _updated("cleanup", affected=1)),
    _route("/tasks/archived/TA-1", _updated("delete_archived"), "DELETE"),
    _route("/artifacts/A-1", _payload("artifact_detail")),
    _post("/artifacts/A-1/extract", _updated("extract")),
    _post("/artifacts/A-1/rag-index", _updated("rag-index")),
    _route("/artifacts/A-1/rag-status", _payload("artifact_rag_status")),
    _route("/artifacts/A-1/rag-preview", _rag_preview),
    _route("/knowledge/collections/KC-1", _payload("knowledge_collection_detail")),
    _post("/knowledge/collections/KC-1/index", _updated("index_collection")),
    _post("/knowledge/collections/KC-1/search", _payload("knowledge_search")),
    _route("/teams/blueprints/BP-1", _payload("blueprint_detail")),
    _post("/teams/team-core/activate", _updated("activate_team")),
    _post("/instruction-profiles/IP-1/select", _updated("select_profile")),
    _post("/instruction-overlays/IO-1/select", _updated("select_overlay")),
    _post("/instruction-overlays/IO-1/attach", _updated("attach_overlay")),
    _post("/instruction-overlays/IO-1/detach", _updated("detach_overlay")),
    _post("/goals/G-1/instruction-selection", _updated("set_goal_instruction_selection")),
    _post("/tasks/T-1/instruction-selection", _updated("set_task_instruction_selection")),
    _post("/templates/validate", _reply(200, {"valid": True, "errors": []})),
    _post("/templates/preview", _reply(200, {"rendered": "Preview output text"})),
    _post(
        "/templates/validation-diagnostics",
        _reply(200, {"diagnostics": [{"severity": "info", "message": "all good"}]}),
    ),
    _post("/config", _patch_config),
    _post("/tasks/autopilot/start", _reply(200, {"updated": True, "running": True})),
    _post("/tasks/autopilot/stop", _reply(200, {"updated": True, "running": False})),
    _post("/tasks/autopilot/tick", _reply(200, {"updated": True, "tick": "ok"})),
    _post("/tasks/auto-planner/configure", _updated("configure_auto_planner")),
    _post("/triggers/configure", _updated("configure_triggers")),
    _post(
        "/api/system/audit/analyze",
        _reply(200, {"summary": {"total": 2, "high_risk": 1}, "top_patterns": ["approval", "automation"]}),
    ),
    _route("/config", _read_config),
)


class FixtureTransport:
    """Stateful fixture transport; only the config can be changed (via ``POST /config``)."""

    def __init__(self, payloads: FixturePayloads, routes: tuple[FixtureRoute, ...] = FIXTURE_ROUTES) -> None:
        self.payloads = payloads
        self.config = payloads.config
        self._routes = routes

    def __call__(
        self,
        method: str,
        url: str,
        _headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> Response:
        parsed_url = urlsplit(url)
        request = FixtureRequest(method=method, path=parsed_url.path, query=parse_qs(parsed_url.query), body=body)
        for route in self._routes:
            if route.matches(request):
                return route.respond(self, request)
        for endpoint, payload in self.payloads.static_endpoints.items():
            if request.path.endswith(endpoint):
                return 200, json.dumps(payload)
        return 404, json.dumps({"error": "not_found"})


def build_fixture_transport() -> FixtureTransport:
    return FixtureTransport(build_fixture_payloads())

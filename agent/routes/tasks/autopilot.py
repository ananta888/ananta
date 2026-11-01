import logging
from typing import Any

from flask import Blueprint, has_app_context, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.service_registry import get_core_services

autopilot_bp = Blueprint("tasks_autopilot", __name__)


# --- CLN-048: engine moved to agent.services.autopilot.loop_manager
from agent.services.autopilot.loop_manager import (  # noqa: E402,F401
    AUTOPILOT_STATE_KEY,
    AutonomousLoopManager,
    _background_threads_disabled,
    _task_dependencies,
    autonomous_loop,
)


def _append_trace_event(task_id: str, event_type: str, **data: Any) -> None:
    _services().autopilot_support_service.append_trace_event(task_id, event_type, **data)


def _append_circuit_event_for_worker_tasks(worker_url: str, event_type: str, **data: Any) -> int:
    return _services().autopilot_support_service.append_circuit_event_for_worker_tasks(worker_url, event_type, **data)


def _services():
    if has_app_context():
        return get_core_services()
    if autonomous_loop._app is not None:
        with autonomous_loop._app.app_context():
            return get_core_services()
    raise RuntimeError("core_services_unavailable")


def init_autopilot(app=None):
    try:
        if app is not None:
            autonomous_loop.bind_app(app)
        autonomous_loop.restore()
    except Exception as e:
        logging.warning(f"Autonomous loop restore failed: {e}")


def request_autopilot_wake(event_type: str, **details: Any) -> None:
    """Best-effort event wakeup for the hub autopilot loop."""
    try:
        autonomous_loop.wake()
        if details.get("task_id"):
            _append_trace_event(str(details.get("task_id")), f"autopilot_wake_{event_type}", **details)
    except Exception:
        logging.getLogger(__name__).debug("request_autopilot_wake: ignored Exception", exc_info=True)


@autopilot_bp.route("/tasks/autopilot/start", methods=["POST"])
@check_auth
@admin_required
def autopilot_start():
    if not _services().autopilot_runtime_service.is_hub_allowed():
        return api_response(status="error", message="hub_only", code=400)
    data = request.get_json(silent=True) or {}
    return api_response(
        data=_services().autopilot_runtime_service.start(
            interval_seconds=data.get("interval_seconds"),
            max_concurrency=data.get("max_concurrency"),
            goal=data.get("goal"),
            team_id=data.get("team_id"),
            budget_label=data.get("budget_label"),
            security_level=data.get("security_level"),
        )
    )


@autopilot_bp.route("/tasks/autopilot/stop", methods=["POST"])
@check_auth
@admin_required
def autopilot_stop():
    return api_response(data=_services().autopilot_runtime_service.stop())


@autopilot_bp.route("/tasks/autopilot/status", methods=["GET"])
@check_auth
def autopilot_status():
    return api_response(data=_services().autopilot_runtime_service.status())


@autopilot_bp.route("/tasks/autopilot/tick", methods=["POST"])
@check_auth
@admin_required
def autopilot_tick():
    if not _services().autopilot_runtime_service.is_hub_allowed():
        return api_response(status="error", message="hub_only", code=400)
    data = request.get_json(silent=True) or {}
    requested_team_id = str(data.get("team_id") or "").strip() or None
    return api_response(data=_services().autopilot_runtime_service.tick(requested_team_id=requested_team_id))


@autopilot_bp.route("/tasks/autopilot/circuits", methods=["GET"])
@check_auth
def autopilot_circuits_status():
    return api_response(data=_services().autopilot_runtime_service.circuit_status())


@autopilot_bp.route("/tasks/autopilot/circuits/reset", methods=["POST"])
@check_auth
@admin_required
def autopilot_circuits_reset():
    data = request.get_json(silent=True) or {}
    worker_url = str(data.get("worker_url") or "").strip() or None
    before = _services().autopilot_runtime_service.circuit_status()
    result = _services().autopilot_runtime_service.reset_circuits(worker_url=worker_url)
    if worker_url:
        affected = _append_circuit_event_for_worker_tasks(
            worker_url, "autopilot_worker_circuit_reset", action="manual_reset"
        )
        log_audit(
            "autopilot_worker_circuit_reset", {"worker_url": worker_url, "affected_tasks": affected, "mode": "single"}
        )
    else:
        affected_total = 0
        for item in before.get("open_workers", []):
            wurl = item.get("worker_url")
            if not wurl:
                continue
            affected_total += _append_circuit_event_for_worker_tasks(
                wurl, "autopilot_worker_circuit_reset", action="manual_reset"
            )
        log_audit(
            "autopilot_worker_circuit_reset", {"worker_url": None, "affected_tasks": affected_total, "mode": "all"}
        )
    return api_response(data={**result, "circuit_breakers": _services().autopilot_runtime_service.circuit_status()})

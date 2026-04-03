import logging
import os
import threading
import time
from typing import Any

from flask import Blueprint, current_app, has_app_context, request

from agent.auth import admin_required, check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.routes.tasks.autopilot_guardrails import (
    check_guardrail_limits,
    resolve_guardrail_limits,
    resolve_resilience_config,
    resolve_security_policy,
)
from agent.routes.tasks.autopilot_tick_engine import execute_autopilot_tick
from agent.routes.tasks.orchestration_policy import compute_retry_delay_seconds
from agent.routes.tasks.utils import _forward_to_worker, _update_local_task_status
from agent.services.service_registry import get_core_services

autopilot_bp = Blueprint("tasks_autopilot", __name__)

AUTOPILOT_STATE_KEY = "autonomous_loop_state"


def _background_threads_disabled(app: Any | None = None) -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if has_app_context() and bool(getattr(current_app, "testing", False)):
        return True
    return bool(getattr(app, "testing", False))


def _append_trace_event(task_id: str, event_type: str, **data: Any) -> None:
    _services().autopilot_support_service.append_trace_event(task_id, event_type, **data)


def _append_circuit_event_for_worker_tasks(worker_url: str, event_type: str, **data: Any) -> int:
    return _services().autopilot_support_service.append_circuit_event_for_worker_tasks(worker_url, event_type, **data)


def _task_dependencies(task: Any) -> list[str]:
    deps = []
    for item in getattr(task, "depends_on", None) or []:
        dep = str(item).strip()
        if dep and dep != getattr(task, "id", "") and dep not in deps:
            deps.append(dep)
    parent = getattr(task, "parent_task_id", None)
    if parent and parent not in deps and parent != getattr(task, "id", ""):
        deps.append(parent)
    return deps


class AutonomousLoopManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.interval_seconds = 20
        self.max_concurrency = 2
        self.last_tick_at: float | None = None
        self.last_error: str | None = None
        self.tick_count = 0
        self.dispatched_count = 0
        self.completed_count = 0
        self.failed_count = 0
        self._worker_cursor = 0
        self.started_at: float | None = None
        self._worker_failure_streak: dict[str, int] = {}
        self._worker_circuit_open_until: dict[str, float] = {}
        self.goal: str = ""
        self.team_id: str = ""
        self.budget_label: str = ""
        self.security_level: str = "safe"
        self._app = None

    def bind_app(self, app):
        self._app = app

    def _app_config(self) -> dict[str, Any]:
        if has_app_context():
            return current_app.config
        if self._app is not None:
            return getattr(self._app, "config", {}) or {}
        return {}

    def _agent_config(self) -> dict[str, Any]:
        return (self._app_config().get("AGENT_CONFIG", {}) or {})

    def _persist_state(self, enabled: bool):
        state = {
            "enabled": bool(enabled),
            "interval_seconds": int(self.interval_seconds),
            "max_concurrency": int(self.max_concurrency),
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
            "tick_count": self.tick_count,
            "dispatched_count": self.dispatched_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "started_at": self.started_at,
            "goal": self.goal,
            "team_id": self.team_id,
            "budget_label": self.budget_label,
            "security_level": self.security_level,
        }
        _services().autopilot_support_service.persist_state(key=AUTOPILOT_STATE_KEY, state=state, app=self._app)

    def _security_policy(self) -> dict:
        return resolve_security_policy(agent_config=self._agent_config(), security_level=self.security_level)

    def restore(self):
        data = _services().autopilot_support_service.load_state(key=AUTOPILOT_STATE_KEY, app=self._app)
        if data is None:
            return
        self.interval_seconds = int(data.get("interval_seconds") or self.interval_seconds)
        self.max_concurrency = int(data.get("max_concurrency") or self.max_concurrency)
        self.last_tick_at = data.get("last_tick_at")
        self.last_error = data.get("last_error")
        self.tick_count = int(data.get("tick_count") or self.tick_count)
        self.dispatched_count = int(data.get("dispatched_count") or self.dispatched_count)
        self.completed_count = int(data.get("completed_count") or self.completed_count)
        self.failed_count = int(data.get("failed_count") or self.failed_count)
        self.started_at = data.get("started_at")
        self.goal = str(data.get("goal") or "")
        self.team_id = str(data.get("team_id") or "")
        self.budget_label = str(data.get("budget_label") or "")
        self.security_level = str(data.get("security_level") or "safe")
        if data.get("enabled") and settings.role == "hub":
            self.start(
                interval_seconds=self.interval_seconds,
                max_concurrency=self.max_concurrency,
                persist=False,
                background=not _background_threads_disabled(self._app),
            )

    def start(
        self,
        interval_seconds: int | None = None,
        max_concurrency: int | None = None,
        goal: str | None = None,
        team_id: str | None = None,
        budget_label: str | None = None,
        security_level: str | None = None,
        persist: bool = True,
        background: bool = True,
    ):
        background = bool(background) and not _background_threads_disabled(self._app)
        with self._lock:
            if interval_seconds is not None:
                self.interval_seconds = max(3, int(interval_seconds))
            if max_concurrency is not None:
                self.max_concurrency = max(1, int(max_concurrency))
            if goal is not None:
                self.goal = str(goal or "").strip()
            if team_id is not None:
                self.team_id = str(team_id or "").strip()
            if budget_label is not None:
                self.budget_label = str(budget_label or "").strip()
            if security_level is not None:
                val = str(security_level or "safe").strip().lower()
                self.security_level = val if val in {"safe", "balanced", "aggressive"} else "safe"
            if self.running:
                if persist:
                    self._persist_state(enabled=True)
                return
            self.running = True
            self.started_at = time.time()
            self._stop_event.clear()
            if background:
                self._thread = threading.Thread(target=self._run_loop, daemon=True, name="autonomous-scrum-loop")
                self._thread.start()
            if persist:
                self._persist_state(enabled=True)

    def stop(self, persist: bool = True):
        thread_to_join = None
        with self._lock:
            self.running = False
            self._stop_event.set()
            thread_to_join = self._thread
            if persist:
                self._persist_state(enabled=False)
        if thread_to_join and thread_to_join.is_alive() and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=2.0)
        with self._lock:
            if self._thread is thread_to_join:
                self._thread = None

    def _circuit_status_unlocked(self) -> dict:
        now = time.time()
        open_items = []
        for worker_url, open_until in sorted(self._worker_circuit_open_until.items(), key=lambda it: it[0]):
            if open_until <= now:
                continue
            open_items.append(
                {
                    "worker_url": worker_url,
                    "open_until": open_until,
                    "remaining_seconds": max(0.0, round(open_until - now, 3)),
                    "failure_streak": int(self._worker_failure_streak.get(worker_url, 0)),
                }
            )
        return {
            "open_count": len(open_items),
            "open_workers": open_items,
            "failure_streak": {k: int(v) for k, v in self._worker_failure_streak.items()},
        }

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "interval_seconds": self.interval_seconds,
                "max_concurrency": self.max_concurrency,
                "last_tick_at": self.last_tick_at,
                "last_error": self.last_error,
                "tick_count": self.tick_count,
                "dispatched_count": self.dispatched_count,
                "completed_count": self.completed_count,
                "failed_count": self.failed_count,
                "started_at": self.started_at,
                "goal": self.goal,
                "team_id": self.team_id,
                "budget_label": self.budget_label,
                "security_level": self.security_level,
                "effective_security_policy": self._security_policy(),
                "circuit_breakers": self._circuit_status_unlocked(),
            }

    def circuit_status(self) -> dict:
        with self._lock:
            return self._circuit_status_unlocked()

    def reset_circuits(self, worker_url: str | None = None) -> dict:
        with self._lock:
            if worker_url:
                existed = worker_url in self._worker_circuit_open_until or worker_url in self._worker_failure_streak
                self._worker_circuit_open_until.pop(worker_url, None)
                self._worker_failure_streak.pop(worker_url, None)
                return {"reset": 1 if existed else 0, "worker_url": worker_url}

            reset_count = len(self._worker_circuit_open_until) + len(self._worker_failure_streak)
            self._worker_circuit_open_until.clear()
            self._worker_failure_streak.clear()
            return {"reset": reset_count, "worker_url": None}

    def _guardrail_limits(self) -> dict:
        return resolve_guardrail_limits(agent_config=self._agent_config())

    def _check_guardrails(self) -> str | None:
        return check_guardrail_limits(
            limits=self._guardrail_limits(),
            started_at=self.started_at,
            tick_count=self.tick_count,
            dispatched_count=self.dispatched_count,
        )

    def _resilience_config(self) -> dict:
        return resolve_resilience_config(agent_config=self._agent_config())

    def _is_worker_circuit_open(self, worker_url: str) -> bool:
        until = self._worker_circuit_open_until.get(worker_url, 0.0)
        return until > time.time()

    def _record_worker_success(self, worker_url: str) -> None:
        self._worker_failure_streak[worker_url] = 0
        self._worker_circuit_open_until.pop(worker_url, None)

    def _record_worker_failure(
        self, worker_url: str, reason: str, task_id: str | None = None, endpoint: str | None = None
    ) -> None:
        cfg = self._resilience_config()
        streak = int(self._worker_failure_streak.get(worker_url, 0)) + 1
        self._worker_failure_streak[worker_url] = streak
        if streak >= cfg["circuit_breaker_threshold"]:
            open_until = time.time() + cfg["circuit_breaker_open_seconds"]
            self._worker_circuit_open_until[worker_url] = open_until
            self.last_error = f"worker_circuit_open:{worker_url}:{reason}"
            details = {
                "worker_url": worker_url,
                "reason": reason,
                "open_until": open_until,
                "failure_streak": streak,
                "endpoint": endpoint,
                "task_id": task_id,
            }
            log_audit("autopilot_worker_circuit_open", details)

    def _forward_with_retry(self, worker_url: str, endpoint: str, payload: dict, token: str | None = None) -> dict:
        cfg = self._resilience_config()
        last_exc: Exception | None = None
        for attempt in range(1, cfg["retry_attempts"] + 1):
            try:
                res = _forward_to_worker(worker_url, endpoint, payload, token=token)
                self._record_worker_success(worker_url)
                return unwrap_api_envelope(res)
            except Exception as e:
                last_exc = e
                self._record_worker_failure(
                    worker_url,
                    f"forward_failed:{endpoint}",
                    task_id=(payload or {}).get("task_id"),
                    endpoint=endpoint,
                )
                if attempt < cfg["retry_attempts"]:
                    delay = compute_retry_delay_seconds(
                        attempt,
                        cfg["retry_backoff_seconds"],
                        max_backoff_seconds=cfg["retry_max_backoff_seconds"],
                        jitter_factor=cfg["retry_jitter_factor"],
                    )
                    if payload.get("task_id"):
                        _append_trace_event(
                            payload["task_id"],
                            "autopilot_retry_scheduled",
                            worker_url=worker_url,
                            endpoint=endpoint,
                            retry_attempt=attempt,
                            retry_delay_seconds=delay,
                        )
                    time.sleep(delay)
        raise RuntimeError(f"worker_forward_failed:{worker_url}:{endpoint}:{last_exc}")

    def tick_once(self) -> dict:  # noqa: C901
        if not has_app_context() and self._app is not None:
            with self._app.app_context():
                return self.tick_once()
        return execute_autopilot_tick(
            loop=self,
            services=_services(),
            append_trace_event=_append_trace_event,
            task_dependencies=_task_dependencies,
            update_local_task_status=_update_local_task_status,
        )

    def _run_loop(self):
        app = self._app
        try:
            while not self._stop_event.is_set():
                try:
                    if app is not None:
                        with app.app_context():
                            self.tick_once()
                    else:
                        # Fallback for tests/misconfiguration; request-driven tick still works.
                        self.tick_once()
                except Exception as e:
                    if self._stop_event.is_set():
                        break
                    logging.exception(f"Autonomous loop tick failed: {e}")
                    self.last_error = str(e)
                    try:
                        if app is not None:
                            with app.app_context():
                                self._persist_state(enabled=self.running)
                        else:
                            self._persist_state(enabled=self.running)
                    except Exception:
                        if not self._stop_event.is_set():
                            logging.exception("Autonomous loop state persistence failed after tick error.")
                self._stop_event.wait(self.interval_seconds)
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


autonomous_loop = AutonomousLoopManager()


def _services():
    if has_app_context():
        return get_core_services()
    if autonomous_loop._app is not None:
        return get_core_services(autonomous_loop._app)
    raise RuntimeError("core_services_unavailable")


def init_autopilot(app=None):
    try:
        if app is not None:
            autonomous_loop.bind_app(app)
        autonomous_loop.restore()
    except Exception as e:
        logging.warning(f"Autonomous loop restore failed: {e}")


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

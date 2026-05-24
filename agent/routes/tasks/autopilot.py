import logging
import os
import threading
import time
import contextlib
import concurrent.futures
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
from agent.routes.tasks.autopilot_dispatch_policy import resolve_effective_concurrency
from agent.routes.tasks.autopilot_tick_engine import execute_autopilot_tick
from agent.routes.tasks.orchestration_policy import compute_retry_delay_seconds
from agent.routes.tasks.utils import _forward_to_worker, _update_local_task_status
from agent.services.service_registry import get_core_services
from agent.services.repository_registry import get_repository_registry
from agent.services.provider_observer_service import get_provider_observer_service

autopilot_bp = Blueprint("tasks_autopilot", __name__)

AUTOPILOT_STATE_KEY = "autonomous_loop_state"


def _background_threads_disabled(app: Any | None = None) -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


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
        self._lock = threading.Lock()          # start/stop/persist lifecycle
        # thr-016: per-goal tick tracking replaces _tick_lock. Different goals
        # can tick concurrently; same-goal concurrency is blocked. This prevents
        # goal-switch deadlocks where _tick_lock was held for up to 180s.
        self._active_goal_ticks: set[str] = set()
        self._active_goal_tick_started: dict[str, float] = {}
        self._active_goal_ticks_lock = threading.Lock()
        # thr-002: protects dispatched_count, completed_count, failed_count, last_error
        self._counters_lock = threading.Lock()
        # thr-003: protects _worker_cursor, _worker_circuit_open_until, _worker_failure_streak
        self._routing_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.interval_seconds = 1
        # Keep default conservative for local Ollama stability.
        self.max_concurrency = 1
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
        self._provider_backpressure_until: dict[str, float] = {}
        self._provider_backpressure_reason: dict[str, str] = {}
        self._forward_http_error_counts: dict[str, int] = {}
        self._forward_http_error_last: dict[str, dict[str, Any]] = {}
        self._task_propose_streak: dict[str, int] = {}
        self._task_propose_last_attempt_at: dict[str, float] = {}
        self._task_propose_next_allowed_at: dict[str, float] = {}
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
        # Cap restored interval at the class default (5s) — prevents old persisted values from inflating latency.
        restored = int(data.get("interval_seconds") or self.interval_seconds)
        self.interval_seconds = min(restored, 5)
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
                self.interval_seconds = max(1, int(interval_seconds))
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
            # New start => new autonomous session baseline.
            # Without this reset, a previously tripped guardrail (for example
            # tick_count >= max_ticks_total) keeps blocking all future starts.
            self.tick_count = 0
            self.dispatched_count = 0
            self.completed_count = 0
            self.failed_count = 0
            self.last_error = None
            self.running = True
            self.started_at = time.time()
            self._stop_event.clear()
            self._wake_event.clear()
            if background:
                self._thread = threading.Thread(target=self._run_loop, daemon=True, name="autonomous-scrum-loop")
                self._thread.start()
            if persist:
                self._persist_state(enabled=True)

    def restart_for_goal(
        self,
        goal: str,
        *,
        team_id: str | None = None,
        persist: bool = True,
    ):
        """Restart the autopilot loop with a different goal scope.

        If the loop is running with a different goal, signal the current tick
        to abort (via _stop_event) and start a fresh loop for the new goal.
        """
        with self._lock:
            if not self.running:
                return  # not running — start() will be called by the caller
            if self.goal == str(goal or "").strip():
                self._wake_event.set()
                return  # same goal, just wake up
            # Different goal — stop the current loop, let the caller start fresh.
            old_thread = self._thread
            self.running = False
            self._stop_event.set()
            self._wake_event.set()
        if old_thread and old_thread.is_alive() and old_thread is not threading.current_thread():
            old_thread.join(timeout=5.0)
        with self._lock:
            if self._thread is old_thread:
                self._thread = None

    def stop(self, persist: bool = True):
        thread_to_join = None
        with self._lock:
            self.running = False
            self._stop_event.set()
            self._wake_event.set()  # unblock any sleep so the thread exits promptly
            thread_to_join = self._thread
            if persist:
                self._persist_state(enabled=False)
        if thread_to_join and thread_to_join.is_alive() and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=2.0)
        with self._lock:
            if self._thread is thread_to_join:
                self._thread = None

    def wake(self) -> None:
        """Interrupt the inter-tick sleep so the next tick fires immediately."""
        self._wake_event.set()

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
            "forward_http_errors": {
                "counts": {k: int(v) for k, v in self._forward_http_error_counts.items()},
                "last": {k: dict(v) for k, v in self._forward_http_error_last.items()},
            },
        }

    def status(self) -> dict:
        with self._lock:
            policy = self._security_policy()
            effective_max_concurrency = resolve_effective_concurrency(
                requested_max_concurrency=self.max_concurrency,
                security_policy=policy,
            )
            status = {
                "running": self.running,
                "interval_seconds": self.interval_seconds,
                "max_concurrency": self.max_concurrency,
                # thr-013: effective value after security-policy cap is applied.
                "effective_max_concurrency": effective_max_concurrency,
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
                "effective_security_policy": policy,
                "circuit_breakers": self._circuit_status_unlocked(),
            }
        # Direct provider probing is intentionally outside self._lock so
        # network probe latency never blocks loop state reads/writes.
        try:
            app_cfg = self._app_config()
            agent_cfg = (app_cfg.get("AGENT_CONFIG", {}) or {})
            provider_urls = (app_cfg.get("PROVIDER_URLS", {}) or {})
            timeout_cfg = agent_cfg.get("provider_observer_timeout_seconds", 3)
            try:
                timeout_seconds = max(1.0, min(10.0, float(timeout_cfg) * 2.0 + 1.0))
            except Exception:
                timeout_seconds = 7.0
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(
                    get_provider_observer_service().snapshot,
                    agent_config=agent_cfg,
                    provider_urls=provider_urls,
                )
                status["provider_observer"] = future.result(timeout=timeout_seconds)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        except concurrent.futures.TimeoutError:
            status["provider_observer"] = {
                "enabled": True,
                "source": "hub_direct_probe",
                "error": "provider_observer_timeout_guard",
                "providers": {},
                "observed_at": time.time(),
            }
        except Exception as exc:
            status["provider_observer"] = {
                "enabled": True,
                "source": "hub_direct_probe",
                "error": f"{type(exc).__name__}: {exc}",
                "providers": {},
                "observed_at": time.time(),
            }
        return status

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

    # thr-003: all three methods acquire _routing_lock to protect _worker_cursor,
    # _worker_circuit_open_until and _worker_failure_streak against concurrent access.

    def _is_worker_circuit_open(self, worker_url: str) -> bool:
        with self._routing_lock:
            until = self._worker_circuit_open_until.get(worker_url, 0.0)
        return until > time.time()

    def _record_worker_success(self, worker_url: str) -> None:
        with self._routing_lock:
            self._worker_failure_streak[worker_url] = 0
            self._worker_circuit_open_until.pop(worker_url, None)

    def _record_worker_failure(
        self, worker_url: str, reason: str, task_id: str | None = None, endpoint: str | None = None
    ) -> None:
        cfg = self._resilience_config()
        with self._routing_lock:
            streak = int(self._worker_failure_streak.get(worker_url, 0)) + 1
            self._worker_failure_streak[worker_url] = streak
            if streak >= cfg["circuit_breaker_threshold"]:
                open_until = time.time() + cfg["circuit_breaker_open_seconds"]
                self._worker_circuit_open_until[worker_url] = open_until
                error_msg = f"worker_circuit_open:{worker_url}:{reason}"
            else:
                open_until = None
                error_msg = None
        if error_msg:
            with self._counters_lock:
                self.last_error = error_msg
            details = {
                "worker_url": worker_url,
                "reason": reason,
                "open_until": open_until,
                "failure_streak": streak,
                "endpoint": endpoint,
                "task_id": task_id,
            }
            log_audit("autopilot_worker_circuit_open", details)

    # thr-002: protected counter/error mutators used by _dispatch_one_task threads.

    def _increment_dispatched(self) -> None:
        with self._counters_lock:
            self.dispatched_count += 1

    def _increment_completed(self) -> None:
        with self._counters_lock:
            self.completed_count += 1

    def _increment_failed(self) -> None:
        with self._counters_lock:
            self.failed_count += 1

    def _increment_tick_count(self) -> None:
        with self._counters_lock:
            self.tick_count += 1

    def _set_last_error(self, error: str | None) -> None:
        with self._counters_lock:
            self.last_error = error

    def _assign_worker(self, task: Any, workers: list) -> tuple:
        """Atomically advance the worker cursor and return (target_worker, was_assigned).

        thr-003: cursor read+write is protected by _routing_lock so two threads
        cannot receive the same worker slot.
        """
        from agent.routes.tasks.autopilot_dispatch_policy import resolve_target_worker_for_task
        with self._routing_lock:
            setattr(task, "_hub_can_be_worker", bool(settings.hub_can_be_worker))
            setattr(task, "_local_worker_url", (settings.agent_url or f"http://localhost:{settings.port}").rstrip("/"))
            target_worker, self._worker_cursor, was_assigned, reason_code = resolve_target_worker_for_task(
                task=task,
                workers=workers,
                worker_cursor=self._worker_cursor,
            )
        return target_worker, was_assigned, reason_code

    def _circuit_open_details(self, worker_url: str) -> tuple[float, int]:
        """Return (open_until, failure_streak) for a worker under _routing_lock."""
        with self._routing_lock:
            return (
                self._worker_circuit_open_until.get(worker_url, 0.0),
                int(self._worker_failure_streak.get(worker_url, 0)),
            )

    def _provider_backpressure_details(self, provider: str) -> tuple[float, str]:
        provider_key = str(provider or "").strip().lower()
        with self._routing_lock:
            until = float(self._provider_backpressure_until.get(provider_key, 0.0))
            reason = str(self._provider_backpressure_reason.get(provider_key, "") or "")
        return until, reason

    def _is_provider_backpressure_active(self, provider: str) -> bool:
        provider_key = str(provider or "").strip().lower()
        now = time.time()
        with self._routing_lock:
            until = float(self._provider_backpressure_until.get(provider_key, 0.0))
            if until <= now:
                self._provider_backpressure_until.pop(provider_key, None)
                self._provider_backpressure_reason.pop(provider_key, None)
                return False
            return True

    def _record_provider_backpressure(self, provider: str, reason: str) -> None:
        provider_key = str(provider or "").strip().lower()
        if not provider_key:
            return
        cfg = self._agent_config() or {}
        hold_seconds = max(3, min(int(cfg.get("autopilot_provider_backpressure_seconds") or 20), 120))
        until = time.time() + hold_seconds
        with self._routing_lock:
            self._provider_backpressure_until[provider_key] = until
            self._provider_backpressure_reason[provider_key] = str(reason or "runtime_backpressure")

    def _task_propose_backoff_details(self, task_id: str) -> tuple[bool, float]:
        key = str(task_id or "").strip()
        if not key:
            return False, 0.0
        now = time.time()
        with self._routing_lock:
            allowed_at = float(self._task_propose_next_allowed_at.get(key, 0.0) or 0.0)
        if allowed_at <= now:
            return False, 0.0
        return True, max(0.0, allowed_at - now)

    def _record_task_propose_attempt(self, task_id: str, *, success: bool) -> None:
        key = str(task_id or "").strip()
        if not key:
            return
        now = time.time()
        cfg = self._agent_config() or {}
        min_interval = max(0.1, min(float(cfg.get("autopilot_task_propose_min_interval_seconds") or 0.75), 5.0))
        max_backoff = max(min_interval, min(float(cfg.get("autopilot_task_propose_max_backoff_seconds") or 30.0), 120.0))
        with self._routing_lock:
            prev_ts = float(self._task_propose_last_attempt_at.get(key, 0.0) or 0.0)
            streak = int(self._task_propose_streak.get(key, 0))
            if success:
                streak = 0
            else:
                if prev_ts and (now - prev_ts) <= max(min_interval * 2.0, 2.0):
                    streak = min(streak + 1, 8)
                else:
                    streak = 1
            delay = min(max_backoff, min_interval * (2 ** max(0, streak)))
            self._task_propose_streak[key] = streak
            self._task_propose_last_attempt_at[key] = now
            self._task_propose_next_allowed_at[key] = now + delay

    def _forward_with_retry(self, worker_url: str, endpoint: str, payload: dict, token: str | None = None) -> dict:
        cfg = self._resilience_config()
        last_exc: Exception | None = None
        resolved_token = token
        hub_url = str(getattr(settings, "hub_url", "") or "").strip().rstrip("/")
        is_step_endpoint = endpoint.startswith("/tasks/") and "/step/" in endpoint
        with contextlib.suppress(Exception):
            agent = get_repository_registry(self._app).agent_repo.get_by_url(worker_url)
            current_token = str(getattr(agent, "token", "") or "").strip()
            if current_token:
                resolved_token = current_token
        target_url = worker_url
        if settings.role == "hub" and is_step_endpoint and hub_url:
            # Task-scoped step endpoints are hub-owned (task state + routing context).
            # Hub may delegate internals further, but the API contract lives here.
            target_url = hub_url

        for attempt in range(1, cfg["retry_attempts"] + 1):
            try:
                res = _forward_to_worker(target_url, endpoint, payload, token=resolved_token if target_url == worker_url else None)
                if res is None:
                    raise RuntimeError(f"worker_empty_response:{target_url}:{endpoint}")
                if isinstance(res, dict) and str(res.get("status") or "").strip().lower() == "error":
                    http_status = int(res.get("http_status") or 0)
                    key = f"{target_url}|{endpoint}|{http_status or 'unknown'}"
                    with self._routing_lock:
                        self._forward_http_error_counts[key] = int(self._forward_http_error_counts.get(key, 0)) + 1
                        self._forward_http_error_last[key] = {
                            "http_status": http_status,
                            "message": str(res.get("message") or ""),
                            "at": time.time(),
                            "task_id": str((payload or {}).get("task_id") or ""),
                        }
                    # Some worker runtimes intentionally do not expose step endpoints.
                    # Fall back to local hub execution path for task step operations.
                    if (
                        http_status == 404
                        and is_step_endpoint
                        and hub_url
                        and worker_url.rstrip("/") != hub_url
                    ):
                        res = _forward_to_worker(hub_url, endpoint, payload, token=None)
                        if not (isinstance(res, dict) and str(res.get("status") or "").strip().lower() == "error"):
                            self._record_worker_success(worker_url)
                            normalized = unwrap_api_envelope(res)
                            if not isinstance(normalized, dict) or not normalized:
                                raise RuntimeError(f"worker_empty_payload:{hub_url}:{endpoint}")
                            return normalized
                    # Retry tokenless only on explicit auth failures.
                    if resolved_token and http_status == 401:
                        res = _forward_to_worker(worker_url, endpoint, payload, token=None)
                        if isinstance(res, dict) and str(res.get("status") or "").strip().lower() == "error":
                            raise RuntimeError(
                                f"worker_http_error:{target_url}:{endpoint}:status={int(res.get('http_status') or 0)}:"
                                f"{str(res.get('message') or '')}"
                            )
                    else:
                        raise RuntimeError(
                            f"worker_http_error:{target_url}:{endpoint}:status={http_status}:{str(res.get('message') or '')}"
                        )
                self._record_worker_success(worker_url)
                normalized = unwrap_api_envelope(res)
                if not isinstance(normalized, dict) or not normalized:
                    raise RuntimeError(f"worker_empty_payload:{target_url}:{endpoint}")
                return normalized
            except Exception as e:
                err_text = str(e or "")
                err_lc = err_text.lower()
                # Worker tokens can drift across container restarts; for internal
                # hub->worker calls we degrade gracefully and retry once without
                # bearer token on explicit auth failures.
                if resolved_token and (
                    "401" in err_lc
                    or "unauthorized" in err_lc
                    or "invalid or missing registration token" in err_lc
                    or "worker_empty_response" in err_lc
                    or "worker_empty_payload" in err_lc
                ):
                    try:
                        res = _forward_to_worker(worker_url, endpoint, payload, token=None)
                        if res is None:
                            raise RuntimeError(f"worker_empty_response:{worker_url}:{endpoint}:tokenless")
                        self._record_worker_success(worker_url)
                        if payload.get("task_id"):
                            _append_trace_event(
                                payload["task_id"],
                                "autopilot_forward_auth_fallback",
                                worker_url=worker_url,
                                endpoint=endpoint,
                                reason="token_401_retry_without_token",
                            )
                        normalized = unwrap_api_envelope(res)
                        if not isinstance(normalized, dict) or not normalized:
                            raise RuntimeError(f"worker_empty_payload:{worker_url}:{endpoint}:tokenless")
                        return normalized
                    except Exception as auth_fallback_exc:
                        e = auth_fallback_exc
                last_exc = e
                err_text = str(e or "")
                err_lc = err_text.lower()
                if "ollama" in err_lc and "/api/generate" in err_lc and "timeout" in err_lc:
                    self._record_provider_backpressure("ollama", "ollama_generate_timeout")
                self._record_worker_failure(
                    worker_url,
                    f"forward_failed:{endpoint}",
                    task_id=(payload or {}).get("task_id"),
                    endpoint=endpoint,
                )
                permanent_http_4xx = "worker_http_error:" in err_lc and "status=4" in err_lc
                if attempt < cfg["retry_attempts"]:
                    if permanent_http_4xx:
                        break
                    if cfg.get("retry_backoff_strategy") == "constant":
                        delay = float(min(cfg["retry_backoff_seconds"], cfg["retry_max_backoff_seconds"]))
                    else:
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
                            retry_backoff_strategy=cfg.get("retry_backoff_strategy"),
                        )
                    time.sleep(delay)
                    _tid = str((payload or {}).get("task_id") or "").strip()
                    if _tid:
                        with contextlib.suppress(Exception):
                            _t = get_repository_registry(self._app).task_repo.get_by_id(_tid)
                            if _t and str(getattr(_t, "status", "") or "") in {"completed", "failed", "cancelled", "skipped"}:
                                last_exc = RuntimeError(f"task_terminal_during_retry:{_tid}:{_t.status}")
                                break
        raise RuntimeError(f"worker_forward_failed:{worker_url}:{endpoint}:{last_exc}")

    def tick_once(self) -> dict:
        if not has_app_context() and self._app is not None:
            with self._app.app_context():
                return self.tick_once()
        goal_key = str(self.goal or "").strip() or "__none__"
        stale_after = 300.0
        now = time.time()
        with self._active_goal_ticks_lock:
            if goal_key in self._active_goal_ticks:
                started = float(self._active_goal_tick_started.get(goal_key) or 0.0)
                if started and (now - started) > stale_after:
                    logging.warning(
                        "Autopilot stale active tick recovered for goal %s (age=%.1fs)",
                        goal_key,
                        now - started,
                    )
                    self._active_goal_ticks.discard(goal_key)
                    self._active_goal_tick_started.pop(goal_key, None)
                else:
                    return {"dispatched": 0, "reason": "tick_already_in_progress"}
            self._active_goal_ticks.add(goal_key)
            self._active_goal_tick_started[goal_key] = now
        try:
            return execute_autopilot_tick(
                loop=self,
                services=_services(),
                append_trace_event=_append_trace_event,
                task_dependencies=_task_dependencies,
                update_local_task_status=_update_local_task_status,
            )
        finally:
            with self._active_goal_ticks_lock:
                self._active_goal_ticks.discard(goal_key)
                self._active_goal_tick_started.pop(goal_key, None)

    def _run_loop(self):
        app = self._app
        try:
            while not self._stop_event.is_set():
                tick_result: dict = {}
                try:
                    if app is not None:
                        with app.app_context():
                            tick_result = self.tick_once()
                    else:
                        # Fallback for tests/misconfiguration; request-driven tick still works.
                        tick_result = self.tick_once()
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
                # Skip the inter-tick sleep when tasks were just dispatched so
                # their newly-unblocked dependents are picked up immediately.
                if int((tick_result or {}).get("dispatched") or 0) > 0:
                    self._wake_event.clear()
                    continue
                self._wake_event.wait(self.interval_seconds)
                self._wake_event.clear()
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


def request_autopilot_wake(event_type: str, **details: Any) -> None:
    """Best-effort event wakeup for the hub autopilot loop."""
    try:
        autonomous_loop.wake()
        if details.get("task_id"):
            _append_trace_event(str(details.get("task_id")), f"autopilot_wake_{event_type}", **details)
    except Exception:
        pass


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

import logging
import threading
import time
from typing import Any

from flask import Blueprint, request, current_app

from agent.auth import check_auth, admin_required
from agent.common.errors import api_response
from agent.common.audit import log_audit
from agent.config import settings
from agent.db_models import ConfigDB
from agent.repository import agent_repo, config_repo, task_repo, team_repo
from agent.common.api_envelope import unwrap_api_envelope
from agent.routes.tasks.quality_gates import evaluate_quality_gates
from agent.routes.tasks.utils import _forward_to_worker, _update_local_task_status
from agent.tool_guardrails import evaluate_tool_call_guardrails, estimate_text_tokens, estimate_tool_calls_tokens

autopilot_bp = Blueprint("tasks_autopilot", __name__)

AUTOPILOT_STATE_KEY = "autonomous_loop_state"


def _append_trace_event(task_id: str, event_type: str, **data: Any) -> None:
    task = task_repo.get_by_id(task_id)
    if not task:
        return
    history = list(task.history or [])
    history.append({"event_type": event_type, "timestamp": time.time(), **data})
    _update_local_task_status(task_id, task.status, history=history)


def _append_circuit_event_for_worker_tasks(worker_url: str, event_type: str, **data: Any) -> int:
    affected = 0
    for task in task_repo.get_all():
        if (task.assigned_agent_url or "") != worker_url:
            continue
        _append_trace_event(task.id, event_type, worker_url=worker_url, **data)
        affected += 1
    return affected


def _task_dependencies(task: Any) -> list[str]:
    deps = []
    for item in (getattr(task, "depends_on", None) or []):
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
        config_repo.save(ConfigDB(key=AUTOPILOT_STATE_KEY, value_json=__import__("json").dumps(state)))

    def _security_policy(self) -> dict:
        level = (self.security_level or "safe").strip().lower()
        if level not in {"safe", "balanced", "aggressive"}:
            level = "safe"

        base = {
            "safe": {
                "max_concurrency_cap": 1,
                "execute_timeout": 45,
                "execute_retries": 0,
                "allowed_tool_classes": ["read"],
            },
            "balanced": {
                "max_concurrency_cap": 2,
                "execute_timeout": 60,
                "execute_retries": 1,
                "allowed_tool_classes": ["read", "write"],
            },
            "aggressive": {
                "max_concurrency_cap": 4,
                "execute_timeout": 120,
                "execute_retries": 2,
                "allowed_tool_classes": ["read", "write", "admin", "unknown"],
            },
        }[level]
        return {"level": level, **base}

    def restore(self):
        cfg = config_repo.get_by_key(AUTOPILOT_STATE_KEY)
        if not cfg:
            return
        try:
            data = __import__("json").loads(cfg.value_json or "{}")
        except Exception:
            logging.warning("Could not parse autonomous loop state config.")
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
        with self._lock:
            self.running = False
            self._stop_event.set()
            if persist:
                self._persist_state(enabled=False)

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
        cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("autonomous_guardrails", {}) or {}
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "max_runtime_seconds": int(cfg.get("max_runtime_seconds") or 21600),
            "max_ticks_total": int(cfg.get("max_ticks_total") or 5000),
            "max_dispatched_total": int(cfg.get("max_dispatched_total") or 50000),
        }

    def _check_guardrails(self) -> str | None:
        limits = self._guardrail_limits()
        if not limits["enabled"]:
            return None
        now = time.time()
        if self.started_at and limits["max_runtime_seconds"] > 0:
            if (now - self.started_at) >= limits["max_runtime_seconds"]:
                return "guardrail_max_runtime_seconds_exceeded"
        if limits["max_ticks_total"] > 0 and self.tick_count >= limits["max_ticks_total"]:
            return "guardrail_max_ticks_total_exceeded"
        if limits["max_dispatched_total"] > 0 and self.dispatched_count >= limits["max_dispatched_total"]:
            return "guardrail_max_dispatched_total_exceeded"
        return None

    def _resilience_config(self) -> dict:
        cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("autonomous_resilience", {}) or {}
        return {
            "retry_attempts": max(1, int(cfg.get("retry_attempts") or 2)),
            "retry_backoff_seconds": max(0.0, float(cfg.get("retry_backoff_seconds") or 0.2)),
            "circuit_breaker_threshold": max(1, int(cfg.get("circuit_breaker_threshold") or 3)),
            "circuit_breaker_open_seconds": max(1.0, float(cfg.get("circuit_breaker_open_seconds") or 30.0)),
        }

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
            if task_id:
                _append_trace_event(task_id, "autopilot_worker_circuit_open", **details)
            else:
                _append_circuit_event_for_worker_tasks(worker_url, "autopilot_worker_circuit_open", **details)
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
                    time.sleep(cfg["retry_backoff_seconds"] * attempt)
        raise RuntimeError(f"worker_forward_failed:{worker_url}:{endpoint}:{last_exc}")

    def tick_once(self) -> dict:
        if settings.role != "hub":
            return {"dispatched": 0, "reason": "hub_only"}
        if self.running:
            guardrail_reason = self._check_guardrails()
            if guardrail_reason:
                self.last_error = guardrail_reason
                self.stop(persist=True)
                return {"dispatched": 0, "reason": guardrail_reason}

        all_tasks = task_repo.get_all()
        if self.team_id:
            all_tasks = [t for t in all_tasks if (t.team_id or "") == self.team_id]
        by_id = {t.id: t for t in all_tasks}

        # Dependency handling: Task wird erst freigegeben, wenn alle Dependencies abgeschlossen sind.
        # Falls eine Dependency fehlschlaegt, wird der Task ebenfalls fehlschlagen.
        for t in all_tasks:
            deps = _task_dependencies(t)
            if not deps:
                continue
            dep_statuses = []
            for dep_id in deps:
                dep_task = by_id.get(dep_id)
                if dep_task is None:
                    dep_statuses.append(("missing", dep_id))
                else:
                    dep_statuses.append((((dep_task.status or "").lower()), dep_id))
            my_status = (t.status or "").lower()
            has_failed = any(status == "failed" for status, _ in dep_statuses)
            all_done = dep_statuses and all(status == "completed" for status, _ in dep_statuses)
            if my_status == "blocked" and all_done:
                _update_local_task_status(t.id, "todo")
                _append_trace_event(
                    t.id,
                    "dependency_unblocked",
                    depends_on=deps,
                    reason="all_dependencies_completed",
                )
            elif my_status == "blocked" and has_failed:
                _update_local_task_status(
                    t.id,
                    "failed",
                    error=f"dependency_failed:{','.join(dep_id for status, dep_id in dep_statuses if status == 'failed')}",
                )
                _append_trace_event(
                    t.id,
                    "dependency_failed",
                    depends_on=deps,
                    reason="dependency_failed",
                )
            elif my_status in {"todo", "created", "assigned"} and not all_done:
                _update_local_task_status(t.id, "blocked")
                _append_trace_event(
                    t.id,
                    "dependency_blocked",
                    depends_on=deps,
                    reason="waiting_for_dependencies",
                )

        # Nach eventuellen Entsperrungen neu laden.
        all_tasks = task_repo.get_all()
        if self.team_id:
            all_tasks = [t for t in all_tasks if (t.team_id or "") == self.team_id]
        candidates = [t for t in all_tasks if (t.status or "").lower() in {"todo", "created", "assigned"}]
        if not candidates:
            self.last_tick_at = time.time()
            self.tick_count += 1
            self._persist_state(enabled=self.running)
            return {"dispatched": 0, "reason": "no_candidates"}

        workers = [a for a in agent_repo.get_all() if (a.role or "").lower() == "worker" and a.status == "online"]
        workers = [w for w in workers if not self._is_worker_circuit_open(w.url)]
        if not workers:
            self.last_error = "no_available_workers"
            self.last_tick_at = time.time()
            self.tick_count += 1
            self._persist_state(enabled=self.running)
            return {"dispatched": 0, "reason": "no_available_workers"}

        dispatched = 0
        policy = self._security_policy()
        effective_concurrency = max(1, min(int(self.max_concurrency), int(policy["max_concurrency_cap"])))
        for task in sorted(candidates, key=lambda t: (t.updated_at or 0.0))[:effective_concurrency]:
            target_worker = None
            if task.assigned_agent_url:
                target_worker = next((w for w in workers if w.url == task.assigned_agent_url), None)
            if target_worker is None:
                target_worker = workers[self._worker_cursor % len(workers)]
                self._worker_cursor += 1
                _update_local_task_status(
                    task.id,
                    "assigned",
                    assigned_agent_url=target_worker.url,
                    assigned_agent_token=target_worker.token,
                )
                _append_trace_event(
                    task.id,
                    "autopilot_handoff",
                    delegated_to=target_worker.url,
                    reason="round_robin_assignment",
                )

            try:
                propose_data = self._forward_with_retry(
                    target_worker.url,
                    f"/tasks/{task.id}/step/propose",
                    {"task_id": task.id},
                    token=target_worker.token,
                )
            except Exception as e:
                _update_local_task_status(task.id, "failed", error=str(e))
                _append_trace_event(task.id, "autopilot_worker_failed", delegated_to=target_worker.url, reason=str(e))
                if self._is_worker_circuit_open(target_worker.url):
                    _append_trace_event(
                        task.id,
                        "autopilot_worker_circuit_open",
                        worker_url=target_worker.url,
                        reason="forward_failed",
                        open_until=self._worker_circuit_open_until.get(target_worker.url),
                        failure_streak=int(self._worker_failure_streak.get(target_worker.url, 0)),
                    )
                self.failed_count += 1
                continue
            command = propose_data.get("command")
            tool_calls = propose_data.get("tool_calls")
            reason = propose_data.get("reason")
            if not command and not tool_calls:
                _update_local_task_status(task.id, "failed", error="autopilot_no_executable_step", last_proposal={"reason": reason})
                _append_trace_event(
                    task.id,
                    "autopilot_decision_failed",
                    delegated_to=target_worker.url,
                    reason=reason or "autopilot_no_executable_step",
                )
                self.failed_count += 1
                continue

            _append_trace_event(
                task.id,
                "autopilot_decision",
                delegated_to=target_worker.url,
                reason=reason,
                command=command,
                tool_calls=tool_calls,
            )

            if tool_calls:
                guard_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
                dynamic_guard = dict((guard_cfg.get("llm_tool_guardrails", {}) or {}))
                tool_classes = dynamic_guard.get("tool_classes", {}) or {}
                allowed_classes = set(policy["allowed_tool_classes"])
                all_classes = set(tool_classes.values()) | {"unknown"}
                blocked_classes = sorted([c for c in all_classes if c not in allowed_classes])
                dynamic_guard["blocked_classes"] = blocked_classes
                token_usage = {
                    "prompt_tokens": estimate_text_tokens(command or reason or task.description),
                    "history_tokens": estimate_text_tokens(__import__("json").dumps(task.history or [], ensure_ascii=False)),
                    "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
                }
                token_usage["estimated_total_tokens"] = sum(int(token_usage.get(k) or 0) for k in token_usage)
                decision = evaluate_tool_call_guardrails(
                    tool_calls, {"llm_tool_guardrails": dynamic_guard}, token_usage=token_usage
                )
                if not decision.allowed:
                    _update_local_task_status(
                        task.id,
                        "failed",
                        error=f"security_policy_tool_guardrail_blocked:{','.join(decision.reasons)}",
                        last_proposal={"reason": reason, "command": command, "tool_calls": tool_calls},
                    )
                    _append_trace_event(
                        task.id,
                        "autopilot_security_policy_blocked",
                        delegated_to=target_worker.url,
                        security_level=policy["level"],
                        blocked_reasons=decision.reasons,
                        blocked_tools=decision.blocked_tools,
                    )
                    self.failed_count += 1
                    continue

            execute_payload = {
                "task_id": task.id,
                "command": command,
                "tool_calls": tool_calls,
                "timeout": int(policy["execute_timeout"]),
                "retries": int(policy["execute_retries"]),
            }
            try:
                execute_data = self._forward_with_retry(
                    target_worker.url,
                    f"/tasks/{task.id}/step/execute",
                    execute_payload,
                    token=target_worker.token,
                )
            except Exception as e:
                _update_local_task_status(task.id, "failed", error=str(e))
                _append_trace_event(task.id, "autopilot_worker_failed", delegated_to=target_worker.url, reason=str(e))
                if self._is_worker_circuit_open(target_worker.url):
                    _append_trace_event(
                        task.id,
                        "autopilot_worker_circuit_open",
                        worker_url=target_worker.url,
                        reason="forward_failed",
                        open_until=self._worker_circuit_open_until.get(target_worker.url),
                        failure_streak=int(self._worker_failure_streak.get(target_worker.url, 0)),
                    )
                self.failed_count += 1
                continue
            exit_code = execute_data.get("exit_code")
            output = execute_data.get("output")
            task_status = execute_data.get("status")
            if task_status not in {"completed", "failed"}:
                task_status = "completed" if (exit_code in (None, 0)) else "failed"
            if task_status == "completed":
                quality_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("quality_gates", {})
                if quality_cfg.get("autopilot_enforce", True):
                    passed, reason_code = evaluate_quality_gates(task, output, exit_code, policy=quality_cfg)
                    if not passed:
                        task_status = "failed"
                        if output:
                            output = f"{output}\n\n[quality_gate] failed: {reason_code}"
                        else:
                            output = f"[quality_gate] failed: {reason_code}"
                        _append_trace_event(
                            task.id,
                            "quality_gate_failed",
                            reason=reason_code,
                            delegated_to=target_worker.url,
                        )
            _update_local_task_status(
                task.id,
                task_status,
                last_output=output,
                last_exit_code=exit_code,
                last_proposal={"reason": reason, "command": command, "tool_calls": tool_calls},
            )
            _append_trace_event(
                task.id,
                "autopilot_result",
                delegated_to=target_worker.url,
                status=task_status,
                exit_code=exit_code,
                output_preview=(output or "")[:220],
            )
            self.dispatched_count += 1
            dispatched += 1
            if task_status == "completed":
                self.completed_count += 1
            else:
                self.failed_count += 1

        self.last_tick_at = time.time()
        self.last_error = None
        self.tick_count += 1
        self._persist_state(enabled=self.running)
        return {"dispatched": dispatched, "reason": "ok"}

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception as e:
                logging.exception(f"Autonomous loop tick failed: {e}")
                self.last_error = str(e)
                self._persist_state(enabled=self.running)
            self._stop_event.wait(self.interval_seconds)


autonomous_loop = AutonomousLoopManager()


def init_autopilot():
    try:
        autonomous_loop.restore()
    except Exception as e:
        logging.warning(f"Autonomous loop restore failed: {e}")


@autopilot_bp.route("/tasks/autopilot/start", methods=["POST"])
@check_auth
@admin_required
def autopilot_start():
    if settings.role != "hub":
        return api_response(status="error", message="hub_only", code=400)
    data = request.get_json(silent=True) or {}
    interval = data.get("interval_seconds")
    max_concurrency = data.get("max_concurrency")
    goal = data.get("goal")
    team_id = data.get("team_id")
    budget_label = data.get("budget_label")
    security_level = data.get("security_level")
    if not team_id:
        active = next((t for t in team_repo.get_all() if bool(getattr(t, "is_active", False))), None)
        if active is not None:
            team_id = active.id
    autonomous_loop.start(
        interval_seconds=interval,
        max_concurrency=max_concurrency,
        goal=goal,
        team_id=team_id,
        budget_label=budget_label,
        security_level=security_level,
        persist=True,
        background=not bool(current_app.testing),
    )
    return api_response(data=autonomous_loop.status())


@autopilot_bp.route("/tasks/autopilot/stop", methods=["POST"])
@check_auth
@admin_required
def autopilot_stop():
    autonomous_loop.stop(persist=True)
    return api_response(data=autonomous_loop.status())


@autopilot_bp.route("/tasks/autopilot/status", methods=["GET"])
@check_auth
def autopilot_status():
    return api_response(data=autonomous_loop.status())


@autopilot_bp.route("/tasks/autopilot/tick", methods=["POST"])
@check_auth
@admin_required
def autopilot_tick():
    if settings.role != "hub":
        return api_response(status="error", message="hub_only", code=400)
    result = autonomous_loop.tick_once()
    return api_response(data={**autonomous_loop.status(), **result})


@autopilot_bp.route("/tasks/autopilot/circuits", methods=["GET"])
@check_auth
def autopilot_circuits_status():
    return api_response(data=autonomous_loop.circuit_status())


@autopilot_bp.route("/tasks/autopilot/circuits/reset", methods=["POST"])
@check_auth
@admin_required
def autopilot_circuits_reset():
    data = request.get_json(silent=True) or {}
    worker_url = str(data.get("worker_url") or "").strip() or None
    before = autonomous_loop.circuit_status()
    result = autonomous_loop.reset_circuits(worker_url=worker_url)
    if worker_url:
        affected = _append_circuit_event_for_worker_tasks(worker_url, "autopilot_worker_circuit_reset", action="manual_reset")
        log_audit("autopilot_worker_circuit_reset", {"worker_url": worker_url, "affected_tasks": affected, "mode": "single"})
    else:
        affected_total = 0
        for item in before.get("open_workers", []):
            wurl = item.get("worker_url")
            if not wurl:
                continue
            affected_total += _append_circuit_event_for_worker_tasks(wurl, "autopilot_worker_circuit_reset", action="manual_reset")
        log_audit("autopilot_worker_circuit_reset", {"worker_url": None, "affected_tasks": affected_total, "mode": "all"})
    return api_response(data={**result, "circuit_breakers": autonomous_loop.circuit_status()})

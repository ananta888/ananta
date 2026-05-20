from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from agent.common.gateways.worker_gateway import get_worker_gateway
from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.services.hub_event_service import build_task_history_event
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status
from agent.utils import _http_post

_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "skipped"}


def _resolve_goal_output_dir(raw_output_dir: str) -> Path:
    raw = str(raw_output_dir or "").strip()
    if not raw:
        return Path("")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    cwd = Path.cwd()
    direct = (cwd / candidate).resolve()
    workspace_relative = (cwd / "project-workspaces" / candidate).resolve()
    if direct.exists():
        return direct
    if workspace_relative.exists():
        return workspace_relative
    return workspace_relative


def _workspace_file_count(path: Path) -> int:
    if not str(path):
        return 0
    if not path.exists() or not path.is_dir():
        return 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
    return count


def _workspace_has_any(path: Path, patterns: list[str]) -> bool:
    if not str(path) or not path.exists() or not path.is_dir():
        return False
    for pattern in patterns:
        if any(path.glob(pattern)):
            return True
    return False


def _goal_requires_fibonacci_artifacts(goal: Any) -> bool:
    goal_text = str(getattr(goal, "goal", "") or "").lower()
    mode = str(getattr(goal, "mode", "") or "").strip().lower()
    return mode == "new_software_project" and "fibonacci" in goal_text


def _maybe_finalize_goal(goal_id: str) -> None:
    try:
        from agent.repository import goal_repo

        goal_tasks = task_repo.get_by_goal_id(goal_id)
        if not goal_tasks:
            return
        statuses = {normalize_task_status(getattr(t, "status", None), default="todo") for t in goal_tasks}
        if not statuses.issubset(_TERMINAL_TASK_STATUSES):
            return
        goal = goal_repo.get_by_id(goal_id)
        if not goal or goal.status not in {"planned", "in_progress", "running"}:
            return
        new_status = "failed" if "failed" in statuses else "completed"
        current_preferences = dict(goal.execution_preferences or {})
        if new_status == "completed":
            raw_output_dir = str(current_preferences.get("output_dir") or "").strip()
            if raw_output_dir:
                resolved_output_dir = _resolve_goal_output_dir(raw_output_dir)
                file_count = _workspace_file_count(resolved_output_dir)
                diagnostics = {
                    "output_dir": raw_output_dir,
                    "resolved_output_dir": str(resolved_output_dir),
                    "workspace_file_count": file_count,
                }
                current_preferences["finalization_diagnostics"] = diagnostics
                if file_count <= 0:
                    new_status = "failed"
                    current_preferences["last_status_reason"] = "no_workspace_artifact_created"
                    current_preferences["failure_classification"] = "no_workspace_artifact_created"
                elif _goal_requires_fibonacci_artifacts(goal):
                    has_source = _workspace_has_any(
                        resolved_output_dir,
                        ["src/**/*fibonacci*.py", "src/**/*fib*.py", "src/**/*.py"],
                    )
                    has_tests = _workspace_has_any(
                        resolved_output_dir,
                        ["tests/test_*.py", "tests/**/*test*.py"],
                    )
                    has_pytest_evidence = _workspace_has_any(
                        resolved_output_dir,
                        ["artifacts/**/*pytest*.*", "**/*pytest-report*.*", "**/.pytest_cache/**"],
                    )
                    current_preferences["finalization_diagnostics"].update(
                        {
                            "fibonacci_source_present": bool(has_source),
                            "fibonacci_tests_present": bool(has_tests),
                            "pytest_evidence_present": bool(has_pytest_evidence),
                        }
                    )
                    if not (has_source and has_tests and has_pytest_evidence):
                        new_status = "failed"
                        current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                        current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                goal.execution_preferences = current_preferences
        goal.status = new_status
        goal.updated_at = time.time()
        goal_repo.save(goal)
        logging.info("Goal %s finalized as %s (all %d tasks terminal)", goal_id, new_status, len(goal_tasks))
    except Exception as exc:
        logging.warning("_maybe_finalize_goal(%s) error: %s", goal_id, exc)

_task_subscribers = []
_subscribers_lock = threading.Lock()


class TaskRuntimeService:
    def get_local_task_status(self, tid: str) -> dict[str, Any] | None:
        return get_local_task_status(tid)

    def notify_task_update(self, tid: str) -> None:
        notify_task_update(tid)

    def update_local_task_status(
        self,
        tid: str,
        status: str,
        event_type: str | None = None,
        event_actor: str = "system",
        event_details: dict | None = None,
        **kwargs,
    ) -> None:
        update_local_task_status(
            tid,
            status,
            event_type=event_type,
            event_actor=event_actor,
            event_details=event_details,
            **kwargs,
        )

    def forward_to_worker(self, worker_url: str, endpoint: str, data: dict, token: str | None = None) -> Any:
        return forward_to_worker(worker_url, endpoint, data, token=token)


def get_local_task_status(tid: str) -> dict[str, Any] | None:
    task = task_repo.get_by_id(tid)
    return task.model_dump() if task else None


def notify_task_update(tid: str) -> None:
    with _subscribers_lock:
        for subscriber_tid, queue in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                queue.put(tid)


def append_task_history_event(task: TaskDB, event_type: str, actor: str = "system", details: dict | None = None) -> None:
    history = list(task.history or [])
    history.append(build_task_history_event(task, event_type, actor=actor, details=details, timestamp=time.time()))
    task.history = history[-200:]


def update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    force: bool = False,
    **kwargs,
) -> None:
    # thr-004: thread-safe — each call opens its own SQLAlchemy Session via
    # `with Session(engine)` in the repository layer. The engine connection pool
    # (PostgreSQL QueuePool) is designed for concurrent multi-threaded access.
    # Concurrent calls for *different* task IDs are fully safe (row-level locking).
    # Concurrent calls for the *same* task ID are serialized by PostgreSQL row locks.
    task = task_repo.get_by_id(tid)
    if not task:
        task = TaskDB(id=tid, created_at=time.time(), status="todo")

    old_status = task.status
    normalized_status = normalize_task_status(status)

    if not force and old_status:
        ok, reason = can_transition_to(old_status, normalized_status)
        if not ok:
            allow_recovery_completion = (
                old_status == "failed"
                and normalized_status == "completed"
                and (
                    event_type == "artifact_first_completion"
                    or "last_output" in kwargs
                    or "verification_status" in kwargs
                )
            )
            if allow_recovery_completion:
                logging.warning(
                    "Recovery-Transition fuer Task %s erzwungen: %s (artifact/execution completion)",
                    tid,
                    reason,
                )
                ok = True
            logging.warning("Blockierter Statuswechsel fuer Task %s: %s (force=False)", tid, reason)
            # Wir blockieren hier aktiv, wenn es kein force-Request ist
            if old_status != normalized_status and not ok:
                return

    task.status = normalized_status
    task.updated_at = time.time()

    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)

    if event_type:
        append_task_history_event(task, event_type=event_type, actor=event_actor, details=event_details or {})

    task_repo.save(task)
    notify_task_update(tid)
    try:
        from agent.routes.tasks.autopilot import request_autopilot_wake

        wake_event = "task_updated"
        if normalized_status == "todo" and old_status != "todo":
            wake_event = "task_created"
        elif normalized_status == "completed":
            wake_event = "task_completed"
        elif normalized_status == "failed":
            wake_event = "task_failed"
        request_autopilot_wake(wake_event, task_id=tid, status=normalized_status)
    except Exception:
        pass

    if normalized_status in _TERMINAL_TASK_STATUSES and task.goal_id:
        _maybe_finalize_goal(task.goal_id)

    if task.callback_url:

        def send_callback() -> None:
            import agent.common.context

            if agent.common.context.shutdown_requested:
                return
            try:
                payload = {"id": tid, "status": normalized_status, "parent_task_id": task.parent_task_id}
                if task.last_output:
                    payload["last_output"] = task.last_output
                if task.last_exit_code is not None:
                    payload["last_exit_code"] = task.last_exit_code
                payload["worker_job_id"] = task.current_worker_job_id
                verification_status = dict(task.verification_status or {})
                execution_artifacts = verification_status.get("execution_artifacts")
                if isinstance(execution_artifacts, list):
                    payload["artifacts"] = execution_artifacts

                headers = {}
                if task.callback_token:
                    headers["Authorization"] = f"Bearer {task.callback_token}"

                _http_post(task.callback_url, data=payload, headers=headers)
                logging.info("Webhook an %s gesendet fuer Task %s", task.callback_url, tid)
            except Exception as exc:
                logging.error("Fehler beim Senden des Webhooks an %s: %s", task.callback_url, exc)

        thread = threading.Thread(target=send_callback, daemon=True)
        import agent.common.context

        agent.common.context.active_threads.append(thread)
        thread.start()


def forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str | None = None) -> Any:
    return get_worker_gateway().forward_task(worker_url, endpoint, data, token=token)


def apply_artifact_first_completion(
    tid: str,
    *,
    collection_result: dict,
    advisory_parse_result: dict | None = None,
    exit_code: int | None = None,
    retry_count: int = 0,
    expected_paths: list[str] | None = None,
    verification_required: bool = False,
    allow_synthesized_manifest: bool = False,
) -> str:
    """Apply artifact-first completion policy to a task. Returns final status.

    Malformed advisory JSON never causes an infinite retry loop when artifacts pass.
    """
    from agent.services.task_artifact_completion_gate_service import get_task_artifact_completion_gate_service
    from agent.services.task_retry_policy_service import (
        get_task_retry_policy_service,
        REASON_ADVISORY_JSON_PARSE_FAILED,
    )

    completion_gate = get_task_artifact_completion_gate_service()
    retry_svc = get_task_retry_policy_service()
    final_status, decision = completion_gate.decide(
        task_id=tid,
        collection_result=collection_result,
        advisory_parse_result=advisory_parse_result,
        exit_code=exit_code,
        retry_count=retry_count,
        expected_paths=expected_paths,
        verification_required=verification_required,
        allow_synthesized_manifest=allow_synthesized_manifest,
    )

    # Advisory parse failure with valid artifacts → never requeue
    if advisory_parse_result and advisory_parse_result.get("parse_error"):
        has_valid = bool(collection_result.get("manifest_valid"))
        retry_cls = retry_svc.classify(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            retry_count=retry_count,
            has_valid_artifacts=has_valid,
        )
        if retry_cls.classification == "ignored":
            logging.info(
                "apply_artifact_first_completion: advisory parse failed but artifacts valid "
                "for task %s — not requeueing (reason_code=advisory_parse_failed_ignored)", tid,
            )

    event_details = {
        **completion_gate.event_details(decision=decision),
    }
    update_local_task_status(
        tid,
        final_status,
        event_type="artifact_first_completion",
        event_actor="system",
        event_details=event_details,
    )
    return final_status


task_runtime_service = TaskRuntimeService()


def get_task_runtime_service() -> TaskRuntimeService:
    return task_runtime_service

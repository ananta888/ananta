from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent.common.gateways.worker_gateway import get_worker_gateway
from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.services.autopilot_wake_service import request_autopilot_wake
from agent.services.hub_event_service import build_task_history_event
from agent.services.recovery_source_callback_delivery import (
    RecoverySourceCallbackDelivery,
)
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status
from agent.utils import _http_post

_TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}
_TASK_STATUS_CAS_REPOSITORY_OWNED_FIELDS = frozenset(
    {"id", "status", "updated_at", "history"}
)


def _is_recovery_related_task(task: Any) -> bool:
    details = dict(
        getattr(task, "status_reason_details", None) or {}
    )
    return bool(
        str(getattr(task, "derivation_reason", "") or "")
        == "goal_task_recovery"
        or isinstance(details.get("model_recovery"), dict)
        or isinstance(
            details.get("model_recovery_strategy"),
            dict,
        )
        or isinstance(
            details.get("model_recovery_release"),
            dict,
        )
    )


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
    summary_text = str(getattr(goal, "summary", "") or "").lower()
    mode = str(getattr(goal, "mode", "") or "").strip().lower()
    if "fibonacci" not in goal_text and "fibonacci" not in summary_text:
        return False
    return mode == "new_software_project" or "fibonacci" in goal_text or "fibonacci" in summary_text


def _workspace_has_file_matching(path: Path, predicate) -> bool:
    if not str(path) or not path.exists() or not path.is_dir():
        return False
    for item in path.rglob("*"):
        if item.is_file() and predicate(item):
            return True
    return False


def _goal_has_required_fibonacci_evidence(resolved_output_dir: Path) -> tuple[bool, dict[str, bool]]:
    source_dir = resolved_output_dir / "src" / "fibonacci"
    tests_dir = resolved_output_dir / "tests"
    has_source_file = _workspace_has_file_matching(
        source_dir,
        lambda item: item.suffix == ".py",
    )
    has_pytest_style_test = _workspace_has_file_matching(
        tests_dir,
        lambda item: item.suffix == ".py" and item.name.startswith("test_"),
    )
    has_pytest_evidence = False
    if resolved_output_dir.exists() and resolved_output_dir.is_dir():
        for item in resolved_output_dir.rglob("*"):
            if not item.is_file():
                continue
            try:
                relative_item = item.relative_to(resolved_output_dir).as_posix().lower()
            except Exception:
                relative_item = item.name.lower()
            if "pytest" in item.name.lower() or "pytest" in relative_item:
                has_pytest_evidence = True
                break
    evidence = {
        "has_source_file": has_source_file,
        "has_pytest_style_test": has_pytest_style_test,
        "has_pytest_evidence": has_pytest_evidence,
    }
    return all(evidence.values()), evidence


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
        tasks_by_id = {
            str(getattr(item, "id", "") or ""): item
            for item in goal_tasks
        }

        def is_successful_terminal(item: Any) -> bool:
            item_status = normalize_task_status(
                getattr(item, "status", None),
                default="todo",
            )
            if item_status in {"completed", "skipped"}:
                return True
            if (
                item_status != "cancelled"
                or str(
                    getattr(item, "derivation_reason", "") or ""
                )
                != "goal_task_recovery"
                or str(
                    getattr(item, "status_reason_code", "") or ""
                )
                != "recovery_parent_terminal"
            ):
                return False
            source = tasks_by_id.get(
                str(getattr(item, "source_task_id", "") or "")
            )
            return normalize_task_status(
                getattr(source, "status", None),
                default="todo",
            ) in {"completed", "skipped"}

        new_status = (
            "completed"
            if statuses and all(
                is_successful_terminal(item)
                for item in goal_tasks
            )
            else "failed"
        )
        current_preferences = dict(goal.execution_preferences or {})
        if new_status == "completed":
            raw_output_dir = str(current_preferences.get("output_dir") or "").strip()
            if not raw_output_dir:
                if _goal_requires_fibonacci_artifacts(goal):
                    new_status = "failed"
                    current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                    current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                    current_preferences["finalization_diagnostics"] = {
                        "output_dir": "",
                        "resolved_output_dir": "",
                        "workspace_file_count": 0,
                        "fibonacci_evidence": {
                            "has_source_file": False,
                            "has_pytest_style_test": False,
                            "has_pytest_evidence": False,
                            "output_dir_available": False,
                        },
                    }
                    goal.execution_preferences = current_preferences
            else:
                resolved_output_dir = _resolve_goal_output_dir(raw_output_dir)
                file_count = _workspace_file_count(resolved_output_dir)
                diagnostics = {
                    "output_dir": raw_output_dir,
                    "resolved_output_dir": str(resolved_output_dir),
                    "workspace_file_count": file_count,
                }
                requires_fibonacci_evidence = _goal_requires_fibonacci_artifacts(goal) or (resolved_output_dir / "src" / "fibonacci").exists()
                if requires_fibonacci_evidence:
                    if not resolved_output_dir.exists():
                        new_status = "failed"
                        current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                        current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                        diagnostics["fibonacci_evidence"] = {
                            "has_source_file": False,
                            "has_pytest_style_test": False,
                            "has_pytest_evidence": False,
                            "output_dir_available": False,
                        }
                    else:
                        has_required_evidence, fibonacci_evidence = _goal_has_required_fibonacci_evidence(resolved_output_dir)
                        diagnostics["fibonacci_evidence"] = fibonacci_evidence
                        if not has_required_evidence:
                            new_status = "failed"
                            current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                            current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                current_preferences["finalization_diagnostics"] = diagnostics
                if file_count <= 0:
                    new_status = "failed"
                    current_preferences["last_status_reason"] = "no_workspace_artifact_created"
                    current_preferences["failure_classification"] = "no_workspace_artifact_created"
                goal.execution_preferences = current_preferences
        from agent.services.lifecycle_service import (
            get_goal_lifecycle_service,
        )

        get_goal_lifecycle_service().transition_goal(
            goal,
            target_status=new_status,
            reason=str(
                current_preferences.get("last_status_reason") or ""
            )
            or None,
        )
        logging.info("Goal %s finalized as %s (all %d tasks terminal)", goal_id, new_status, len(goal_tasks))
    except Exception as exc:
        logging.warning("_maybe_finalize_goal(%s) error: %s", goal_id, exc)

_task_subscribers = []
_subscribers_lock = threading.Lock()
def task_status_mutation_lock(task_id: str):
    """Return the shared local/distributed mutation boundary for one task."""

    from agent.services.task_mutation_lock_service import (
        get_task_mutation_lock_port,
    )

    task = task_repo.get_by_id(str(task_id or ""))
    source_task_id = str(
        getattr(task, "source_task_id", "") or ""
    ).strip()
    lock_ids = {str(task_id or "")}
    if source_task_id:
        lock_ids.add(source_task_id)
    return get_task_mutation_lock_port().mutation_locks(lock_ids)


def _task_engine():
    """Compatibility hook; authoritative status CAS now lives in TaskRepository."""

    from agent.database import engine

    return engine


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

    def compare_and_set_local_task_status(
        self,
        tid: str,
        status: str,
        *,
        expected_statuses: set[str] | list[str] | tuple[str, ...],
        authoritative_predicate: (
            Callable[[TaskDB], bool] | None
        ) = None,
        event_type: str | None = None,
        event_actor: str = "system",
        event_details: dict | None = None,
        force: bool = False,
        **kwargs,
    ) -> bool:
        return compare_and_set_local_task_status(
            tid,
            status,
            expected_statuses=expected_statuses,
            authoritative_predicate=authoritative_predicate,
            event_type=event_type,
            event_actor=event_actor,
            event_details=event_details,
            force=force,
            **kwargs,
        )

    def forward_to_worker(self, worker_url: str, endpoint: str, data: dict, token: str | None = None) -> Any:
        return forward_to_worker(worker_url, endpoint, data, token=token)


def get_local_task_status(tid: str) -> dict[str, Any] | None:
    task = task_repo.get_by_id(tid)
    return task.model_dump() if task else None


# Fields synced from hub to worker — excludes FK-constrained columns
# (assigned_agent_url, team_id, assigned_role_id) and execution-state fields.
# What a worker may copy from a Hub manifest into its own database.
#
# plan_id is deliberately absent: tasks.plan_id carries an unconditional
# foreign key to plans.id, and the Hub's plans do not exist in a worker's
# database, so writing it fails the key outright.  goal_id and plan_node_id
# stay because their keys are composite over tenant and project, which a
# synced task does not set, so they correlate without claiming a row.  The
# plan reference is not lost — it is recorded in the sync event and in
# worker_execution_context, where nothing enforces it.
_TASK_SYNC_FIELDS: frozenset[str] = frozenset({
    "title", "description", "priority", "task_kind",
    "goal_id", "goal_trace_id", "plan_node_id",
    "retrieval_intent", "required_context_scope", "preferred_bundle_mode",
    "required_capabilities", "context_bundle_id",
    "worker_execution_context", "worker_execution_contract",
    "expected_artifacts",
    "verification_spec", "status_reason_details", "depends_on",
    "parent_task_id", "source_task_id", "derivation_reason", "derivation_depth",
})

_hub_sync_client = None


def _get_hub_sync_client():
    global _hub_sync_client
    if _hub_sync_client is None:
        from agent.common.http import HttpClient
        _hub_sync_client = HttpClient(timeout=10, retries=1)
    return _hub_sync_client


def sync_task_from_hub(tid: str) -> dict[str, Any] | None:
    """Pull an assigned Recovery child manifest from the Hub.

    Only runs on workers (non-hub role).  The dedicated endpoint accepts a
    registered Worker service identity, verifies the authoritative assignment,
    and returns only the approval-bound Recovery execution payload.  Local
    persistence still skips FK-constrained and execution-state fields.
    """
    from agent.config import settings
    role = str(getattr(settings, "role", "") or "").strip().lower()
    if role == "hub":
        return None
    hub_url = str(getattr(settings, "hub_url", "") or "").strip().rstrip("/")
    if not hub_url:
        return None
    try:
        from agent.auth import resolve_configured_agent_token
        from agent.services.recovery_task_manifest_service import (
            RECOVERY_TASK_MANIFEST_SCHEMA,
        )
        from agent.services.workflow_worker_service_auth import (
            WORKER_ID_HEADER,
            WORKER_URL_HEADER,
        )

        worker_token = resolve_configured_agent_token()
        worker_id = str(
            getattr(settings, "agent_name", "") or ""
        ).strip()
        worker_url = str(
            getattr(settings, "agent_url", "") or ""
        ).strip().rstrip("/")
        if not worker_token or not worker_id or not worker_url:
            logging.warning(
                "Cannot sync task %s: registered worker identity unavailable",
                tid,
            )
            return None
        resp = _get_hub_sync_client().get(
            (
                f"{hub_url}/internal/tasks/{tid}"
                "/recovery-child-manifest"
            ),
            timeout=10,
            return_response=True,
            silent=True,
            headers={
                "Authorization": f"Bearer {worker_token}",
                WORKER_ID_HEADER: worker_id,
                WORKER_URL_HEADER: worker_url,
            },
        )
        if resp is None or getattr(resp, "status_code", 500) != 200:
            return None
        body = resp.json()
        manifest = body.get("data") if isinstance(body, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema")
            != RECOVERY_TASK_MANIFEST_SCHEMA
            or not isinstance(manifest.get("task"), dict)
        ):
            logging.warning(
                "Rejected recovery task sync %s: manifest contract invalid",
                tid,
            )
            return None
        task_data = dict(manifest["task"])
        if str(task_data.get("id") or "") != str(tid or ""):
            logging.warning(
                "Rejected recovery task sync %s: task identity mismatch",
                tid,
            )
            return None
        from types import SimpleNamespace

        from agent.services.recovery_plan_contract import (
            calculate_recovery_task_payload_digest,
        )
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        if recovery_task_role(task_data) == "child":
            release = dict(
                (
                    task_data.get("status_reason_details") or {}
                ).get("model_recovery_release")
                or {}
            )
            expected_digest = str(
                release.get("task_payload_digest") or ""
            )
            actual_digest = (
                calculate_recovery_task_payload_digest(
                    SimpleNamespace(**task_data)
                )
            )
            if (
                not expected_digest
                or expected_digest != actual_digest
            ):
                logging.warning(
                    "Rejected recovery task sync %s: payload digest mismatch",
                    tid,
                )
                return None
        else:
            logging.warning(
                "Rejected recovery task sync %s: payload is not a child",
                tid,
            )
            return None
        kwargs = {
            k: task_data[k]
            for k in _TASK_SYNC_FIELDS
            if k in task_data and task_data[k] is not None
        }
        update_local_task_status(
            tid,
            "todo",
            force=True,
            event_type="hub_task_synced",
            event_actor="worker_sync",
            **kwargs,
        )
        logging.info("Synced task %s from hub %s", tid, hub_url)
        return get_local_task_status(tid)
    except Exception as exc:
        logging.warning("Failed to sync task %s from hub %s: %s", tid, hub_url, exc)
        return None


def notify_task_update(tid: str) -> None:
    with _subscribers_lock:
        for subscriber_tid, queue in _task_subscribers:
            if subscriber_tid == tid or subscriber_tid == "*":
                queue.put(tid)


def append_task_history_event(task: TaskDB, event_type: str, actor: str = "system", details: dict | None = None) -> None:
    history = list(task.history or [])
    history.append(build_task_history_event(task, event_type, actor=actor, details=details, timestamp=time.time()))
    task.history = history[-200:]


def _cancel_recovery_children_for_terminal_source(
    task: TaskDB,
    *,
    synchronous_delivery: bool = False,
) -> None:
    """Invalidate delegated recovery work after its source becomes terminal."""

    task_id = str(getattr(task, "id", "") or "").strip()
    goal_id = str(getattr(task, "goal_id", "") or "").strip()
    if not task_id or not goal_id:
        return
    try:
        children = [
            child
            for child in list(task_repo.get_by_goal_id(goal_id) or [])
            if str(getattr(child, "source_task_id", "") or "") == task_id
            and str(getattr(child, "derivation_reason", "") or "")
            == "goal_task_recovery"
        ]
        cancelled_child_ids: list[str] = []
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )

        recovery_gate = get_recovery_dispatch_gate_service()
        for child in children:
            child_id = str(getattr(child, "id", "") or "")
            child_status = normalize_task_status(
                getattr(child, "status", None),
                default="todo",
            )
            if child_status in _TERMINAL_TASK_STATUSES:
                continue
            changed = recovery_gate.invalidate_task(
                child_id,
                reason_code="recovery_parent_terminal",
            )
            if changed:
                cancelled_child_ids.append(child_id)
        if cancelled_child_ids:
            def cancel_worker_requests() -> None:
                from agent.services.request_cancellation_service import (
                    get_request_cancellation_service,
                )

                cancellation = get_request_cancellation_service()
                for child_id in cancelled_child_ids:
                    try:
                        cancellation.cancel_task_requests(
                            task_id=child_id,
                            include_workers=True,
                        )
                    except Exception:
                        logging.exception(
                            "Worker cancellation failed for recovery child %s",
                            child_id,
                        )
                        if synchronous_delivery:
                            raise

            if synchronous_delivery:
                cancel_worker_requests()
            else:
                threading.Thread(
                    target=cancel_worker_requests,
                    daemon=True,
                    name=f"recovery-cancel-{task_id[:24]}",
                ).start()
    except Exception:
        logging.exception(
            "Failed to cancel recovery children for terminal source %s",
            task_id,
        )
        if synchronous_delivery:
            raise


def _run_task_status_post_commit(
    *,
    task: TaskDB,
    tid: str,
    old_status: str | None,
    normalized_status: str,
    event_type: str | None,
    force: bool,
    synchronous_delivery: bool = False,
    strict_callback_delivery: bool = False,
) -> RecoverySourceCallbackDelivery | None:
    """Run the one post-commit contract for normal and CAS mutations."""

    if old_status != normalized_status:
        try:
            from agent.services.execution_audit_service import (
                get_execution_audit_service,
            )

            trace_id = (
                str(
                    getattr(task, "goal_trace_id", "") or ""
                ).strip()
                or None
            )
            audit_service = get_execution_audit_service()
            audit_service.emit_workflow_transition(
                trace_id=trace_id,
                task_id=tid,
                goal_id=getattr(task, "goal_id", None),
                from_state=str(old_status or "unknown"),
                to_state=normalized_status,
                trigger=str(event_type or "status_update"),
                policy_context="task_runtime_state_machine",
                actor_role="hub",
                details={"force": bool(force)},
            )
            audit_service.emit_write_operation(
                trace_id=trace_id,
                task_id=tid,
                goal_id=getattr(task, "goal_id", None),
                target_path_class="task_state",
                write_reason=str(
                    event_type or "status_update"
                ),
                approval_source="task_state_machine",
                verification_result=(
                    "verified"
                    if normalized_status
                    in _TERMINAL_TASK_STATUSES
                    else "pending"
                ),
                risk_level=(
                    "low"
                    if normalized_status
                    in {"todo", "in_progress", "completed"}
                    else "high"
                ),
                actor_role="hub",
                details={
                    "from_status": str(old_status or ""),
                    "to_status": normalized_status,
                },
            )
        except Exception:
            logging.exception(
                "Task status audit failed after commit for %s",
                tid,
            )

    notify_task_update(tid)
    try:
        wake_event = "task_updated"
        if normalized_status == "todo" and old_status != "todo":
            wake_event = "task_created"
        elif normalized_status == "completed":
            wake_event = "task_completed"
        elif normalized_status == "failed":
            wake_event = "task_failed"
        request_autopilot_wake(
            wake_event,
            task_id=tid,
            status=normalized_status,
        )
    except Exception:
        pass

    if normalized_status in _TERMINAL_TASK_STATUSES:
        _cancel_recovery_children_for_terminal_source(
            task,
            synchronous_delivery=synchronous_delivery,
        )
        if task.goal_id:
            _maybe_finalize_goal(task.goal_id)

    if not task.callback_url:
        if strict_callback_delivery:
            return RecoverySourceCallbackDelivery(
                delivered=True,
                callback_required=False,
                reason_code=(
                    "recovery_source_callback_not_configured"
                ),
            )
        return

    def send_callback() -> RecoverySourceCallbackDelivery | None:
        import agent.common.context

        if agent.common.context.shutdown_requested:
            if synchronous_delivery:
                raise RuntimeError(
                    "task callback delivery skipped during shutdown"
                )
            return
        try:
            payload = {
                "id": tid,
                "status": normalized_status,
                "parent_task_id": task.parent_task_id,
            }
            post_commit_marker = dict(
                (
                    dict(task.status_reason_details or {}).get(
                        "recovery_source_post_commit"
                    )
                    or {}
                )
            )
            transition_id = str(
                post_commit_marker.get("transition_id") or ""
            ).strip()
            if transition_id:
                payload["status_transition_id"] = transition_id
                payload["idempotency_key"] = transition_id
            if task.last_output:
                payload["last_output"] = task.last_output
            if task.last_exit_code is not None:
                payload["last_exit_code"] = task.last_exit_code
            payload["worker_job_id"] = task.current_worker_job_id
            from agent.services.recovery_source_result_projection import (
                project_recovery_source_callback_artifacts,
            )

            callback_artifacts = (
                project_recovery_source_callback_artifacts(task)
            )
            if callback_artifacts is None:
                # Backward-compatible projection for ordinary tasks. Recovery
                # source rows never fall back after Hub ownership is proven.
                verification_status = dict(
                    task.verification_status or {}
                )
                execution_artifacts = verification_status.get(
                    "execution_artifacts"
                )
                if isinstance(execution_artifacts, list):
                    callback_artifacts = execution_artifacts
            if isinstance(callback_artifacts, list):
                payload["artifacts"] = callback_artifacts

            headers = {}
            if task.callback_token:
                headers["Authorization"] = f"Bearer {task.callback_token}"

            if strict_callback_delivery:
                response = _http_post(
                    task.callback_url,
                    data=payload,
                    headers=headers,
                    return_response=True,
                    idempotency_key=transition_id or None,
                )
                if response is None:
                    raise RuntimeError(
                        "recovery_source_callback_no_response"
                    )
                status_code = getattr(
                    response,
                    "status_code",
                    None,
                )
                if (
                    isinstance(status_code, bool)
                    or not isinstance(status_code, int)
                ):
                    raise RuntimeError(
                        "recovery_source_callback_invalid_response"
                    )
                if status_code < 200 or status_code >= 300:
                    raise RuntimeError(
                        "recovery_source_callback_http_"
                        f"{status_code}"
                    )
                delivery = RecoverySourceCallbackDelivery(
                    delivered=True,
                    callback_required=True,
                    reason_code=(
                        "recovery_source_callback_delivered"
                    ),
                    status_code=status_code,
                )
            else:
                _http_post(
                    task.callback_url,
                    data=payload,
                    headers=headers,
                )
                delivery = None
            logging.info(
                "Webhook an %s gesendet fuer Task %s",
                task.callback_url,
                tid,
            )
            return delivery
        except Exception as exc:
            logging.error(
                "Fehler beim Senden des Webhooks an %s: %s",
                task.callback_url,
                exc,
            )
            if synchronous_delivery:
                raise

    if synchronous_delivery:
        return send_callback()

    thread = threading.Thread(target=send_callback, daemon=True)
    import agent.common.context
    agent.common.context.active_threads.append(thread)
    thread.start()


def run_external_task_status_post_commit(
    tid: str,
    *,
    old_status: str | None,
    event_type: str,
    force: bool = False,
    synchronous_delivery: bool = False,
    strict_callback_delivery: bool = False,
) -> RecoverySourceCallbackDelivery | None:
    """Apply the canonical post-commit contract to an external UoW write."""

    task = task_repo.get_by_id(str(tid or ""))
    if task is None:
        return
    return _run_task_status_post_commit(
        task=task,
        tid=str(tid or ""),
        old_status=normalize_task_status(old_status),
        normalized_status=normalize_task_status(task.status),
        event_type=event_type,
        force=force,
        synchronous_delivery=synchronous_delivery,
        strict_callback_delivery=strict_callback_delivery,
    )


def update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    force: bool = False,
    **kwargs,
) -> None:
    from agent.services.recovery_result_write_boundary import (
        defer_task_status_mutation,
    )

    if defer_task_status_mutation(
        tid,
        status,
        event_type=event_type,
        event_actor=event_actor,
        event_details=event_details,
        force=force,
        values=kwargs,
    ):
        return
    post_commit: tuple[TaskDB, str | None, str] | None = None
    with task_status_mutation_lock(tid) as lock_acquired:
        if not lock_acquired:
            raise RuntimeError(
                f"task_status_mutation_lock_unavailable:{tid}"
            )
        post_commit = _update_local_task_status_unlocked(
            tid,
            status,
            event_type=event_type,
            event_actor=event_actor,
            event_details=event_details,
            force=force,
            **kwargs,
        )
    if post_commit is None:
        return
    task, old_status, normalized_status = post_commit
    _run_task_status_post_commit(
        task=task,
        tid=tid,
        old_status=old_status,
        normalized_status=normalized_status,
        event_type=event_type,
        force=force,
    )


def compare_and_set_local_task_status(
    tid: str,
    status: str,
    *,
    expected_statuses: set[str] | list[str] | tuple[str, ...],
    authoritative_predicate: (
        Callable[[TaskDB], bool] | None
    ) = None,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    force: bool = False,
    **kwargs,
) -> bool:
    """Atomically update an existing task only from an expected state.

    PostgreSQL holds a row lock through validation and commit.  The local lock
    supplies the same serialization guarantee for the single-process SQLite
    runtime used by tests and development.
    """

    requested_fields = set(kwargs)
    unknown_fields = sorted(
        requested_fields.difference(TaskDB.model_fields)
    )
    if unknown_fields:
        logging.warning(
            "Rejected task status CAS %s with unknown fields: %s",
            tid,
            ",".join(unknown_fields),
        )
        return False
    repository_owned_fields = sorted(
        requested_fields
        & _TASK_STATUS_CAS_REPOSITORY_OWNED_FIELDS
    )
    if repository_owned_fields:
        logging.warning(
            "Rejected task status CAS %s with repository-owned fields: %s",
            tid,
            ",".join(repository_owned_fields),
        )
        return False

    from agent.common.recovery_result_write_boundary import (
        defer_task_compare_and_set,
    )

    if defer_task_compare_and_set(
        tid,
        status,
        expected_statuses=expected_statuses,
        values=kwargs,
    ):
        return True

    normalized_target = normalize_task_status(status)
    normalized_expected = {
        normalize_task_status(value)
        for value in expected_statuses
        if str(value or "").strip()
    }
    allowed_expected = {
        old_status
        for old_status in normalized_expected
        if force
        or old_status == normalized_target
        or can_transition_to(
            old_status,
            normalized_target,
        )[0]
    }
    if not allowed_expected:
        return False

    def _mutate_candidate(task: TaskDB) -> None:
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if event_type:
            append_task_history_event(
                task,
                event_type=event_type,
                actor=event_actor,
                details=event_details or {},
            )

    try:
        result = task_repo.compare_and_set_status(
            str(tid or ""),
            expected_statuses=allowed_expected,
            target_status=normalized_target,
            predicate=authoritative_predicate,
            mutate=_mutate_candidate,
        )
    except ValueError as exc:
        if str(exc).startswith("recovery_"):
            logging.warning(
                "Rejected Recovery status CAS %s: %s",
                tid,
                exc,
            )
            return False
        raise
    committed_task = result.task
    old_status = result.previous_status
    if not result.updated or committed_task is None:
        return False
    _run_task_status_post_commit(
        task=committed_task,
        tid=tid,
        old_status=old_status,
        normalized_status=normalized_target,
        event_type=event_type,
        force=force,
    )
    return True


def _update_local_task_status_unlocked(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    force: bool = False,
    **kwargs,
) -> tuple[TaskDB, str | None, str] | None:
    # Calls for the same task are serialized by task_status_mutation_lock in
    # this Hub process. The repository still opens an independent session per
    # call so different task IDs remain concurrent.
    task = task_repo.get_by_id(tid)
    if not task:
        task = TaskDB(id=tid, created_at=time.time(), status="todo")

    old_status = task.status
    normalized_status = normalize_task_status(status)

    if (
        _is_recovery_related_task(task)
        and normalize_task_status(old_status)
        in _TERMINAL_TASK_STATUSES
        and normalized_status != normalize_task_status(old_status)
        and not (
            normalize_task_status(old_status) == "completed"
            and normalized_status == "verification_failed"
            and event_type
            == "recovery_result_verification_failed"
        )
    ):
        logging.warning(
            "Rejected stale recovery-task mutation %s: %s -> %s",
            tid,
            old_status,
            normalized_status,
        )
        return None

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
                return None

    task.status = normalized_status
    task.updated_at = time.time()

    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)

    if event_type:
        append_task_history_event(task, event_type=event_type, actor=event_actor, details=event_details or {})

    task = task_repo.save(task)
    persisted_status = normalize_task_status(task.status)
    if persisted_status != normalized_status:
        logging.warning(
            "Task mutation %s was rejected by the authoritative repository: "
            "requested=%s persisted=%s",
            tid,
            normalized_status,
            persisted_status,
        )
        return None
    return task, old_status, persisted_status


def forward_to_worker(
    worker_url: str,
    endpoint: str,
    data: dict,
    token: str | None = None,
    timeout: int | None = None,
) -> Any:
    return get_worker_gateway().forward_task(
        worker_url, endpoint, data, token=token, timeout=timeout
    )


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

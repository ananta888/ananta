"""
Auto-Planner: Goal-basierte Task-Generierung und Output-Analyse.

Dieses Modul ermoeglicht:
1. Aus einem High-Level-Goal automatisch Subtasks zu generieren
2. Nach Task-Abschluss auf Folgeaufgaben zu pruefen
3. Neue Tasks automatisch zu erkennen und anzulegen
"""

import json
import os
import threading
import time
import uuid
from typing import Optional

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import PlanningError, api_response, with_error_context
from agent.db_models import ConfigDB
from agent.llm_integration import generate_text
from agent.models import AutoPlannerAnalyzeRequest, AutoPlannerConfigureRequest, AutoPlannerPlanRequest
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.planning_service import get_planning_service as get_fallback_planning_service
from agent.services.planning_utils import (
    build_planning_prompt,
    extract_json_payload,
    match_goal_template,
    parse_followup_analysis,
    parse_subtasks_from_llm_response,
    sanitize_input,
    strip_markdown_fences,
    try_load_repo_context,
    validate_goal,
)

auto_planner_bp = Blueprint("tasks_auto_planner", __name__)


def _repos():
    return get_repository_registry()


config_repo = get_repository_registry().config_repo


def get_planning_service():
    try:
        return get_core_services().planning_service
    except RuntimeError:
        return get_fallback_planning_service()


def _log():
    return get_core_services().log_service.bind(__name__)

AUTO_PLANNER_STATE_KEY = "auto_planner_state"


def _background_threads_disabled() -> bool:
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
        or bool(getattr(current_app, "testing", False))
    )


def _generate_task_id(prefix: str = "auto") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _strip_markdown_fences(text: str) -> str:
    return strip_markdown_fences(text)


def _extract_json_payload(text: str) -> str | None:
    return extract_json_payload(text)


def _parse_subtasks_from_llm_response(response: str, default_priority: str = "Medium") -> list[dict]:
    return parse_subtasks_from_llm_response(response, default_priority=default_priority)


def _parse_followup_analysis(raw_response: str, default_priority: str = "Medium") -> dict:
    return parse_followup_analysis(raw_response, default_priority=default_priority)


def _match_goal_template(goal: str) -> Optional[list[dict]]:
    return match_goal_template(goal)


def _try_load_repo_context(goal: str) -> Optional[str]:
    return try_load_repo_context(goal)


def _build_planning_prompt(goal: str, context: Optional[str] = None, max_tasks: int = 8) -> str:
    return build_planning_prompt(goal, context=context, max_tasks=max_tasks)


def _build_followup_prompt(completed_task: dict, output: str, exit_code: Optional[int]) -> str:
    status_str = "erfolgreich" if exit_code in (None, 0) else f"fehlgeschlagen (exit code: {exit_code})"
    prompt = f"""Du analysierst ein abgeschlossenes Aufgabe auf Folgeaufgaben.

ABGESCHLOSSENE AUFGABE:
Titel: {completed_task.get("title", "N/A")}
Beschreibung: {completed_task.get("description", "N/A")}
Status: {status_str}

AUSGABE DES TASKS:
{output[:2000] if output else "(keine Ausgabe)"}

AUFGABE:
Pruefe ob:
1. Die Aufgabe wirklich abgeschlossen ist oder Nacharbeiten benoetigt
2. Natuerliche Folgeaufgaben entstehen (z.B. Tests schreiben nach Implementierung)
3. Fehler behoben werden muessen (falls exit_code != 0)

AUSGABEFORMAT (nur JSON):
{{
  "task_complete": true|false,
  "needs_review": true|false,
  "followup_tasks": [
    {{"title": "...", "description": "...", "priority": "Medium"}}
  ],
  "suggestions": ["Optionale Verbesserungen"]
}}
"""
    return prompt


def _sanitize_input(text: str, max_length: int = 4000) -> str:
    return sanitize_input(text, max_length=max_length)


def _validate_goal(goal: str) -> tuple[bool, str]:
    return validate_goal(goal)


class AutoPlanner:
    def __init__(self):
        self._lock = threading.RLock()
        self.enabled = False
        self.auto_followup_enabled = True
        self.max_subtasks_per_goal = 10
        self.default_priority = "Medium"
        self.auto_start_autopilot = True
        self.llm_timeout = 30
        self.llm_retry_attempts = 2
        self.llm_retry_backoff = 1.0
        self._stats = {
            "goals_processed": 0,
            "tasks_created": 0,
            "followups_created": 0,
            "errors": 0,
            "llm_retries": 0,
        }

    def _persist_state(self):
        state = {
            "enabled": self.enabled,
            "auto_followup_enabled": self.auto_followup_enabled,
            "max_subtasks_per_goal": self.max_subtasks_per_goal,
            "default_priority": self.default_priority,
            "auto_start_autopilot": self.auto_start_autopilot,
            "llm_timeout": self.llm_timeout,
            "stats": self._stats,
        }
        config_repo.save(ConfigDB(key=AUTO_PLANNER_STATE_KEY, value_json=json.dumps(state)))

    def _is_timeout_error(self, exc: Exception) -> bool:
        error_name = type(exc).__name__.lower()
        error_msg = str(exc).lower()
        return any(t in error_name or t in error_msg for t in ["timeout", "timedout", "timed out"])

    def _call_llm_with_retry(self, prompt: str, llm_config: dict) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.llm_retry_attempts + 1):
            try:
                raw_response = generate_text(
                    prompt=prompt,
                    provider=llm_config.get("provider"),
                    model=llm_config.get("model"),
                    base_url=llm_config.get("base_url"),
                    api_key=llm_config.get("api_key"),
                    timeout=self.llm_timeout,
                )
                if not isinstance(raw_response, str):
                    raw_response = str(raw_response or "")
                return raw_response
            except Exception as e:
                last_exc = e
                is_timeout = self._is_timeout_error(e)
                if is_timeout and attempt < self.llm_retry_attempts:
                    self._stats["llm_retries"] += 1
                    backoff = self.llm_retry_backoff * attempt
                    _log().warning(
                        "LLM timeout (attempt %s/%s), retrying in %ss: %s",
                        attempt,
                        self.llm_retry_attempts,
                        backoff,
                        e,
                    )
                    time.sleep(backoff)
                elif not is_timeout:
                    break
        if last_exc:
            raise with_error_context(
                PlanningError(
                    "auto_planner_llm_failed",
                    details={"timeout_seconds": self.llm_timeout, "attempts": self.llm_retry_attempts},
                ),
                cause=str(last_exc),
            )
        raise PlanningError("auto_planner_llm_failed", details={"timeout_seconds": self.llm_timeout})

    def restore(self):
        cfg = config_repo.get_by_key(AUTO_PLANNER_STATE_KEY)
        if not cfg:
            return
        try:
            data = json.loads(cfg.value_json or "{}")
            self.enabled = bool(data.get("enabled", False))
            self.auto_followup_enabled = bool(data.get("auto_followup_enabled", True))
            self.max_subtasks_per_goal = int(data.get("max_subtasks_per_goal", 10))
            self.default_priority = str(data.get("default_priority", "Medium"))
            self.auto_start_autopilot = bool(data.get("auto_start_autopilot", True))
            self.llm_timeout = int(data.get("llm_timeout", 30))
            if isinstance(data.get("stats"), dict):
                self._stats = data["stats"]
        except Exception as e:
            _log().warning("Could not restore auto planner state: %s", e)

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "auto_followup_enabled": self.auto_followup_enabled,
                "max_subtasks_per_goal": self.max_subtasks_per_goal,
                "default_priority": self.default_priority,
                "auto_start_autopilot": self.auto_start_autopilot,
                "llm_timeout": self.llm_timeout,
                "llm_retry_attempts": self.llm_retry_attempts,
                "llm_retry_backoff": self.llm_retry_backoff,
                "stats": dict(self._stats),
            }

    def configure(
        self,
        enabled: Optional[bool] = None,
        auto_followup_enabled: Optional[bool] = None,
        max_subtasks_per_goal: Optional[int] = None,
        default_priority: Optional[str] = None,
        auto_start_autopilot: Optional[bool] = None,
        llm_timeout: Optional[int] = None,
        llm_retry_attempts: Optional[int] = None,
        llm_retry_backoff: Optional[float] = None,
    ) -> dict:
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
            if auto_followup_enabled is not None:
                self.auto_followup_enabled = bool(auto_followup_enabled)
            if max_subtasks_per_goal is not None:
                self.max_subtasks_per_goal = max(1, min(int(max_subtasks_per_goal), 20))
            if default_priority is not None:
                self.default_priority = str(default_priority)
            if auto_start_autopilot is not None:
                self.auto_start_autopilot = bool(auto_start_autopilot)
            if llm_timeout is not None:
                self.llm_timeout = max(5, min(int(llm_timeout), 120))
            if llm_retry_attempts is not None:
                self.llm_retry_attempts = max(1, min(int(llm_retry_attempts), 5))
            if llm_retry_backoff is not None:
                self.llm_retry_backoff = max(0.1, min(float(llm_retry_backoff), 10.0))
            self._persist_state()
            return self.status()

    def plan_goal(
        self,
        goal: str,
        context: Optional[str] = None,
        team_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        create_tasks: bool = True,
        use_template: bool = True,
        use_repo_context: bool = True,
        goal_id: Optional[str] = None,
        goal_trace_id: Optional[str] = None,
    ) -> dict:
        """
        Analysiert ein Goal und generiert Subtasks.

        Returns:
            dict mit 'subtasks' (Liste der generierten Tasks) und 'created_task_ids'
        """
        result = get_planning_service().plan_goal(
            planner=self,
            goal=goal,
            context=context,
            team_id=team_id,
            parent_task_id=parent_task_id,
            create_tasks=create_tasks,
            use_template=use_template,
            use_repo_context=use_repo_context,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
        )

        created_ids = list(result.get("created_task_ids") or [])

        if self.auto_start_autopilot and created_ids:
            self._ensure_autopilot_running()

        log_audit(
            "auto_planner_goal_processed",
            {
                "goal_preview": goal[:100],
                "goal_id": goal_id,
                "trace_id": goal_trace_id,
                "plan_id": result.get("plan_id"),
                "subtask_count": len(result.get("subtasks") or []),
                "created_count": len(created_ids),
                "team_id": team_id,
            },
        )

        return result

    def analyze_and_create_followups(
        self,
        task_id: str,
        output: Optional[str] = None,
        exit_code: Optional[int] = None,
    ) -> dict:
        """
        Analysiert einen abgeschlossenen Task und erstellt ggf. Folgeaufgaben.
        """
        if not self.auto_followup_enabled:
            return {"followups_created": [], "analysis": None, "skipped": "auto_followup_disabled"}

        task = _repos().task_repo.get_by_id(task_id)
        if not task:
            return {"followups_created": [], "analysis": None, "error": "task_not_found"}

        task_dict = task.model_dump()
        prompt = _build_followup_prompt(task_dict, output or task.last_output or "", exit_code)

        try:
            llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
            raw_response = self._call_llm_with_retry(prompt, llm_config)
        except Exception as e:
            _log().error("LLM call failed for followup analysis: %s", e)
            self._stats["errors"] += 1
            return {"followups_created": [], "analysis": None, "error": str(e)}

        analysis = _parse_followup_analysis(raw_response, default_priority=self.default_priority)

        created_followups = []
        followup_tasks = analysis.get("followup_tasks", [])

        for ft in followup_tasks[:5]:
            followup_id = _generate_task_id("followup")
            title = str(ft.get("title") or "")[:200]
            description = str(ft.get("description") or ft.get("title") or "")[:2000]
            priority = str(ft.get("priority") or self.default_priority)

            from agent.routes.tasks.utils import _update_local_task_status

            _update_local_task_status(
                followup_id,
                "todo",
                title=title,
                description=description,
                priority=priority,
                parent_task_id=task_id,
                source_task_id=task_id,
                derivation_reason="followup_llm",
                derivation_depth=int(task_dict.get("derivation_depth") or 0) + 1,
                team_id=task_dict.get("team_id"),
            )

            created_followups.append(
                {
                    "id": followup_id,
                    "title": title,
                    "priority": priority,
                }
            )
            self._stats["followups_created"] += 1

        if created_followups:
            self._persist_state()
            self._ensure_autopilot_running()

        log_audit(
            "auto_planner_followups_created",
            {
                "parent_task_id": task_id,
                "followup_count": len(created_followups),
                "task_complete": analysis.get("task_complete"),
            },
        )

        return {
            "followups_created": created_followups,
            "analysis": analysis,
        }

    def _ensure_autopilot_running(self):
        try:
            from agent.routes.tasks.autopilot import autonomous_loop

            if not autonomous_loop.running:
                active_team = next((t for t in _repos().team_repo.get_all() if t.is_active), None)
                autonomous_loop.start(
                    interval_seconds=20,
                    max_concurrency=2,
                    team_id=active_team.id if active_team else None,
                    security_level="safe",
                    persist=True,
                    background=not _background_threads_disabled(),
                )
                _log().info("Auto-Planner started autopilot automatically")
        except Exception as e:
            _log().warning("Could not start autopilot: %s", e)


auto_planner = AutoPlanner()


def init_auto_planner():
    try:
        auto_planner.restore()
    except Exception as e:
        _log().warning("Auto planner restore failed: %s", e)


@auto_planner_bp.route("/tasks/auto-planner/status", methods=["GET"])
@check_auth
def auto_planner_status():
    return api_response(data=get_core_services().auto_planner_runtime_service.status(auto_planner))


@auto_planner_bp.route("/tasks/auto-planner/configure", methods=["POST"])
@check_auth
@admin_required
def auto_planner_configure():
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerConfigureRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="invalid_payload", code=400)
    new_config = get_core_services().auto_planner_runtime_service.configure(
        planner=auto_planner,
        data=payload.model_dump(exclude_none=True),
    )
    return api_response(data=new_config)


@auto_planner_bp.route("/tasks/auto-planner/plan", methods=["POST"])
@check_auth
def plan_goal_endpoint():
    """
    Analysiert ein Goal und erstellt automatisch Subtasks.

    Request Body:
    {
        "goal": "Beschreibung des Ziels",
        "context": "Optionaler Kontext",
        "team_id": "Optional: Team-ID",
        "parent_task_id": "Optional: Parent-Task-ID",
        "create_tasks": true
    }
    """
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerPlanRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="goal_required", code=400)
    result = get_core_services().auto_planner_runtime_service.plan_goal(
        planner=auto_planner,
        data=payload.model_dump(exclude_none=True),
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"], code=result.get("code", 201))


@auto_planner_bp.route("/tasks/auto-planner/analyze/<task_id>", methods=["POST"])
@check_auth
def analyze_task_for_followups(task_id):
    """
    Analysiert einen abgeschlossenen Task auf Folgeaufgaben.

    Request Body (optional):
    {
        "output": "Ueberschreibt die Task-Ausgabe",
        "exit_code": 0
    }
    """
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerAnalyzeRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="invalid_payload", code=400)
    result = get_core_services().auto_planner_runtime_service.analyze_task_for_followups(
        planner=auto_planner,
        task_id=task_id,
        data=payload.model_dump(exclude_none=True),
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])

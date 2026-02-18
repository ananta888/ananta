"""
Auto-Planner: Goal-basierte Task-Generierung und Output-Analyse.

Dieses Modul ermoeglicht:
1. Aus einem High-Level-Goal automatisch Subtasks zu generieren
2. Nach Task-Abschluss auf Folgeaufgaben zu pruefen
3. Neue Tasks automatisch zu erkennen und anzulegen
"""

import json
import logging
import threading
import time
import uuid
from typing import Any, Optional

from flask import Blueprint, request, current_app

from agent.auth import check_auth, admin_required
from agent.common.errors import api_response
from agent.common.audit import log_audit
from agent.config import settings
from agent.db_models import ConfigDB
from agent.repository import task_repo, config_repo, team_repo
from agent.routes.tasks.management import _normalize_depends_on, _validate_dependencies_and_cycles
from agent.routes.tasks.status import normalize_task_status
from agent.llm_integration import generate_text

auto_planner_bp = Blueprint("tasks_auto_planner", __name__)

AUTO_PLANNER_STATE_KEY = "auto_planner_state"


def _generate_task_id(prefix: str = "auto") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _parse_subtasks_from_llm_response(response: str) -> list[dict]:
    cleaned = response.strip()
    try:
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[-1].startswith("```"):
                cleaned = "\n".join(lines[1:-1])
            else:
                cleaned = "\n".join(lines[1:])
        return json.loads(cleaned)
    except json.JSONDecodeError:
        tasks = []
        for line in cleaned.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                desc = line.lstrip("-*1234567890. ").strip()
                if desc:
                    tasks.append({"description": desc, "priority": "Medium"})
        return tasks


def _build_planning_prompt(goal: str, context: Optional[str] = None, max_tasks: int = 8) -> str:
    prompt = f"""Du bist ein Projektplanungs-Assistent. Analysiere das folgende Ziel und zerlege es in konkrete, ausfuehrbare Teilaufgaben.

ZIEL:
{goal}

ANFORDERUNGEN:
1. Erstelle {max_tasks} oder weniger Teilaufgaben
2. Jede Teilaufgabe soll konkret und ausfuehrbar sein
3. Priorisiere nach Abhaengigkeiten (was muss zuerst erledigt werden)
4. Verwende diese Prioritaeten: High, Medium, Low

AUSGABEFORMAT (nur JSON, keine Erklaerung):
[
  {{"title": "Kurzer Titel", "description": "Detaillierte Beschreibung der Aufgabe", "priority": "High|Medium|Low", "depends_on": []}},
  ...
]
"""
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


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


class AutoPlanner:
    def __init__(self):
        self._lock = threading.Lock()
        self.enabled = False
        self.auto_followup_enabled = True
        self.max_subtasks_per_goal = 10
        self.default_priority = "Medium"
        self.auto_start_autopilot = True
        self.llm_timeout = 30
        self._stats = {
            "goals_processed": 0,
            "tasks_created": 0,
            "followups_created": 0,
            "errors": 0,
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
            logging.warning(f"Could not restore auto planner state: {e}")

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "auto_followup_enabled": self.auto_followup_enabled,
                "max_subtasks_per_goal": self.max_subtasks_per_goal,
                "default_priority": self.default_priority,
                "auto_start_autopilot": self.auto_start_autopilot,
                "llm_timeout": self.llm_timeout,
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
            self._persist_state()
            return self.status()

    def plan_goal(
        self,
        goal: str,
        context: Optional[str] = None,
        team_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        create_tasks: bool = True,
    ) -> dict:
        """
        Analysiert ein Goal und generiert Subtasks.

        Returns:
            dict mit 'subtasks' (Liste der generierten Tasks) und 'created_task_ids'
        """
        if not goal or not goal.strip():
            return {"subtasks": [], "created_task_ids": [], "error": "goal_required"}

        prompt = _build_planning_prompt(goal, context, self.max_subtasks_per_goal)

        try:
            llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
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
        except Exception as e:
            logging.error(f"LLM call failed for goal planning: {e}")
            self._stats["errors"] += 1
            return {"subtasks": [], "created_task_ids": [], "error": str(e)}

        subtasks = _parse_subtasks_from_llm_response(raw_response)

        if not subtasks:
            logging.warning(f"No subtasks parsed from LLM response for goal: {goal[:100]}")
            return {"subtasks": [], "created_task_ids": [], "raw_response": raw_response}

        created_ids = []
        depends_on_previous: list[str] = []

        if create_tasks:
            for i, st in enumerate(subtasks[: self.max_subtasks_per_goal]):
                task_id = _generate_task_id("goal")
                title = str(st.get("title") or "")[:200] or f"Subtask {i + 1}"
                description = str(st.get("description") or st.get("title") or "")[:2000]
                priority = str(st.get("priority") or self.default_priority)

                task_depends_on = []
                if st.get("depends_on"):
                    task_depends_on = _normalize_depends_on(st.get("depends_on"), task_id)
                elif depends_on_previous and i > 0:
                    task_depends_on = depends_on_previous[-1:]

                ok, reason = _validate_dependencies_and_cycles(task_id, task_depends_on)
                if not ok:
                    logging.warning(f"Skipping task with invalid deps: {reason}")
                    continue

                task_data = {
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "team_id": team_id,
                    "parent_task_id": parent_task_id,
                    "depends_on": task_depends_on if task_depends_on else None,
                }

                from agent.routes.tasks.utils import _update_local_task_status

                _update_local_task_status(task_id, "todo", **task_data)

                created_ids.append(task_id)
                depends_on_previous.append(task_id)
                self._stats["tasks_created"] += 1

            self._stats["goals_processed"] += 1
            self._persist_state()

            if self.auto_start_autopilot and created_ids:
                self._ensure_autopilot_running()

        log_audit(
            "auto_planner_goal_processed",
            {
                "goal_preview": goal[:100],
                "subtask_count": len(subtasks),
                "created_count": len(created_ids),
                "team_id": team_id,
            },
        )

        return {
            "subtasks": subtasks,
            "created_task_ids": created_ids,
            "raw_response": raw_response if not create_tasks else None,
        }

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

        task = task_repo.get_by_id(task_id)
        if not task:
            return {"followups_created": [], "analysis": None, "error": "task_not_found"}

        task_dict = task.model_dump()
        prompt = _build_followup_prompt(task_dict, output or task.last_output or "", exit_code)

        try:
            llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
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
        except Exception as e:
            logging.error(f"LLM call failed for followup analysis: {e}")
            self._stats["errors"] += 1
            return {"followups_created": [], "analysis": None, "error": str(e)}

        try:
            analysis = json.loads(raw_response.strip())
        except json.JSONDecodeError:
            analysis = {"task_complete": True, "followup_tasks": [], "parse_error": True}

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
                active_team = next((t for t in team_repo.get_all() if t.is_active), None)
                autonomous_loop.start(
                    interval_seconds=20,
                    max_concurrency=2,
                    team_id=active_team.id if active_team else None,
                    security_level="safe",
                    persist=True,
                )
                logging.info("Auto-Planner started autopilot automatically")
        except Exception as e:
            logging.warning(f"Could not start autopilot: {e}")


auto_planner = AutoPlanner()


def init_auto_planner():
    try:
        auto_planner.restore()
    except Exception as e:
        logging.warning(f"Auto planner restore failed: {e}")


@auto_planner_bp.route("/tasks/auto-planner/status", methods=["GET"])
@check_auth
def auto_planner_status():
    return api_response(data=auto_planner.status())


@auto_planner_bp.route("/tasks/auto-planner/configure", methods=["POST"])
@check_auth
@admin_required
def auto_planner_configure():
    data = request.get_json(silent=True) or {}
    new_config = auto_planner.configure(
        enabled=data.get("enabled"),
        auto_followup_enabled=data.get("auto_followup_enabled"),
        max_subtasks_per_goal=data.get("max_subtasks_per_goal"),
        default_priority=data.get("default_priority"),
        auto_start_autopilot=data.get("auto_start_autopilot"),
        llm_timeout=data.get("llm_timeout"),
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
    goal = str(data.get("goal") or "").strip()

    if not goal:
        return api_response(status="error", message="goal_required", code=400)

    result = auto_planner.plan_goal(
        goal=goal,
        context=data.get("context"),
        team_id=data.get("team_id"),
        parent_task_id=data.get("parent_task_id"),
        create_tasks=bool(data.get("create_tasks", True)),
    )

    if result.get("error"):
        return api_response(status="error", message=result["error"], code=400)

    return api_response(data=result, code=201)


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
    result = auto_planner.analyze_and_create_followups(
        task_id=task_id,
        output=data.get("output"),
        exit_code=data.get("exit_code"),
    )

    if result.get("error"):
        return api_response(status="error", message=result["error"], code=400)

    return api_response(data=result)

import copy
from typing import Any, List

from flask import current_app

from agent.config import settings
from agent.db_models import GoalDB
from agent.services.planning_service import get_goal_feature_flags, get_plan_generation_limits, get_planning_service
from agent.services.planning_utils import GOAL_TEMPLATES
from agent.services.cost_aggregation_service import get_cost_aggregation_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry


class GoalService:
    def deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self.deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def flatten_dict(self, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in (data or {}).items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flat.update(self.flatten_dict(value, path))
            else:
                flat[path] = value
        return flat

    def build_provenance(self, defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, str]:
        provenance = {key: "default" for key in self.flatten_dict(defaults)}
        for key in self.flatten_dict(overrides):
            provenance[key] = "override"
        return provenance

    def default_workflow_config(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        plan_limits = get_plan_generation_limits()
        planning_defaults = {
            "engine": "auto_planner",
            "create_tasks": True,
            "use_template": True,
            "use_repo_context": True,
            "max_subtasks_per_goal": 8,
            "max_plan_nodes": plan_limits["max_plan_nodes"],
            "max_plan_depth": plan_limits["max_plan_depth"],
        }
        routing_defaults = {
            "mode": "active_team_or_hub_default",
            "team_id": None,
            "worker_selection": "current_assignment_flow",
        }
        verification_defaults = {
            "mode": "existing_quality_gates",
            "enabled": bool((agent_cfg.get("quality_gates") or {}).get("enabled", True)),
        }
        artifact_defaults = {
            "result_view": "task_summary",
            "include_task_tree": True,
        }
        policy_defaults = {
            "mode": "hub_enforced",
            "security_level": "safe_defaults",
        }
        return {
            "planning": planning_defaults,
            "routing": routing_defaults,
            "verification": verification_defaults,
            "artifacts": artifact_defaults,
            "policy": policy_defaults,
        }

    def build_goal_workflow_overrides(self, payload: Any) -> dict[str, Any]:
        overrides = copy.deepcopy(payload.workflow or {})
        overrides = self.deep_merge(overrides, self.build_mode_workflow_defaults(str(payload.mode or "generic"), payload.mode_data or {}))
        if payload.team_id:
            overrides.setdefault("routing", {})["team_id"] = payload.team_id
        if payload.create_tasks is not None:
            overrides.setdefault("planning", {})["create_tasks"] = bool(payload.create_tasks)
        if payload.use_template is not None:
            overrides.setdefault("planning", {})["use_template"] = bool(payload.use_template)
        if payload.use_repo_context is not None:
            overrides.setdefault("planning", {})["use_repo_context"] = bool(payload.use_repo_context)
        return overrides

    def build_mode_workflow_defaults(self, mode: str, mode_data: dict[str, Any]) -> dict[str, Any]:
        if mode == "new_software_project":
            return {
                "planning": {
                    "create_tasks": True,
                    "use_template": True,
                    "use_repo_context": False,
                },
                "verification": {
                    "enabled": True,
                    "review_required": True,
                    "mode": "reviewable_project_start",
                },
                "policy": {
                    "security_level": "review_required",
                    "write_access": "confirmation_required",
                    "runtime_execution": "confirmation_required",
                    "automation": "no_uncontrolled_full_auto",
                },
            }
        if mode == "project_evolution":
            risk_level = str(mode_data.get("risk_level") or "mittel").strip().lower()
            security_level = "strict_review" if risk_level == "hoch" else "review_required"
            return {
                "planning": {
                    "create_tasks": True,
                    "use_template": True,
                    "use_repo_context": True,
                },
                "verification": {
                    "enabled": True,
                    "review_required": True,
                    "mode": "risk_and_regression_review",
                },
                "artifacts": {
                    "include_risk_view": True,
                    "include_test_plan": True,
                    "include_diff_scope": True,
                },
                "policy": {
                    "security_level": security_level,
                    "write_access": "confirmation_required",
                    "large_change_handling": "split_into_reviewable_steps",
                    "runtime_execution": "confirmation_required",
                },
            }
        return {}

    def build_mode_context(self, mode: str, mode_data: dict[str, Any], context: str | None) -> str | None:
        parts = [str(context or "").strip()] if str(context or "").strip() else []
        if mode == "new_software_project":
            parts.append(
                "\n".join(
                    [
                        "MODUSKONTEXT: Neues Softwareprojekt anlegen.",
                        "Nutze sichere Start-Defaults: erst planen, dann reviewen, keine unkontrollierte Vollautomatik.",
                        "Erzeuge Scope, Architekturvorschlag, initiales Backlog, Tests und naechste reviewbare Schritte.",
                    ]
                )
            )
        elif mode == "project_evolution":
            affected_areas = str(mode_data.get("affected_areas") or "").strip()
            constraints = str(mode_data.get("constraints") or "").strip()
            risk_level = str(mode_data.get("risk_level") or "mittel").strip()
            change_type = str(mode_data.get("change_type") or "kleine_erweiterung").strip()
            lines = [
                "MODUSKONTEXT: Existierendes Softwareprojekt weiterentwickeln.",
                "Nutze bestehendes Repo-, Artifact- und Task-Wissen als Startkontext, aber bevorzuge relevante Bereiche gegenueber langer Rohhistorie.",
                f"Weiterentwicklungsart: {change_type}.",
                f"Risikoniveau: {risk_level}.",
                "Zerlege die Aenderung in kleine verifizierbare Schritte mit betroffenen Bereichen, Risiken, Tests und Review-Hinweisen.",
            ]
            if affected_areas:
                lines.append(f"Betroffene Bereiche: {affected_areas}.")
            if constraints:
                lines.append(f"Restriktionen: {constraints}.")
            parts.append("\n".join(lines))
        return "\n\n".join(parts) if parts else None

    def build_mode_constraints(self, mode: str, mode_data: dict[str, Any]) -> list[str]:
        if mode == "new_software_project":
            constraints = [
                "Keine unkontrollierte Vollautomatik beim Projektstart.",
                "Schreib- und Runtime-Schritte bleiben bestaetigungspflichtig.",
            ]
            non_goals = str(mode_data.get("non_goals") or "").strip()
            if non_goals:
                constraints.append(f"Nicht-Ziele: {non_goals}")
            return constraints
        if mode == "project_evolution":
            constraints = [
                "Keine monolithische Aenderung als Standard.",
                "Grosse oder riskante Aenderungen in reviewbare Schritte zerlegen.",
            ]
            user_constraints = str(mode_data.get("constraints") or "").strip()
            if user_constraints:
                constraints.append(user_constraints)
            return constraints
        return []

    def build_mode_acceptance_criteria(self, mode: str) -> list[str]:
        if mode == "new_software_project":
            return [
                "Projekt-Blueprint mit Scope, Architekturvorschlag und initialem Backlog ist sichtbar.",
                "Review- und Verification-Schritte bleiben aktiv.",
            ]
        if mode == "project_evolution":
            return [
                "Aenderungsplan enthaelt betroffene Bereiche, Risiken, Tests und Review-Hinweise.",
                "Naechste Schritte sind klein und einzeln verifizierbar.",
            ]
        return []

    def goal_readiness(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        llm_cfg = agent_cfg.get("llm_config", {}) or {}
        repos = get_repository_registry()
        workers = repos.agent_repo.get_all()
        active_team = next((team for team in repos.team_repo.get_all() if team.is_active), None)
        local_worker_available = bool(settings.role == "worker" or getattr(settings, "hub_can_be_worker", False))
        worker_available = bool(workers) or local_worker_available
        planning_provider_available = bool(str(llm_cfg.get("provider") or "").strip())
        planning_template_available = bool(GOAL_TEMPLATES)
        planning_available = bool(planning_provider_available or planning_template_available)
        degraded_hints: list[str] = []

        if not workers and local_worker_available:
            degraded_hints.append("no_remote_workers_registered_using_local_worker_fallback")
        if not active_team:
            degraded_hints.append("no_active_team_default_routing_will_use_existing_assignment_flow")
        if not planning_provider_available:
            degraded_hints.append("llm_provider_not_configured_template_planning_only")

        return {
            "happy_path_ready": bool(worker_available and planning_available),
            "planning_available": planning_available,
            "planning_provider_available": planning_provider_available,
            "planning_template_available": planning_template_available,
            "worker_available": worker_available,
            "active_team_id": active_team.id if active_team else None,
            "available_worker_count": len(workers),
            "degraded_hints": degraded_hints,
            "defaults": self.default_workflow_config(),
            "feature_flags": get_goal_feature_flags(),
        }

    def enforce_goal_preconditions(
        self,
        *,
        payload: Any,
        effective_workflow: dict[str, Any],
        readiness: dict[str, Any],
        is_admin: bool,
    ) -> str | None:
        planning_cfg = dict(effective_workflow.get("planning") or {})
        use_template = bool(planning_cfg.get("use_template", True))
        create_tasks = bool(planning_cfg.get("create_tasks", True))
        if create_tasks and not use_template and not bool(readiness.get("planning_provider_available")):
            return "planning_backend_unavailable"

        requested_policy_override = bool((payload.workflow or {}).get("policy"))
        if requested_policy_override and not is_admin:
            return "policy_override_requires_admin"
        return None

    def serialize_goal(self, goal: GoalDB) -> dict[str, Any]:
        repos = get_repository_registry()
        data = goal.model_dump()
        data["task_count"] = len(repos.task_repo.get_by_goal_id(goal.id))
        data["instruction_layers"] = get_instruction_layer_service().goal_selection_summary(data)
        return data

    def team_scope_allows(self, goal: GoalDB, user_payload: dict[str, Any] | None, is_admin: bool) -> bool:
        if not goal.team_id or is_admin:
            return True
        user_payload = user_payload or {}
        return bool(user_payload.get("team_id")) and str(user_payload.get("team_id")) == str(goal.team_id)

    def can_access_goal(self, goal: GoalDB | None, user_payload: dict[str, Any] | None, is_admin: bool) -> bool:
        if not goal:
            return False
        return self.team_scope_allows(goal, user_payload, is_admin)

    def sanitize_governance_summary(self, summary: dict[str, Any], is_admin: bool) -> dict[str, Any]:
        if is_admin:
            return summary
        return {
            "goal_id": summary.get("goal_id"),
            "trace_id": summary.get("trace_id"),
            "policy": {
                "total": (summary.get("policy") or {}).get("total", 0),
                "approved": (summary.get("policy") or {}).get("approved", 0),
                "blocked": (summary.get("policy") or {}).get("blocked", 0),
            },
            "verification": {
                "total": (summary.get("verification") or {}).get("total", 0),
                "passed": (summary.get("verification") or {}).get("passed", 0),
                "failed": (summary.get("verification") or {}).get("failed", 0),
                "escalated": (summary.get("verification") or {}).get("escalated", 0),
            },
            "cost_summary": {
                "goal_id": (summary.get("cost_summary") or {}).get("goal_id"),
                "task_count": (summary.get("cost_summary") or {}).get("task_count", 0),
                "tasks_with_cost": (summary.get("cost_summary") or {}).get("tasks_with_cost", 0),
                "tasks_without_cost": (summary.get("cost_summary") or {}).get("tasks_without_cost", 0),
                "total_cost_units": (summary.get("cost_summary") or {}).get("total_cost_units", 0.0),
                "total_tokens": (summary.get("cost_summary") or {}).get("total_tokens", 0),
                "total_latency_ms": (summary.get("cost_summary") or {}).get("total_latency_ms", 0),
                "currency": (summary.get("cost_summary") or {}).get("currency", "cost_units"),
            },
            "summary": {
                **dict(summary.get("summary") or {}),
                "governance_visible": False,
                "detail_level": "restricted",
            },
        }

    def build_artifact_summary(self, goal: GoalDB) -> dict[str, Any]:
        repos = get_repository_registry()
        tasks = repos.task_repo.get_by_goal_id(goal.id)
        plan, nodes = get_planning_service().get_latest_plan_for_goal(goal.id)
        verification_records = repos.verification_record_repo.get_by_goal_id(goal.id)
        memory_entries = repos.memory_entry_repo.get_by_goal(goal.id)
        task_outputs = [
            {
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "plan_node_id": task.plan_node_id,
                "preview": str(task.last_output or "")[:280],
                "trace_id": task.goal_trace_id,
            }
            for task in tasks
            if task.last_output
        ]
        latest_output = next((item for item in task_outputs if item.get("preview")), None)
        planned_artifacts = self.build_planned_artifacts(nodes)
        return {
            "goal_id": goal.id,
            "trace_id": goal.trace_id,
            "result_summary": {
                "status": goal.status,
                "task_count": len(tasks),
                "completed_tasks": len([task for task in tasks if task.status == "completed"]),
                "failed_tasks": len([task for task in tasks if task.status == "failed"]),
                "verification_passed": len([record for record in verification_records if record.status == "passed"]),
                "memory_entries": len(memory_entries),
                "cost_summary": get_cost_aggregation_service().aggregate_tasks(tasks),
            },
            "headline_artifact": latest_output,
            "artifacts": task_outputs[:10],
            "planned_artifacts": planned_artifacts,
            "reusable_artifacts": planned_artifacts,
            "memory_entries": [
                {
                    "id": entry.id,
                    "task_id": entry.task_id,
                    "title": entry.title,
                    "summary": entry.summary,
                    "trace_id": entry.trace_id,
                    "retrieval_tags": list(entry.retrieval_tags or []),
                }
                for entry in memory_entries[:10]
            ],
        }

    def build_planned_artifacts(self, nodes: list[Any]) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        for node in nodes or []:
            rationale = dict(node.rationale or {})
            artifact_key = str(rationale.get("artifact") or "").strip()
            if not artifact_key:
                continue
            planned.append(
                {
                    "artifact": artifact_key,
                    "title": node.title,
                    "description": node.description,
                    "plan_node_id": node.id,
                    "node_key": node.node_key,
                    "position": node.position,
                    "status": node.status,
                    "risk_focus": rationale.get("risk_focus"),
                    "test_focus": rationale.get("test_focus"),
                    "review_focus": rationale.get("review_focus"),
                    "reusable": True,
                }
            )
        return planned[:12]

    def goal_detail(self, goal: GoalDB, *, is_admin: bool) -> dict[str, Any]:
        repos = get_repository_registry()
        plan, nodes = get_planning_service().get_latest_plan_for_goal(goal.id)
        tasks = repos.task_repo.get_by_goal_id(goal.id)
        from agent.services.verification_service import get_verification_service

        governance = get_verification_service().governance_summary(goal.id, include_sensitive=is_admin)
        memory_entries = repos.memory_entry_repo.get_by_goal(goal.id)
        cost_summary = get_cost_aggregation_service().aggregate_goal_costs(goal.id)
        return {
            "goal": self.serialize_goal(goal),
            "trace": {
                "trace_id": goal.trace_id,
                "goal_id": goal.id,
                "plan_id": plan.id if plan else None,
                "task_ids": [task.id for task in tasks],
            },
            "artifacts": self.build_artifact_summary(goal),
            "cost_summary": cost_summary,
            "plan": {
                "plan": plan.model_dump() if plan else None,
                "nodes": [node.model_dump() for node in nodes],
            },
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                    "plan_node_id": task.plan_node_id,
                    "verification_status": dict(task.verification_status or {}),
                    "cost_summary": get_cost_aggregation_service().task_cost_summary(task),
                    "trace_id": task.goal_trace_id,
                }
                for task in tasks
            ],
            "governance": self.sanitize_governance_summary(governance, is_admin) if governance else None,
            "memory": [
                {
                    "id": entry.id,
                    "task_id": entry.task_id,
                    "title": entry.title,
                    "summary": entry.summary,
                    "content": entry.content if is_admin else None,
                    "artifact_refs": list(entry.artifact_refs or []),
                    "retrieval_tags": list(entry.retrieval_tags or []),
                    "trace_id": entry.trace_id,
                }
                for entry in memory_entries
            ],
        }

    def get_guided_modes(self) -> List[dict[str, Any]]:
        return [
            {
                "id": "code_fix",
                "title": "Codeproblem loesen",
                "description": "Ein spezifisches Codeproblem analysieren und beheben.",
                "icon": "code",
                "fields": [
                    {"name": "issue_description", "label": "Problembeschreibung", "type": "textarea", "required": True},
                    {"name": "affected_files", "label": "Betroffene Dateien (optional)", "type": "text", "required": False}
                ],
                "recommended_capabilities": ["file_read", "file_write", "file_patch"]
            },
            {
                "id": "repo_analysis",
                "title": "Projekt analysieren",
                "description": "Die Struktur und Qualitaet eines Projekts untersuchen.",
                "icon": "analytics",
                "fields": [
                    {"name": "scope", "label": "Analyseschwerpunkt", "type": "text", "placeholder": "z.B. Security, Architektur", "required": False}
                ],
                "recommended_capabilities": ["file_read"]
            },
            {
                "id": "doc_summary",
                "title": "Datei zusammenfassen",
                "description": "Den Inhalt einer oder mehrerer Dateien verständlich zusammenfassen.",
                "icon": "description",
                "fields": [
                    {"name": "files", "label": "Dateien", "type": "text", "required": True},
                    {"name": "detail_level", "label": "Detailgrad", "type": "select", "options": ["kurz", "mittel", "ausführlich"], "default": "mittel"}
                ],
                "recommended_capabilities": ["file_read"]
            },
            {
                "id": "sys_diag",
                "title": "Systemdiagnose",
                "description": "Laufzeitprobleme, Logs oder Container-Status prüfen.",
                "icon": "troubleshoot",
                "fields": [
                    {"name": "target", "label": "Diagnoseziel", "type": "text", "placeholder": "z.B. Docker Compose, App Log", "required": True}
                ],
                "recommended_capabilities": ["shell_exec"]
            },
            {
                "id": "docker_compose_repair",
                "title": "Docker-/Compose-Reparatur",
                "description": "Diagnostiziert Container-/Compose-Probleme und erstellt einen kontrollierten Repair-Plan.",
                "icon": "build",
                "fields": [
                    {"name": "issue_symptom", "label": "Symptom", "type": "textarea", "required": True},
                    {"name": "compose_file", "label": "Compose-Datei (optional)", "type": "text", "required": False},
                    {"name": "service", "label": "Service-Name (optional)", "type": "text", "required": False},
                ],
                "recommended_capabilities": ["shell_exec", "file_read", "file_patch"]
            },
            {
                "id": "new_software_project",
                "title": "Neues Softwareprojekt anlegen",
                "description": "Erstellt aus einer Projektidee einen kontrollierten Blueprint mit initialem Backlog.",
                "icon": "add_box",
                "fields": [
                    {"name": "project_idea", "label": "Projektidee", "type": "textarea", "required": True},
                    {"name": "target_users", "label": "Zielgruppe", "type": "text", "required": True},
                    {"name": "platform", "label": "Plattform", "type": "text", "placeholder": "z.B. Web, CLI, API", "required": True},
                    {"name": "preferred_stack", "label": "Bevorzugter Stack (optional)", "type": "text", "required": False},
                    {"name": "non_goals", "label": "Nicht-Ziele (optional)", "type": "textarea", "required": False},
                ],
                "recommended_capabilities": ["file_read", "file_write", "file_patch"]
            },
            {
                "id": "project_evolution",
                "title": "Existierendes Projekt weiterentwickeln",
                "description": "Plant aktive Weiterentwicklung mit kleinen, reviewbaren Aenderungen statt reiner Repository-Analyse.",
                "icon": "upgrade",
                "fields": [
                    {"name": "change_goal", "label": "Zielaenderung", "type": "textarea", "required": True},
                    {
                        "name": "change_type",
                        "label": "Weiterentwicklungsart",
                        "type": "select",
                        "options": ["kleine_erweiterung", "refactoring", "feature_ausbau", "technische_verbesserung"],
                        "default": "kleine_erweiterung",
                        "required": True,
                    },
                    {"name": "affected_areas", "label": "Betroffene Bereiche (optional)", "type": "text", "required": False},
                    {
                        "name": "risk_level",
                        "label": "Risikoniveau",
                        "type": "select",
                        "options": ["niedrig", "mittel", "hoch"],
                        "default": "mittel",
                        "required": True,
                    },
                    {"name": "constraints", "label": "Restriktionen (optional)", "type": "textarea", "required": False},
                ],
                "recommended_capabilities": ["file_read", "git_diff", "file_patch"]
            },
            {
                "id": "runtime_repair",
                "title": "Laufzeit-Reparatur",
                "description": "Analysiert Runtime-Ausfaelle und erstellt reproduzierbare, reviewbare Reparaturschritte.",
                "icon": "medical_services",
                "fields": [
                    {"name": "runtime_target", "label": "Runtime-Ziel", "type": "text", "required": True},
                    {"name": "error_signal", "label": "Fehlersignal", "type": "textarea", "required": True},
                    {"name": "log_paths", "label": "Log-Pfade (optional)", "type": "text", "required": False},
                ],
                "recommended_capabilities": ["shell_exec", "file_read", "file_patch"]
            },
            {
                "id": "doc_gen",
                "title": "Dokumentation erstellen",
                "description": "Erzeugt technische Dokumentation, READMEs oder Architekturuebersichten.",
                "icon": "auto_stories",
                "fields": [
                    {"name": "topic", "label": "Thema / Scope", "type": "text", "required": True},
                    {"name": "format", "label": "Format", "type": "select", "options": ["Markdown", "HTML", "Plain Text"], "default": "Markdown"}
                ],
                "recommended_capabilities": ["file_read", "file_write"]
            },
            {
                "id": "code_review",
                "title": "Code Review durchfuehren",
                "description": "Analysiert Code oder Diffs auf Probleme und schlaegt Verbesserungen vor.",
                "icon": "rate_review",
                "fields": [
                    {"name": "scope", "label": "Review Scope", "type": "text", "placeholder": "z.B. letzte Commits, spezifische Datei", "required": True}
                ],
                "recommended_capabilities": ["git_diff", "file_read"]
            }
        ]

    def build_goal_from_mode(self, mode: str, mode_data: dict[str, Any]) -> str:
        if mode == "code_fix":
            desc = mode_data.get("issue_description", "")
            files = mode_data.get("affected_files", "")
            goal = f"Behebe folgendes Codeproblem: {desc}"
            if files:
                goal += f" In den Dateien: {files}"
            return goal
        if mode == "repo_analysis":
            scope = mode_data.get("scope") or "allgemeine Struktur"
            return f"Analysiere das Repository mit Schwerpunkt auf: {scope}"
        if mode == "doc_summary":
            files = mode_data.get("files", "")
            level = mode_data.get("detail_level", "mittel")
            return f"Fasse folgende Dateien {level} zusammen: {files}"
        if mode == "sys_diag":
            target = mode_data.get("target", "")
            return f"Fuehre eine Systemdiagnose für {target} durch und identifiziere Probleme."
        if mode == "docker_compose_repair":
            symptom = str(mode_data.get("issue_symptom", "")).strip()
            compose_file = str(mode_data.get("compose_file", "")).strip()
            service = str(mode_data.get("service", "")).strip()
            goal = (
                f"Diagnostiziere und repariere kontrolliert ein Docker-/Compose-Problem: {symptom}. "
                "Erstelle reproduzierbare Analyse-Schritte, konkrete Repair-Vorschlaege und klare Verifikationskriterien."
            )
            if compose_file:
                goal += f" Nutze bevorzugt die Compose-Datei: {compose_file}."
            if service:
                goal += f" Fokus-Service: {service}."
            return goal
        if mode == "new_software_project":
            project_idea = str(mode_data.get("project_idea", "")).strip()
            target_users = str(mode_data.get("target_users", "")).strip()
            platform = str(mode_data.get("platform", "")).strip()
            preferred_stack = str(mode_data.get("preferred_stack", "")).strip()
            non_goals = str(mode_data.get("non_goals", "")).strip()
            goal = (
                f"Lege ein neues Softwareprojekt an: {project_idea}. "
                f"Zielgruppe: {target_users or 'noch zu klaeren'}. Plattform: {platform or 'noch zu klaeren'}. "
                "Erstelle einen reviewbaren Projekt-Blueprint mit Scope, Architekturvorschlag, "
                "initialem Backlog, Tests und sicheren naechsten Schritten."
            )
            if preferred_stack:
                goal += f" Bevorzugter Stack: {preferred_stack}."
            if non_goals:
                goal += f" Nicht-Ziele: {non_goals}."
            return goal
        if mode == "project_evolution":
            change_goal = str(mode_data.get("change_goal", "")).strip()
            change_type = str(mode_data.get("change_type", "kleine_erweiterung")).strip()
            affected_areas = str(mode_data.get("affected_areas", "")).strip()
            risk_level = str(mode_data.get("risk_level", "mittel")).strip()
            constraints = str(mode_data.get("constraints", "")).strip()
            goal = (
                f"Plane eine kontrollierte Weiterentwicklung eines bestehenden Projekts: {change_goal}. "
                f"Art der Weiterentwicklung: {change_type}. Risikoniveau: {risk_level}. "
                "Zerlege die Aenderung in kleine verifizierbare Schritte mit Risikoanalyse, betroffenen Tests "
                "und Review-Plan."
            )
            if affected_areas:
                goal += f" Betroffene Bereiche: {affected_areas}."
            if constraints:
                goal += f" Restriktionen: {constraints}."
            return goal
        if mode == "runtime_repair":
            runtime_target = str(mode_data.get("runtime_target", "")).strip()
            error_signal = str(mode_data.get("error_signal", "")).strip()
            log_paths = str(mode_data.get("log_paths", "")).strip()
            goal = (
                f"Untersuche eine Laufzeitstoerung fuer {runtime_target}: {error_signal}. "
                "Leite daraus reviewbare Reparaturschritte und Folge-Tasks mit Risikohinweisen ab."
            )
            if log_paths:
                goal += f" Beruecksichtige explizit diese Logs: {log_paths}."
            return goal
        if mode == "doc_gen":
            topic = mode_data.get("topic", "")
            fmt = mode_data.get("format", "Markdown")
            return f"Erstelle eine Dokumentation im Format {fmt} zum Thema: {topic}"
        if mode == "code_review":
            scope = mode_data.get("scope", "")
            return f"Fuehre ein Code-Review durch fuer: {scope}. Achte auf SOLID und Best Practices."

        return "Generic Goal"


goal_service = GoalService()


def get_goal_service() -> GoalService:
    return goal_service

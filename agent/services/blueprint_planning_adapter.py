from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from agent.services.repository_registry import get_repository_registry

VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}


@dataclass(frozen=True)
class BlueprintPlanningResolution:
    blueprint_id: str | None
    blueprint_name: str | None
    subtasks: list[dict[str, Any]]
    artifact_refs: list[str]
    role_template_hints: list[dict[str, Any]]
    degraded: bool = False
    degraded_reason: str | None = None


class BlueprintPlanningAdapter:
    """Derive planning subtasks from blueprint task artifacts when available."""

    def resolve(self, query: str) -> BlueprintPlanningResolution | None:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return None
        try:
            repos = get_repository_registry()
            blueprint = self._match_blueprint(normalized_query, repos)
            if blueprint is None:
                return None

            artifacts = repos.blueprint_artifact_repo.get_by_blueprint(blueprint.id)
            roles = repos.blueprint_role_repo.get_by_blueprint(blueprint.id)
            template_name_by_id = self._resolve_template_names(
                template_repo=repos.template_repo,
                role_hints=list(roles or []),
            )
            role_template_hints = [
                {
                    "role_name": str(role.name or "").strip(),
                    "template_id": str(role.template_id or "").strip(),
                    "template_name": template_name_by_id.get(str(role.template_id or "").strip()),
                    "is_required": bool(role.is_required),
                    "capability_defaults": dict(role.config or {}).get("capability_defaults"),
                    "risk_profile": dict(role.config or {}).get("risk_profile"),
                    "verification_defaults": dict(role.config or {}).get("verification_defaults"),
                }
                for role in list(roles or [])
                if str(role.name or "").strip()
            ]
            subtasks = self._build_subtasks(
                blueprint_id=str(blueprint.id),
                blueprint_name=str(blueprint.name),
                artifacts=list(artifacts or []),
                role_template_hints=role_template_hints,
            )
            artifact_refs = [
                f"blueprint_artifact:{artifact.id}"
                for artifact in list(artifacts or [])
                if getattr(artifact, "id", None)
            ]
            return BlueprintPlanningResolution(
                blueprint_id=str(blueprint.id),
                blueprint_name=str(blueprint.name),
                subtasks=subtasks,
                artifact_refs=artifact_refs,
                role_template_hints=role_template_hints,
                degraded=False,
                degraded_reason=None,
            )
        except SQLAlchemyError as exc:
            return BlueprintPlanningResolution(
                blueprint_id=None,
                blueprint_name=None,
                subtasks=[],
                artifact_refs=[],
                role_template_hints=[],
                degraded=True,
                degraded_reason=f"blueprint_repo_unavailable:{str(exc)[:200]}",
            )

    def resolve_subtasks(self, query: str) -> list[dict[str, Any]] | None:
        resolution = self.resolve(query)
        if resolution is None:
            return None
        if resolution.degraded or not resolution.subtasks:
            return None
        return list(resolution.subtasks)

    @staticmethod
    def _match_blueprint(query: str, repos) -> Any | None:  # noqa: ANN401
        blueprints = list(repos.team_blueprint_repo.get_all() or [])
        if not blueprints:
            return None
        query_key = _normalize_key(query)
        exact_name = {str(item.name).strip().lower(): item for item in blueprints if str(item.name or "").strip()}
        if query.strip().lower() in exact_name:
            return exact_name[query.strip().lower()]

        exact_slug = {_normalize_key(str(item.name or "")): item for item in blueprints if str(item.name or "").strip()}
        if query_key in exact_slug:
            return exact_slug[query_key]

        # Fuzzy pass: blueprint token contained in query.
        for blueprint in blueprints:
            name = str(blueprint.name or "").strip()
            if not name:
                continue
            name_key = _normalize_key(name)
            if name_key and name_key in query_key:
                return blueprint
        return None

    @staticmethod
    def _build_subtasks(
        *,
        blueprint_id: str,
        blueprint_name: str,
        artifacts: list[Any],
        role_template_hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:  # noqa: ANN401
        task_artifacts = [
            artifact
            for artifact in artifacts
            if str(getattr(artifact, "kind", "")).strip().lower() == "task"
        ]
        subtasks: list[dict[str, Any]] = []
        for index, artifact in enumerate(task_artifacts, start=1):
            payload = dict(getattr(artifact, "payload", {}) or {})
            priority_raw = str(payload.get("priority") or "Medium").strip().lower()
            priority = VALID_PRIORITIES.get(priority_raw, "Medium")
            subtask = {
                "title": str(getattr(artifact, "title", "") or f"{blueprint_name} task {index}").strip()[:200],
                "description": (
                    str(
                        getattr(artifact, "description", "")
                        or payload.get("description")
                        or getattr(artifact, "title", "")
                    )
                    .strip()[:2000]
                ),
                "priority": priority,
                "depends_on": [
                    str(item).strip()
                    for item in list(payload.get("depends_on") or [])
                    if str(item).strip()
                ],
                "artifact": (
                    str(payload.get("artifact") or "").strip()
                    or f"blueprint_artifact:{getattr(artifact, 'id', '')}"
                ),
                "blueprint_id": blueprint_id,
                "blueprint_name": blueprint_name,
                "blueprint_artifact_id": str(getattr(artifact, "id", "")).strip(),
                "blueprint_role_hints": [
                    str(hint.get("role_name") or "").strip()
                    for hint in role_template_hints
                    if str(hint.get("role_name") or "").strip()
                ],
                "blueprint_role_template_hints": [dict(hint) for hint in role_template_hints],
            }
            primary_hint = role_template_hints[0] if role_template_hints else {}
            primary_role_name = str(primary_hint.get("role_name") or "").strip()
            if primary_role_name:
                subtask["blueprint_role_name"] = primary_role_name
            primary_template_name = str(primary_hint.get("template_name") or "").strip()
            if primary_template_name:
                subtask["template_name"] = primary_template_name
            for metadata_key in ("risk_focus", "test_focus", "review_focus"):
                value = str(payload.get(metadata_key) or "").strip()
                if value:
                    subtask[metadata_key] = value
            subtasks.append(subtask)
        return subtasks

    @staticmethod
    def _resolve_template_names(*, template_repo: Any, role_hints: list[Any]) -> dict[str, str]:
        template_ids = {
            str(getattr(role, "template_id", "") or "").strip()
            for role in role_hints
            if str(getattr(role, "template_id", "") or "").strip()
        }
        resolved: dict[str, str] = {}
        for template_id in template_ids:
            template = template_repo.get_by_id(template_id)
            if template is None:
                continue
            template_name = str(getattr(template, "name", "") or "").strip()
            if template_name:
                resolved[template_id] = template_name
        return resolved


blueprint_planning_adapter = BlueprintPlanningAdapter()


def get_blueprint_planning_adapter() -> BlueprintPlanningAdapter:
    return blueprint_planning_adapter


def _normalize_key(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")

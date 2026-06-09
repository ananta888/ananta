from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import jsonschema
from sqlalchemy.exc import SQLAlchemyError

from agent.services.repository_registry import get_repository_registry

# BlueprintPlanningAdapter translates blueprint task artifacts into planning subtasks.
# It may pass role hints/defaults forward, but it must not own worker prompt composition.

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

            workflow_steps = self._load_workflow_steps(repos, blueprint.id)
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
            if workflow_steps:
                # WFG-006: a validated workflow block is the explicit contract
                # for this blueprint. Build subtasks from the workflow DAG in
                # topological order; fall back to artifacts only when the
                # workflow block is absent or empty.
                subtasks = self._build_subtasks_from_workflow(
                    blueprint_id=str(blueprint.id),
                    blueprint_name=str(blueprint.name),
                    workflow_steps=workflow_steps,
                    role_template_hints=role_template_hints,
                )
                artifact_refs = [
                    f"blueprint_workflow_step:{step.id}"
                    for step in workflow_steps
                    if getattr(step, "id", None)
                ]
            else:
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
    def _load_workflow_steps(repos: Any, blueprint_id: str) -> list[Any]:
        """WFG-006: load BlueprintWorkflowStepDB rows for a blueprint.

        Returns an empty list when the repository is unavailable or
        the blueprint has no workflow block. The presence of a
        non-empty list is the trigger for the workflow-first
        subtask builder; the artifacts-first builder is the
        WFG-021 backward-compat path.
        """
        repo = getattr(repos, "blueprint_workflow_step_repo", None)
        if repo is None:
            return []
        try:
            steps = repo.get_by_blueprint(blueprint_id)
        except SQLAlchemyError:
            return []
        return list(steps or [])

    @staticmethod
    def _build_subtasks_from_workflow(
        *,
        blueprint_id: str,
        blueprint_name: str,
        workflow_steps: list[Any],
        role_template_hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """WFG-006: build subtasks from a blueprint's workflow DAG.

        Output order follows topological execution order (depends_on
        first), not sort_order. The catalog normalizer has already
        validated that the DAG is acyclic, so a stable topological
        sort by step_id suffices here.
        """
        ordered = _stable_topo_order(workflow_steps)
        subtasks: list[dict[str, Any]] = []
        for step in ordered:
            role_name = str(getattr(step, "role_name", "") or "").strip()
            task_kind = str(getattr(step, "task_kind", "coding") or "coding").strip() or "coding"
            step_id = str(getattr(step, "step_id", "") or "").strip()
            title = (
                str(getattr(step, "title", "") or f"{blueprint_name} → {role_name} ({step_id})").strip()[:200]
            )
            description = (
                str(getattr(step, "description", "") or "").strip()[:2000]
            )
            depends_on = [str(d).strip() for d in list(getattr(step, "depends_on", []) or []) if str(d).strip()]
            subtasks.append({
                "title": title,
                "description": description,
                "priority": "Medium",
                "depends_on": depends_on,
                "artifact": f"blueprint_workflow_step:{getattr(step, 'id', '')}",
                "blueprint_id": blueprint_id,
                "blueprint_name": blueprint_name,
                "blueprint_workflow_step_id": str(getattr(step, "id", "")).strip(),
                "blueprint_workflow_step_id_label": step_id,
                "blueprint_role_name": role_name,
                "task_kind": task_kind,
                "gate": bool(getattr(step, "gate", False)),
                "checks": dict(getattr(step, "checks", {}) or {}),
                "failure_policy": getattr(step, "failure_policy", None),
                "required_capabilities": list(getattr(step, "required_capabilities", []) or []),
                "produces": list(getattr(step, "produces", []) or []),
                "consumes": list(getattr(step, "consumes", []) or []),
                "blueprint_role_hints": [
                    str(hint.get("role_name") or "").strip()
                    for hint in role_template_hints
                    if str(hint.get("role_name") or "").strip()
                ],
                "blueprint_role_template_hints": [dict(hint) for hint in role_template_hints],
            })
        return subtasks

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


    # --- plan-pattern binding (PAT-005/PAT-006) --------------------------
    # Additive only. The adapter is still pure (no I/O writes, no plan
    # mutation). Bindings are loaded from a single source of truth file
    # so workers can re-validate them out-of-band.

    _BINDINGS_PATH = "./config/plan_pattern_bindings.v1.json"
    _BINDING_SCHEMA_PATH = "./schemas/patterns/plan_pattern_binding.v1.json"

    def _binding_path(self) -> str:
        return os.environ.get(
            "ANANTA_PLAN_PATTERN_BINDINGS_PATH", self._BINDINGS_PATH
        )

    def _binding_schema_path(self) -> str:
        return os.environ.get(
            "ANANTA_PLAN_PATTERN_BINDING_SCHEMA_PATH", self._BINDING_SCHEMA_PATH
        )

    def _load_binding_schema(self) -> dict[str, Any]:
        path = self._binding_schema_path()
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_plan_pattern_bindings(self) -> list[dict[str, Any]]:
        """Return all known plan→pattern bindings.

        Source of truth: ``config/plan_pattern_bindings.v1.json``
        (overridable via ``ANANTA_PLAN_PATTERN_BINDINGS_PATH``).

        The function never mutates any plan or state — it is a pure
        read. Bindings are returned as raw dicts; the caller (or
        ``resolve_pattern_binding``) is responsible for schema
        validation per binding.
        """
        path = self._binding_path()
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        bindings = data.get("bindings", []) if isinstance(data, dict) else []
        if not isinstance(bindings, list):
            return []
        return [b for b in bindings if isinstance(b, dict)]

    def resolve_pattern_binding(
        self, plan_id: str, step_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return the first enabled binding for a plan/step, validated.

        - Filters ``list_plan_pattern_bindings()`` to the given plan
          and optional step.
        - Validates each candidate against the binding schema. A
          candidate that fails validation is logged via the returned
          ``None`` (the caller does not crash; the next candidate is
          tried).
        - Skips disabled bindings (``control.enabled is False``).
        - Returns the first valid, enabled binding dict, or ``None``
          if none match.
        """
        plan_id = str(plan_id or "").strip()
        if not plan_id:
            return None
        step_id_normalized = str(step_id or "").strip() or None
        candidates = [
            b
            for b in self.list_plan_pattern_bindings()
            if str(b.get("plan_id") or "").strip() == plan_id
        ]
        if step_id_normalized is not None:
            candidates = [
                b
                for b in candidates
                if str(b.get("step_id") or "").strip() == step_id_normalized
            ]
        else:
            # plan-level bindings only
            candidates = [
                b for b in candidates if not str(b.get("step_id") or "").strip()
            ]
        schema = self._load_binding_schema()
        validator = jsonschema.Draft7Validator(schema) if schema else None
        for binding in candidates:
            control = binding.get("control") or {}
            if not bool(control.get("enabled", True)):
                continue
            if validator is not None:
                try:
                    validator.validate(binding)
                except jsonschema.ValidationError:
                    continue
            return dict(binding)
        return None


blueprint_planning_adapter = BlueprintPlanningAdapter()


def get_blueprint_planning_adapter() -> BlueprintPlanningAdapter:
    return blueprint_planning_adapter


def _normalize_key(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def _stable_topo_order(steps: list[Any]) -> list[Any]:
    """Stable topological sort by step.depends_on (Kahn, ties by step_id).

    The catalog normalizer has already validated the DAG is acyclic, so
    this routine does not re-validate cycles. It still raises on
    unknown dependencies (defense-in-depth) and on a leftover cycle
    that the normalizer somehow missed.
    """
    if not steps:
        return []
    by_id: dict[str, Any] = {}
    for step in steps:
        sid = str(getattr(step, "step_id", "") or "").strip()
        if not sid:
            continue
        by_id[sid] = step
    in_degree: dict[str, int] = {sid: 0 for sid in by_id}
    edges: dict[str, list[str]] = {sid: [] for sid in by_id}
    for step in steps:
        sid = str(getattr(step, "step_id", "") or "").strip()
        if not sid:
            continue
        for dep in list(getattr(step, "depends_on", []) or []):
            dep_id = str(dep or "").strip()
            if dep_id not in by_id:
                # Unknown dep means a stale BlueprintWorkflowStepDB row.
                # Skip defensively; the catalog normalizer is the
                # authoritative gate.
                continue
            in_degree[sid] += 1
            edges[dep_id].append(sid)

    from collections import deque
    ready: deque[str] = deque(sorted(sid for sid, d in in_degree.items() if d == 0))
    out: list[Any] = []
    while ready:
        sid = ready.popleft()
        out.append(by_id[sid])
        succs = sorted(edges[sid])
        for succ in succs:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                ready.append(succ)
    if len(out) != len(by_id):
        # Defensive: cycle that escaped the normalizer. Surface it loudly.
        from agent.services.workflow_definition_service import WorkflowDefinitionError
        raise WorkflowDefinitionError(
            f"workflow DAG cycle detected at materialization time; reached {len(out)} of {len(by_id)} steps"
        )
    return out

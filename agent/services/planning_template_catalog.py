from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_SUBTASK_METADATA = ("artifact", "risk_focus", "test_focus", "review_focus")
VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}


class PlanningTemplateCatalog:
    """Catalog for deterministic planning template resolution."""

    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        schema_path: Path | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.catalog_path = catalog_path or (self.repository_root / "config" / "planning_templates.json")
        self.schema_path = schema_path or (
            self.repository_root / "schemas" / "planning" / "planning_template_catalog.v1.json"
        )
        self._catalog: dict[str, Any] | None = None
        self._templates: list[dict[str, Any]] = []
        self._templates_by_id: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None

    def load(self, *, force_reload: bool = False) -> dict[str, Any]:
        if self._catalog is not None and not force_reload:
            return copy.deepcopy(self._catalog)

        payload = self._load_json(self.catalog_path)
        schema = self._load_json(self.schema_path)
        validation_errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
        if validation_errors:
            readable = "; ".join(
                f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in validation_errors
            )
            raise ValueError(f"invalid planning template catalog {self.catalog_path}: {readable}")

        templates = list(payload.get("templates") or [])
        normalized_templates: list[dict[str, Any]] = []
        templates_by_id: dict[str, dict[str, Any]] = {}
        for index, template in enumerate(templates):
            template_id = str(template.get("id") or "").strip()
            if not template_id:
                raise ValueError(f"planning template at index {index} missing id")
            if template_id in templates_by_id:
                raise ValueError(f"duplicate planning template id: {template_id}")
            keywords = [str(item).strip() for item in list(template.get("keywords") or []) if str(item).strip()]
            if not keywords:
                raise ValueError(f"planning template {template_id} has no keywords")
            subtasks = self._normalize_subtasks(
                template_id=template_id,
                subtasks=list(template.get("subtasks") or []),
            )
            normalized_template = {
                "id": template_id,
                "title": str(template.get("title") or template_id).strip() or template_id,
                "keywords": keywords,
                "related_standard_blueprints": [
                    str(item).strip()
                    for item in list(template.get("related_standard_blueprints") or [])
                    if str(item).strip()
                ],
                "subtasks": subtasks,
            }
            normalized_templates.append(normalized_template)
            templates_by_id[template_id] = normalized_template

        self._catalog = {
            "schema": str(payload.get("schema") or ""),
            "version": str(payload.get("version") or ""),
            "templates": normalized_templates,
            "execution_focused_goal_hints": [
                str(item).strip()
                for item in list(payload.get("execution_focused_goal_hints") or [])
                if str(item).strip()
            ],
        }
        self._templates = normalized_templates
        self._templates_by_id = templates_by_id
        self.load_error = None
        return copy.deepcopy(self._catalog)

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        if not self._ensure_loaded():
            return None
        normalized_id = str(template_id or "").strip()
        if not normalized_id:
            return None
        template = self._templates_by_id.get(normalized_id)
        if template is None:
            return None
        return copy.deepcopy(template)

    def resolve_subtasks(self, query: str, *, exact_id_first: bool = True) -> list[dict[str, Any]] | None:
        template = self.resolve_template(query, exact_id_first=exact_id_first)
        if template is None:
            return None
        return copy.deepcopy(list(template.get("subtasks") or []))

    def resolve_template(self, query: str, *, exact_id_first: bool = True) -> dict[str, Any] | None:
        if not self._ensure_loaded():
            return None
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return None
        if exact_id_first:
            direct = self._templates_by_id.get(normalized_query)
            if direct is not None:
                return copy.deepcopy(direct)
        lower_query = normalized_query.lower()
        for template in self._templates:
            for keyword in list(template.get("keywords") or []):
                if str(keyword).lower() in lower_query:
                    return copy.deepcopy(template)
        return None

    def _ensure_loaded(self) -> bool:
        try:
            self.load()
            return True
        except (OSError, ValueError) as exc:
            self.load_error = str(exc)
            return False

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _normalize_subtasks(*, template_id: str, subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_subtasks: list[dict[str, Any]] = []
        for index, subtask in enumerate(subtasks, start=1):
            normalized = _normalize_subtask(subtask, default_priority="Medium")
            if normalized is None:
                raise ValueError(f"invalid subtask in template {template_id} at position {index}")
            merged = dict(normalized)
            for key in OPTIONAL_SUBTASK_METADATA:
                value = str((subtask or {}).get(key) or "").strip()
                if value:
                    merged[key] = value
            normalized_subtasks.append(merged)
        if not normalized_subtasks:
            raise ValueError(f"planning template {template_id} has no valid subtasks")
        return normalized_subtasks


planning_template_catalog = PlanningTemplateCatalog()


def get_planning_template_catalog() -> PlanningTemplateCatalog:
    return planning_template_catalog


def _normalize_priority(value: Any, default_priority: str = "Medium") -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_PRIORITIES:
        return VALID_PRIORITIES[raw]
    fallback = str(default_priority or "Medium").strip().lower()
    return VALID_PRIORITIES.get(fallback, "Medium")


def _normalize_subtask(subtask: dict[str, Any], default_priority: str = "Medium") -> dict[str, Any] | None:
    if not isinstance(subtask, dict):
        return None
    title = str(subtask.get("title") or subtask.get("name") or "").strip()
    description = str(subtask.get("description") or subtask.get("task") or title).strip()
    if not title:
        title = description[:200].strip()
    if not title or not description:
        return None
    depends_on_raw = subtask.get("depends_on")
    depends_on = []
    if isinstance(depends_on_raw, list):
        depends_on = [str(item).strip() for item in depends_on_raw if str(item).strip()][:10]
    return {
        "title": title[:200],
        "description": description[:2000],
        "priority": _normalize_priority(subtask.get("priority"), default_priority=default_priority),
        "depends_on": depends_on,
    }

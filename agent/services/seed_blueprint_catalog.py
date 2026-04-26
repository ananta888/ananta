from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class SeedBlueprintCatalog:
    """Catalog for loading standard seed blueprints from data files."""

    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        schema_path: Path | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.catalog_path = catalog_path or (
            self.repository_root / "config" / "blueprints" / "standard" / "blueprints.json"
        )
        self.schema_path = schema_path or (
            self.repository_root / "schemas" / "blueprints" / "seed_blueprint_catalog.v1.json"
        )
        self._catalog: dict[str, Any] | None = None
        self._blueprints: list[dict[str, Any]] = []
        self._blueprints_by_name: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None

    def load(self, *, force_reload: bool = False) -> dict[str, Any]:
        if self._catalog is not None and not force_reload:
            return copy.deepcopy(self._catalog)

        payload = self._load_json(self.catalog_path)
        schema = self._load_json(self.schema_path)
        validation_errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda err: list(err.path),
        )
        if validation_errors:
            readable = "; ".join(
                f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in validation_errors
            )
            raise ValueError(f"invalid seed blueprint catalog {self.catalog_path}: {readable}")

        normalized_blueprints: list[dict[str, Any]] = []
        by_name: dict[str, dict[str, Any]] = {}
        for index, blueprint in enumerate(list(payload.get("blueprints") or []), start=1):
            normalized = self._normalize_blueprint(blueprint, index=index)
            name_key = str(normalized["name"]).strip().lower()
            if name_key in by_name:
                raise ValueError(f"duplicate seed blueprint name: {normalized['name']}")
            normalized_blueprints.append(normalized)
            by_name[name_key] = normalized

        if not normalized_blueprints:
            raise ValueError("seed blueprint catalog has no blueprints")

        self._catalog = {
            "schema": str(payload.get("schema") or ""),
            "version": str(payload.get("version") or ""),
            "blueprints": normalized_blueprints,
        }
        self._blueprints = normalized_blueprints
        self._blueprints_by_name = by_name
        self.load_error = None
        return copy.deepcopy(self._catalog)

    def list_blueprints(self) -> list[dict[str, Any]]:
        if not self._ensure_loaded():
            return []
        return copy.deepcopy(self._blueprints)

    def get_blueprint(self, name: str) -> dict[str, Any] | None:
        if not self._ensure_loaded():
            return None
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            return None
        blueprint = self._blueprints_by_name.get(normalized_name)
        if blueprint is None:
            return None
        return copy.deepcopy(blueprint)

    def as_seed_blueprint_map(self) -> dict[str, dict[str, Any]]:
        if not self._ensure_loaded():
            return {}
        result: dict[str, dict[str, Any]] = {}
        for blueprint in self._blueprints:
            name = str(blueprint.get("name") or "").strip()
            if not name:
                continue
            result[name] = {
                "description": str(blueprint.get("description") or "").strip(),
                "base_team_type_name": str(blueprint.get("base_team_type_name") or "").strip() or None,
                "roles": copy.deepcopy(list(blueprint.get("roles") or [])),
                "artifacts": copy.deepcopy(list(blueprint.get("artifacts") or [])),
            }
        return result

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
    def _normalize_blueprint(raw: dict[str, Any], *, index: int) -> dict[str, Any]:
        name = str(raw.get("name") or "").strip()
        description = str(raw.get("description") or "").strip()
        base_team_type_name = str(raw.get("base_team_type_name") or "").strip() or None
        if not name:
            raise ValueError(f"seed blueprint at index {index} missing name")
        if not description:
            raise ValueError(f"seed blueprint {name} missing description")

        roles = list(raw.get("roles") or [])
        artifacts = list(raw.get("artifacts") or [])
        normalized_roles: list[dict[str, Any]] = []
        normalized_artifacts: list[dict[str, Any]] = []

        seen_role_names: set[str] = set()
        seen_role_orders: set[int] = set()
        for role in roles:
            role_name = str((role or {}).get("name") or "").strip()
            role_desc = str((role or {}).get("description") or "").strip()
            template_name = str((role or {}).get("template_name") or "").strip()
            sort_order = int((role or {}).get("sort_order") or 0)
            is_required = bool((role or {}).get("is_required"))
            config = dict((role or {}).get("config") or {})
            if not role_name:
                raise ValueError(f"seed blueprint {name} has role without name")
            if role_name.lower() in seen_role_names:
                raise ValueError(f"seed blueprint {name} has duplicate role name: {role_name}")
            if sort_order in seen_role_orders:
                raise ValueError(f"seed blueprint {name} has duplicate role sort_order: {sort_order}")
            seen_role_names.add(role_name.lower())
            seen_role_orders.add(sort_order)
            normalized_roles.append(
                {
                    "name": role_name,
                    "description": role_desc,
                    "template_name": template_name,
                    "sort_order": sort_order,
                    "is_required": is_required,
                    "config": config,
                }
            )

        seen_artifact_titles: set[str] = set()
        seen_artifact_orders: set[int] = set()
        for artifact in artifacts:
            artifact_kind = str((artifact or {}).get("kind") or "").strip()
            artifact_title = str((artifact or {}).get("title") or "").strip()
            artifact_description = str((artifact or {}).get("description") or "").strip()
            artifact_sort = int((artifact or {}).get("sort_order") or 0)
            artifact_payload = dict((artifact or {}).get("payload") or {})
            if not artifact_kind or not artifact_title:
                raise ValueError(f"seed blueprint {name} has invalid artifact entry")
            if artifact_title.lower() in seen_artifact_titles:
                raise ValueError(f"seed blueprint {name} has duplicate artifact title: {artifact_title}")
            if artifact_sort in seen_artifact_orders:
                raise ValueError(f"seed blueprint {name} has duplicate artifact sort_order: {artifact_sort}")
            seen_artifact_titles.add(artifact_title.lower())
            seen_artifact_orders.add(artifact_sort)
            normalized_artifacts.append(
                {
                    "kind": artifact_kind,
                    "title": artifact_title,
                    "description": artifact_description,
                    "sort_order": artifact_sort,
                    "payload": artifact_payload,
                }
            )

        if not normalized_roles:
            raise ValueError(f"seed blueprint {name} has no roles")
        if not normalized_artifacts:
            raise ValueError(f"seed blueprint {name} has no artifacts")

        return {
            "name": name,
            "description": description,
            "base_team_type_name": base_team_type_name,
            "roles": normalized_roles,
            "artifacts": normalized_artifacts,
        }


seed_blueprint_catalog = SeedBlueprintCatalog()


def get_seed_blueprint_catalog() -> SeedBlueprintCatalog:
    return seed_blueprint_catalog

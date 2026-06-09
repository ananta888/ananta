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
        fragments_dir: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.catalog_path = catalog_path or (
            self.repository_root / "config" / "blueprints" / "standard" / "blueprints.json"
        )
        self.fragments_dir = fragments_dir or self.catalog_path.parent / "blueprints.d"
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

        payloads = self._load_payloads()
        schema = self._load_json(self.schema_path)
        normalized_blueprints: list[dict[str, Any]] = []
        by_name: dict[str, dict[str, Any]] = {}
        schema_name = ""
        version = ""

        for payload_path, payload in payloads:
            if not schema_name:
                schema_name = str(payload.get("schema") or "")
            if not version:
                version = str(payload.get("version") or "")
            validation_errors = sorted(
                Draft202012Validator(schema).iter_errors(payload),
                key=lambda err: list(err.path),
            )
            if validation_errors:
                readable = "; ".join(
                    f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in validation_errors
                )
                raise ValueError(f"invalid seed blueprint catalog {payload_path}: {readable}")

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
            "schema": schema_name,
            "version": version,
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
            workflow = blueprint.get("workflow")
            if isinstance(workflow, dict) and workflow:
                result[name]["workflow"] = copy.deepcopy(workflow)
        return result

    def _ensure_loaded(self) -> bool:
        try:
            self.load()
            return True
        except (OSError, ValueError) as exc:
            self.load_error = str(exc)
            return False

    def _load_payloads(self) -> list[tuple[Path, dict[str, Any]]]:
        payloads: list[tuple[Path, dict[str, Any]]] = [(self.catalog_path, self._load_json(self.catalog_path))]
        if self.fragments_dir.exists():
            for fragment_path in sorted(self.fragments_dir.glob("*.json")):
                payloads.append((fragment_path, self._load_json(fragment_path)))
        return payloads

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

        workflow = SeedBlueprintCatalog._normalize_workflow(
            raw.get("workflow"),
            blueprint_name=name,
            role_names=seen_role_names,
        )
        return {
            "name": name,
            "description": description,
            "base_team_type_name": base_team_type_name,
            "roles": normalized_roles,
            "artifacts": normalized_artifacts,
            "workflow": workflow,
        }

    @staticmethod
    def _normalize_workflow(
        raw: Any,
        *,
        blueprint_name: str,
        role_names: set[str],
    ) -> dict[str, Any] | None:
        if not raw:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"seed blueprint {blueprint_name} workflow must be an object")

        mode = str(raw.get("mode") or "gated")
        if mode not in {"off", "direct", "gated", "strict_gated"}:
            raise ValueError(f"seed blueprint {blueprint_name} workflow.mode invalid: {mode}")

        default_failure_policy = str(raw.get("default_failure_policy") or "block")
        if default_failure_policy not in {"block", "skip", "manual"}:
            raise ValueError(
                f"seed blueprint {blueprint_name} workflow.default_failure_policy invalid: "
                f"{default_failure_policy}"
            )

        raw_steps = list(raw.get("steps") or [])
        normalized_steps: list[dict[str, Any]] = []
        seen_step_ids: set[str] = set()
        by_id: dict[str, dict[str, Any]] = {}

        for index, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{index}] must be an object"
                )
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{index}] missing id"
                )
            if step_id in seen_step_ids:
                raise ValueError(
                    f"seed blueprint {blueprint_name} has duplicate workflow step id: {step_id}"
                )
            seen_step_ids.add(step_id)

            role_name = str(step.get("role") or "").strip()
            if not role_name:
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}] missing role"
                )
            if role_name.lower() not in role_names:
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}].role "
                    f"'{role_name}' is not in the blueprint's roles"
                )

            task_kind = str(step.get("task_kind") or "coding")
            gate = bool(step.get("gate", False))
            checks = step.get("checks")
            if gate and not checks:
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}].gate=true "
                    f"requires a non-empty 'checks' object"
                )
            if checks is not None and not isinstance(checks, dict):
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}].checks "
                    f"must be an object"
                )

            failure_policy = step.get("failure_policy")
            if failure_policy is not None and failure_policy not in {"block", "skip", "manual"}:
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}].failure_policy "
                    f"invalid: {failure_policy}"
                )

            raw_hints = step.get("pattern_hints")
            pattern_hints = _normalize_pattern_hints(
                raw_hints, blueprint_name=blueprint_name, step_id=step_id
            ) if raw_hints is not None else None

            normalized = {
                "id": step_id,
                "role": role_name,
                "task_kind": task_kind,
                "title": str(step.get("title") or step_id).strip() or step_id,
                "description": str(step.get("description") or "").strip(),
                "produces": [str(x) for x in (step.get("produces") or [])],
                "consumes": [str(x) for x in (step.get("consumes") or [])],
                "depends_on": [str(x) for x in (step.get("depends_on") or [])],
                "gate": gate,
                "checks": copy.deepcopy(checks) if checks else None,
                "failure_policy": failure_policy,
                "required_capabilities": [str(x) for x in (step.get("required_capabilities") or [])],
                "sort_order": int(step.get("sort_order") or 0),
                "pattern_hints": pattern_hints,
            }
            normalized_steps.append(normalized)
            by_id[step_id] = normalized

        for step in normalized_steps:
            for dep in step["depends_on"]:
                if dep not in by_id:
                    raise ValueError(
                        f"seed blueprint {blueprint_name} workflow.steps[{step['id']}].depends_on "
                        f"references unknown step: {dep}"
                    )

        indeg: dict[str, int] = {sid: 0 for sid in by_id}
        for step in normalized_steps:
            for _ in step["depends_on"]:
                indeg[step["id"]] += 1
        ready: list[str] = sorted(
            [sid for sid, d in indeg.items() if d == 0],
            key=lambda s: (by_id[s]["sort_order"], s),
        )
        topo: list[str] = []
        while ready:
            nxt = ready.pop(0)
            topo.append(nxt)
            for step in normalized_steps:
                if nxt in step["depends_on"]:
                    indeg[step["id"]] -= 1
                    if indeg[step["id"]] == 0:
                        ready.append(step["id"])
            ready.sort(key=lambda s: (by_id[s]["sort_order"], s))
        if len(topo) != len(by_id):
            raise ValueError(
                f"seed blueprint {blueprint_name} workflow.steps contain a cycle"
            )

        return {
            "mode": mode,
            "default_failure_policy": default_failure_policy,
            "steps": normalized_steps,
        }


seed_blueprint_catalog = SeedBlueprintCatalog()


_VALID_PATTERN_ID_RE = __import__("re").compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _normalize_pattern_hints(
    raw: Any,
    *,
    blueprint_name: str,
    step_id: str,
) -> dict[str, Any]:
    """Validate and normalize a workflow step's optional pattern_hints block.

    Accepted keys: allowed_patterns, preferred_patterns, forbid_patterns,
    language_targets, require_tests.  Unknown keys are silently dropped so
    that future catalog extensions don't break old seeds.

    Raises ValueError when a pattern ID contains invalid characters or when
    a preferred ID is not also in allowed_patterns (would silently ignore it).
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"seed blueprint {blueprint_name} workflow.steps[{step_id}].pattern_hints "
            "must be an object"
        )

    def _ids(key: str) -> list[str]:
        items = list(raw.get(key) or [])
        out: list[str] = []
        for item in items:
            s = str(item or "").strip().lower()
            if not s:
                continue
            if not _VALID_PATTERN_ID_RE.match(s):
                raise ValueError(
                    f"seed blueprint {blueprint_name} workflow.steps[{step_id}]"
                    f".pattern_hints.{key} contains invalid id: {s!r}"
                )
            out.append(s)
        return out

    allowed = _ids("allowed_patterns")
    preferred = _ids("preferred_patterns")
    forbidden = _ids("forbid_patterns")
    language_targets = [
        str(x or "").strip().lower()
        for x in list(raw.get("language_targets") or [])
        if str(x or "").strip()
    ]
    require_tests = bool(raw.get("require_tests", True))

    # preferred must be a subset of allowed (if allowed is non-empty)
    if allowed and preferred:
        unknown_preferred = [p for p in preferred if p not in allowed]
        if unknown_preferred:
            raise ValueError(
                f"seed blueprint {blueprint_name} workflow.steps[{step_id}]"
                f".pattern_hints.preferred_patterns contains ids not in "
                f"allowed_patterns: {unknown_preferred}"
            )

    hints: dict[str, Any] = {"require_tests": require_tests}
    if allowed:
        hints["allowed_patterns"] = allowed
    if preferred:
        hints["preferred_patterns"] = preferred
    if forbidden:
        hints["forbid_patterns"] = forbidden
    if language_targets:
        hints["language_targets"] = language_targets
    return hints


def get_seed_blueprint_catalog() -> SeedBlueprintCatalog:
    return seed_blueprint_catalog

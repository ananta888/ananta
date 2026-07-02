"""ExpertDefinition — COSMOS-001

Schema, Loader und Registry für Expert-Definitionen.
Experts beschreiben wiederverwendbare Worker-Rollen. Die tatsächlich geltenden
Rechte sind immer die Schnittmenge aus Expert-Definition und aktiver Hub-Policy.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# Pflichtfelder gemäß Design-Doc COSMOS-001
_REQUIRED_FIELDS = {"expert_id", "version", "title", "purpose", "output_contract"}


@dataclass
class ExpertDefinition:
    expert_id: str
    version: str
    title: str
    purpose: str
    allowed_tools: list[str]
    denied_tools: list[str]
    allowed_path_patterns: list[str]
    denied_path_patterns: list[str]
    model_routing: dict[str, Any]
    context_strategy: str
    output_contract: str
    approval_gates: list[str]
    min_policy_scope: str
    extends: str | None = None

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Returns list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.expert_id:
            errors.append("expert_id is required and must not be empty")
        if not self.version:
            errors.append("version is required and must not be empty")
        if not self.title:
            errors.append("title is required and must not be empty")
        if not self.purpose:
            errors.append("purpose is required and must not be empty")
        if not self.output_contract:
            errors.append("output_contract is required and must not be empty")
        valid_scopes = {"global", "project", "workspace"}
        if self.min_policy_scope not in valid_scopes:
            errors.append(
                f"min_policy_scope must be one of {valid_scopes}, got: {self.min_policy_scope!r}"
            )
        return errors

    # ── Tool access ───────────────────────────────────────────────────────────

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Returns True if tool is in allowed_tools and NOT in denied_tools.

        denied_tools always overrides allowed_tools.
        """
        if tool_name in self.denied_tools:
            return False
        return tool_name in self.allowed_tools

    # ── Path access ───────────────────────────────────────────────────────────

    def is_path_allowed(self, path: str) -> bool:
        """Matches path against allowed/denied patterns.

        Rules (fail-closed / default-deny):
        1. If path matches any denied_path_pattern → False.
        2. If path matches any allowed_path_pattern → True.
        3. Otherwise → False (default deny).
        """
        for pattern in self.denied_path_patterns:
            if fnmatch.fnmatch(path, pattern):
                return False
        for pattern in self.allowed_path_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict) -> "ExpertDefinition":
        """Construct ExpertDefinition from a plain dict (e.g. loaded from YAML)."""
        return cls(
            expert_id=str(data.get("expert_id") or ""),
            version=str(data.get("version") or ""),
            title=str(data.get("title") or ""),
            purpose=str(data.get("purpose") or ""),
            allowed_tools=list(data.get("allowed_tools") or []),
            denied_tools=list(data.get("denied_tools") or []),
            allowed_path_patterns=list(data.get("allowed_path_patterns") or []),
            denied_path_patterns=list(data.get("denied_path_patterns") or []),
            model_routing=dict(data.get("model_routing") or {}),
            context_strategy=str(data.get("context_strategy") or ""),
            output_contract=str(data.get("output_contract") or ""),
            approval_gates=list(data.get("approval_gates") or []),
            min_policy_scope=str(data.get("min_policy_scope") or "project"),
            extends=data.get("extends") or None,
        )


class ExpertRegistry:
    """Loads and serves ExpertDefinitions from a config directory."""

    _DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "experts"

    def __init__(self, config_dir: str | Path | None = None) -> None:
        self._config_dir = Path(config_dir) if config_dir else self._DEFAULT_CONFIG_DIR
        self._experts: dict[str, ExpertDefinition] = {}

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_all(self) -> dict[str, ExpertDefinition]:
        """Load all YAML/JSON files from config_dir.

        Returns {expert_id: ExpertDefinition}.
        Raises ValueError on duplicate expert_id.
        """
        if yaml is None:
            raise ImportError("PyYAML is required for ExpertRegistry.load_all()")

        loaded: dict[str, ExpertDefinition] = {}
        config_dir = self._config_dir

        if not config_dir.exists():
            return loaded

        for path in sorted(config_dir.glob("*.yaml")):
            expert = self._load_file(path)
            if expert.expert_id in loaded:
                raise ValueError(
                    f"Duplicate expert_id {expert.expert_id!r} detected in {path}"
                )
            loaded[expert.expert_id] = expert

        for path in sorted(config_dir.glob("*.json")):
            expert = self._load_file(path)
            if expert.expert_id in loaded:
                raise ValueError(
                    f"Duplicate expert_id {expert.expert_id!r} detected in {path}"
                )
            loaded[expert.expert_id] = expert

        self._experts = loaded
        return dict(loaded)

    def _load_file(self, path: Path) -> ExpertDefinition:
        suffix = path.suffix.lower()
        with path.open("r", encoding="utf-8") as fh:
            if suffix in {".yaml", ".yml"}:
                data = yaml.safe_load(fh)
            else:
                import json
                data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Expert file {path} must contain a YAML/JSON mapping")
        return ExpertDefinition.from_dict(data)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def get(self, expert_id: str) -> ExpertDefinition | None:
        """Return ExpertDefinition by id, or None if not found."""
        return self._experts.get(expert_id)

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_all(self) -> dict[str, list[str]]:
        """Returns {expert_id: [errors]} for all experts with validation issues."""
        result: dict[str, list[str]] = {}
        for expert_id, expert in self._experts.items():
            errors = expert.validate()
            if errors:
                result[expert_id] = errors
        return result

    # ── Policy intersection ───────────────────────────────────────────────────

    def apply_policy_intersection(
        self,
        expert: ExpertDefinition,
        *,
        allowed_tools: list[str],
        allowed_paths: list[str],
    ) -> ExpertDefinition:
        """Returns a new ExpertDefinition where tools and paths are the intersection
        of the expert's own allowlists and the hub policy allowlists.

        An expert can never gain more rights than the hub policy permits.
        """
        # Tool intersection: only tools the expert allows AND policy allows
        intersected_tools = [t for t in expert.allowed_tools if t in allowed_tools]

        # Path patterns: keep only expert patterns that are covered by a policy pattern.
        # A path pattern is "covered" if there exists a policy pattern that is equal to
        # or broader than the expert pattern. Since patterns can be wildcards, we use a
        # conservative approach: keep expert pattern only if policy has an identical
        # pattern or the wildcard "**".
        policy_path_set = set(allowed_paths)
        intersected_paths: list[str] = []
        for expert_pattern in expert.allowed_path_patterns:
            if expert_pattern in policy_path_set or "**" in policy_path_set:
                intersected_paths.append(expert_pattern)

        from dataclasses import replace
        return replace(
            expert,
            allowed_tools=intersected_tools,
            allowed_path_patterns=intersected_paths,
        )

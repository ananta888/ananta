"""SkillRegistry: disabled-by-default skill loading with hash/version state. AWF-T028."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.skills.skill_manifest import SkillManifest, validate_skill_manifest


@dataclass
class SkillEntry:
    manifest: SkillManifest
    enabled: bool = False
    load_error: str | None = None  # first validation error if invalid


class SkillRegistry:
    """Registry of available skills. Skills are disabled by default. AWF-T028.

    Skills must be explicitly enabled after registration.
    Invalid skills (failed manifest validation) cannot be enabled.
    Duplicate id+version is rejected with a conflict error.
    """

    def __init__(self, *, known_capabilities: set[str] | None = None) -> None:
        self._entries: dict[str, SkillEntry] = {}
        self._known_capabilities = known_capabilities

    def _key(self, skill_id: str, version: str) -> str:
        return f"{skill_id}:{version}"

    def register(
        self,
        manifest: SkillManifest,
        *,
        known_capabilities: set[str] | None = None,
    ) -> list[str]:
        """Register a skill manifest. Returns validation errors (empty = success). AWF-T028.

        On success the skill is loaded but disabled.
        On failure the entry is stored with load_error for diagnostics.
        """
        caps = known_capabilities if known_capabilities is not None else self._known_capabilities
        errors = validate_skill_manifest(manifest, known_capabilities=caps)
        key = self._key(manifest.id, manifest.version)

        if key in self._entries:
            conflict = f"skill_registry_conflict:{manifest.id}:{manifest.version}"
            return [conflict]

        self._entries[key] = SkillEntry(
            manifest=manifest,
            enabled=False,
            load_error=errors[0] if errors else None,
        )
        return errors

    def enable(self, skill_id: str, version: str | None = None) -> bool:
        """Enable a previously registered skill. Returns False if not found or invalid."""
        entry = self._resolve(skill_id, version)
        if entry is None or entry.load_error:
            return False
        entry.enabled = True
        return True

    def disable(self, skill_id: str, version: str | None = None) -> None:
        entry = self._resolve(skill_id, version)
        if entry:
            entry.enabled = False

    def get(self, skill_id: str, version: str | None = None) -> SkillEntry | None:
        return self._resolve(skill_id, version)

    def is_enabled(self, skill_id: str, version: str | None = None) -> bool:
        entry = self._resolve(skill_id, version)
        return entry is not None and entry.enabled and not entry.load_error

    def _resolve(self, skill_id: str, version: str | None) -> SkillEntry | None:
        if version:
            return self._entries.get(self._key(skill_id, version))
        # Return last registered version for this id (order of insertion)
        matches = [e for k, e in self._entries.items() if k.startswith(f"{skill_id}:")]
        return matches[-1] if matches else None

    def list_diagnostics(self) -> list[dict[str, Any]]:
        """Return safe diagnostics without exposing skill prompts or secrets. AWF-T028."""
        return [
            {
                "id": e.manifest.id,
                "version": e.manifest.version,
                "name": e.manifest.name,
                "enabled": e.enabled,
                "risk_class": e.manifest.risk_class,
                "required_capabilities": list(e.manifest.required_capabilities),
                "allowed_tool_count": len(e.manifest.allowed_tools),
                "content_hash": e.manifest.content_hash,
                "load_error": e.load_error,
            }
            for e in self._entries.values()
        ]

"""Hub-owned loading and validation of immutable verification profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ananta_contracts.verification import VerificationBudgets, canonical_digest


@dataclass(frozen=True, slots=True)
class VerificationProfile:
    profile_id: str
    backend: str
    enabled: bool
    release_gate: bool
    network: str
    filesystem: str
    allow_plugins: bool
    allow_unblock_everything: bool
    budgets: VerificationBudgets
    raw: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return canonical_digest(dict(self.raw))


class VerificationProfileService:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, VerificationProfile]:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if payload.get("schema") != "ananta.verification-profiles.v1" or set(payload) != {"schema", "profiles"}:
            raise ValueError("verification_profiles_schema_invalid")
        profiles: dict[str, VerificationProfile] = {}
        for raw_value in payload["profiles"]:
            raw = dict(raw_value)
            required = {
                "profile_id",
                "backend",
                "enabled",
                "release_gate",
                "network",
                "filesystem",
                "allow_plugins",
                "allow_unblock_everything",
                "budgets",
            }
            if set(raw) != required:
                raise ValueError("verification_profile_fields_invalid")
            profile_id = str(raw["profile_id"])
            if profile_id in profiles:
                raise ValueError("verification_profile_duplicate")
            if raw["network"] != "none" or raw["filesystem"] != "task_workspace_only":
                raise ValueError("verification_profile_isolation_invalid")
            if raw["allow_plugins"] is not False or raw["allow_unblock_everything"] is not False:
                raise ValueError("verification_profile_tool_escape_invalid")
            profile = VerificationProfile(
                profile_id=profile_id,
                backend=str(raw["backend"]),
                enabled=raw["enabled"] is True,
                release_gate=raw["release_gate"] is True,
                network=str(raw["network"]),
                filesystem=str(raw["filesystem"]),
                allow_plugins=raw["allow_plugins"] is True,
                allow_unblock_everything=raw["allow_unblock_everything"] is True,
                budgets=VerificationBudgets(**dict(raw["budgets"])),
                raw=raw,
            )
            profiles[profile_id] = profile
        return profiles

    def get_enabled(self, profile_id: str) -> VerificationProfile:
        profile = self.load().get(profile_id)
        if profile is None or not profile.enabled:
            raise ValueError("verification_profile_disabled_or_unknown")
        return profile


__all__ = ["VerificationProfile", "VerificationProfileService"]

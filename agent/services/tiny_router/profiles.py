"""Fail-closed loader for the versioned tiny action-model catalog."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from agent.services.tiny_router.types import TinyActionModelProfile

CATALOG_SCHEMA = "ananta.tiny_action_model_profiles.v1"
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "models"
    / "tiny_action_model_profiles.v1.json"
)


@dataclass(frozen=True)
class ProfileCatalog:
    profiles: tuple[TinyActionModelProfile, ...]
    source_path: str
    content_sha256: str
    safe_mode: bool = False
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG_PATH) -> "ProfileCatalog":
        source = Path(path)
        try:
            raw = source.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("catalog_must_be_object")
            if payload.get("schema") != CATALOG_SCHEMA:
                raise ValueError("unsupported_catalog_schema")
            rows = payload.get("profiles")
            if not isinstance(rows, list):
                raise ValueError("catalog_profiles_must_be_array")
            profiles = tuple(TinyActionModelProfile.from_mapping(row) for row in rows)
            ids = [item.profile_id for item in profiles]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate_profile_id")
            return cls(profiles, str(source), hashlib.sha256(raw).hexdigest())
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return cls(
                (), str(source), "", safe_mode=True,
                diagnostics=(str(exc) or exc.__class__.__name__,),
            )

    @classmethod
    def from_profiles(
        cls, profiles: Iterable[TinyActionModelProfile]
    ) -> "ProfileCatalog":
        rows = tuple(profiles)
        if len({item.profile_id for item in rows}) != len(rows):
            raise ValueError("duplicate_profile_id")
        return cls(rows, "<injected>", "")

    def get(self, profile_id: str) -> TinyActionModelProfile | None:
        normalized = str(profile_id or "").strip()
        return next((item for item in self.profiles if item.profile_id == normalized), None)

    def ordered(
        self, requested_ids: Iterable[str], *, commercial_use: bool,
        allow_research_only: bool,
    ) -> tuple[tuple[TinyActionModelProfile, ...], tuple[tuple[str, str], ...]]:
        selected: list[TinyActionModelProfile] = []
        rejected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_id in requested_ids:
            profile_id = str(raw_id or "").strip()
            if not profile_id or profile_id in seen:
                continue
            seen.add(profile_id)
            profile = self.get(profile_id)
            if profile is None:
                rejected.append((profile_id, "unknown_profile"))
            elif commercial_use and not profile.commercial_use_allowed:
                rejected.append((profile_id, "license_commercial_use_denied"))
            elif profile.research_only and not allow_research_only:
                rejected.append((profile_id, "research_only_profile_denied"))
            else:
                selected.append(profile)
        return tuple(selected), tuple(rejected)

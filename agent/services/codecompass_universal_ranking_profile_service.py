"""Hub-owned strategy selection and governed universal ranking profiles."""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Mapping

from ananta_codecompass.ranking import RankingProfile

VALID_STRATEGIES = frozenset({"universal", "shadow", "legacy"})
_REQUIRED_OVERRIDE_FIELDS = frozenset({"owner", "reason", "scope", "version", "expires_at"})


@dataclass(frozen=True, slots=True)
class UniversalRankingPolicy:
    strategy: str
    profile: RankingProfile
    override_status: str


class CodeCompassUniversalRankingProfileService:
    """Resolve deployment-wide policy; request/session input is intentionally absent."""

    def resolve(self, environment: Mapping[str, str] | None = None) -> UniversalRankingPolicy:
        env = environment or os.environ
        strategy = str(env.get("ANANTA_CODECOMPASS_REPOSITORY_RANKER") or "universal").strip().lower()
        if strategy not in VALID_STRATEGIES:
            strategy = "universal"
        raw_override = str(env.get("ANANTA_CODECOMPASS_RANKING_OVERRIDE_JSON") or "").strip()
        if not raw_override:
            return UniversalRankingPolicy(strategy, RankingProfile(), "disabled")
        try:
            override = json.loads(raw_override)
        except json.JSONDecodeError:
            return UniversalRankingPolicy(strategy, RankingProfile(), "rejected_invalid_json")
        if not isinstance(override, dict) or not _REQUIRED_OVERRIDE_FIELDS <= set(override):
            return UniversalRankingPolicy(strategy, RankingProfile(), "rejected_missing_governance")
        try:
            expires = dt.datetime.fromisoformat(str(override["expires_at"]).replace("Z", "+00:00"))
        except ValueError:
            return UniversalRankingPolicy(strategy, RankingProfile(), "rejected_invalid_expiry")
        now = dt.datetime.now(dt.timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.timezone.utc)
        if expires <= now:
            return UniversalRankingPolicy(strategy, RankingProfile(), "rejected_expired")
        defaults = RankingProfile()
        weights = dict(defaults.weights)
        for key, value in dict(override.get("weights") or {}).items():
            if key not in weights:
                continue
            try:
                weights[key] = float(value)
            except (TypeError, ValueError):
                continue
        metadata = {key: str(override[key]) for key in sorted(_REQUIRED_OVERRIDE_FIELDS)}
        profile = RankingProfile(
            profile_id=f"experimental:{metadata['version']}",
            weights=weights,
            diversification_enabled=bool(override.get("diversification_enabled", True)),
            overrides_enabled=True,
            override_metadata=metadata,
        )
        return UniversalRankingPolicy(strategy, profile, "active_experimental_override")


def get_codecompass_universal_ranking_profile_service() -> CodeCompassUniversalRankingProfileService:
    return CodeCompassUniversalRankingProfileService()

"""Detect cognitive-style measurement drift without mutating active profiles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from agent.services.cognitive_style_benchmark_service import CognitiveStyleBenchmarkSuite
from ananta_contracts.cognitive_style import (
    StyleMeasurementContext,
    StyleProfileDriftEntry,
    StyleProfileDriftReport,
)
from ananta_contracts.model_selection import AgentStyleProfile


class CognitiveStyleDriftService:
    def evaluate(
        self,
        *,
        profiles: Iterable[AgentStyleProfile],
        contexts: Iterable[StyleMeasurementContext],
        stale_after_days: int = 90,
    ) -> StyleProfileDriftReport:
        known = tuple(profiles)
        entries = tuple(
            self._entry(known, context, stale_after_days=max(1, stale_after_days))
            for context in contexts
        )
        return StyleProfileDriftReport(
            benchmark_revision=CognitiveStyleBenchmarkSuite.REVISION,
            entries=entries,
            rebenchmark_due_count=sum(item.rebenchmark_due for item in entries),
        )

    def _entry(
        self,
        profiles: tuple[AgentStyleProfile, ...],
        context: StyleMeasurementContext,
        *,
        stale_after_days: int,
    ) -> StyleProfileDriftEntry:
        candidates = tuple(
            item for item in profiles
            if item.model_profile_id == context.model_profile_id
        )
        profile = max(candidates, key=lambda item: item.measured_at, default=None)
        if profile is None:
            return StyleProfileDriftEntry(
                model_profile_id=context.model_profile_id,
                status="missing",
                rebenchmark_due=True,
                reason_codes=("style_profile_missing",),
            )
        common = {
            "model_profile_id": context.model_profile_id,
            "active_profile_id": profile.profile_id,
            "measured_at": profile.measured_at,
        }
        if profile.model_revision != context.model_revision:
            return StyleProfileDriftEntry(
                **common, status="model_revision_drift", rebenchmark_due=True,
                reason_codes=("style_model_revision_changed",),
            )
        context_pairs = (
            (profile.quantization, context.quantization),
            (profile.runtime, context.runtime),
            (profile.backend_id, context.backend_id),
            (profile.prompt_digest, context.system_prompt_digest),
            (profile.role_prompt_digest, context.role_prompt_digest),
            (profile.tool_mode, context.tool_mode),
            (profile.sampling_digest, context.sampling_digest),
        )
        if any(current != measured for current, measured in context_pairs):
            return StyleProfileDriftEntry(
                **common, status="measurement_context_drift", rebenchmark_due=True,
                reason_codes=("style_measurement_context_changed",),
            )
        if profile.benchmark_revision != CognitiveStyleBenchmarkSuite.REVISION:
            return StyleProfileDriftEntry(
                **common, status="benchmark_revision_drift", rebenchmark_due=True,
                reason_codes=("style_benchmark_revision_changed",),
            )
        now = datetime.now(timezone.utc)
        try:
            measured = datetime.fromisoformat(profile.measured_at.replace("Z", "+00:00"))
            age_days = (now - measured).total_seconds() / 86400
        except ValueError:
            age_days = float("inf")
        if profile.expires_at:
            try:
                expires = datetime.fromisoformat(profile.expires_at.replace("Z", "+00:00"))
            except ValueError:
                expires = now
            if expires <= now:
                return StyleProfileDriftEntry(
                    **common, status="expired", rebenchmark_due=True,
                    reason_codes=("style_profile_expired",),
                )
        if age_days >= stale_after_days:
            return StyleProfileDriftEntry(
                **common, status="stale", rebenchmark_due=True,
                reason_codes=("style_profile_stale",),
            )
        return StyleProfileDriftEntry(
            **common, status="current", rebenchmark_due=False,
        )


__all__ = ["CognitiveStyleDriftService"]

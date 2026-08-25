"""Plan revision-safe Hub benchmark batches for drifted style profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agent.services.cognitive_style_benchmark_service import CognitiveStyleBenchmarkSuite
from agent.services.cognitive_style_drift_service import CognitiveStyleDriftService
from agent.services.model_profile_loader import ModelProfile
from ananta_contracts.cognitive_style import (
    StyleBenchmarkPlan,
    StyleProfileDriftReport,
    StyleRebenchmarkDueCommand,
)
from ananta_contracts.model_selection import AgentStyleProfile


@dataclass(frozen=True, slots=True)
class CognitiveStyleRebenchmarkWorkItem:
    profile: ModelProfile
    plan: StyleBenchmarkPlan


@dataclass(frozen=True, slots=True)
class CognitiveStyleRebenchmarkSchedule:
    drift: StyleProfileDriftReport
    work_items: tuple[CognitiveStyleRebenchmarkWorkItem, ...]
    skipped_profile_ids: tuple[str, ...]


class CognitiveStyleRebenchmarkPlanner:
    """Pure planner; job ownership and execution remain in the Hub."""

    def plan(
        self,
        *,
        command: StyleRebenchmarkDueCommand,
        style_profiles: Iterable[AgentStyleProfile],
        model_profiles: Iterable[ModelProfile],
    ) -> CognitiveStyleRebenchmarkSchedule:
        drift = CognitiveStyleDriftService().evaluate(
            profiles=style_profiles,
            contexts=command.contexts,
            stale_after_days=command.stale_after_days,
        )
        profiles_by_id = {item.profile_id: item for item in model_profiles}
        contexts_by_id = {
            context.model_profile_id: context for context in command.contexts
        }
        work: list[CognitiveStyleRebenchmarkWorkItem] = []
        skipped: list[str] = []
        for entry in drift.entries:
            if not entry.rebenchmark_due:
                continue
            profile = profiles_by_id.get(entry.model_profile_id)
            context = contexts_by_id.get(entry.model_profile_id)
            if profile is None or context is None:
                skipped.append(entry.model_profile_id)
                continue
            work.append(CognitiveStyleRebenchmarkWorkItem(
                profile=profile,
                plan=CognitiveStyleBenchmarkSuite.plan(
                    context,
                    repeats=command.repeats,
                    seeds=command.seeds,
                    temperatures=command.temperatures,
                ),
            ))
        return CognitiveStyleRebenchmarkSchedule(
            drift=drift,
            work_items=tuple(work),
            skipped_profile_ids=tuple(skipped),
        )


__all__ = [
    "CognitiveStyleRebenchmarkPlanner", "CognitiveStyleRebenchmarkSchedule",
    "CognitiveStyleRebenchmarkWorkItem",
]

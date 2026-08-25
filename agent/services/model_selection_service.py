"""Hub-owned model consumer, assignment and cognitive-style policies."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Iterable, Protocol

from ananta_contracts.model_selection import (
    AgentStyleProfile,
    ModelAssignment,
    ModelConsumer,
    ModelFallbackGroup,
    ModelRoutingConfiguration,
    ModelRoutingMutationCommand,
    RoleStyleTarget,
    StyleRange,
)


class ModelRoutingConflict(RuntimeError):
    def __init__(self, reason_code: str, *, current_revision: int) -> None:
        self.reason_code = reason_code
        self.current_revision = current_revision
        super().__init__(reason_code)


class ModelRoutingConfigurationPort(Protocol):
    def load(self) -> ModelRoutingConfiguration: ...
    def save_if_revision(
        self, expected_revision: int, value: ModelRoutingConfiguration
    ) -> bool: ...


class ModelConsumerRegistry:
    """Immutable Hub registry; workers cannot register consumers."""

    def __init__(self, consumers: Iterable[ModelConsumer]) -> None:
        values = tuple(consumers)
        indexed = {item.consumer_id: item for item in values}
        if len(indexed) != len(values):
            raise ValueError("model_consumer_duplicate")
        self._consumers = indexed

    @classmethod
    def defaults(cls) -> "ModelConsumerRegistry":
        def item(identifier: str, label: str, category: str, *caps: str) -> ModelConsumer:
            return ModelConsumer(
                consumer_id=identifier,
                label=label,
                category=category,
                required_capabilities=tuple(caps),
                allowed_scopes=("global", "organization", "project", "workflow", "agent", "role", "task_kind", "step"),
            )
        return cls((
            item("task.planning", "Planung", "tasks", "reasoning"),
            item("task.coding", "Coding", "tasks", "code"),
            item("task.debugging", "Debugging", "tasks", "code"),
            item("task.review", "Review", "tasks", "reasoning"),
            item("task.research", "Research", "tasks", "reasoning"),
            item("task.repo_analysis", "Repository-Analyse", "tasks", "code"),
            item("chat.ai_snake", "AI-Snake", "chat", "chat"),
            item("planning.autoplanner", "Auto-Planner", "planning", "reasoning"),
            item("evaluation.judge", "Evaluator", "evaluation", "json"),
            item("voice.corrector", "Voice-Korrektur", "voice", "chat"),
            item("knowledge.embedding", "Embeddings", "knowledge", "embeddings"),
            item("evolver.proposal", "Evolver", "evolution", "reasoning"),
        ))

    def all(self) -> tuple[ModelConsumer, ...]:
        return tuple(sorted(self._consumers.values(), key=lambda item: item.consumer_id))

    def require(self, consumer_id: str) -> ModelConsumer:
        try:
            return self._consumers[consumer_id]
        except KeyError as exc:
            raise ValueError("model_consumer_unknown") from exc


class InMemoryModelRoutingConfigurationRepository:
    """Test adapter; production supplies an atomic persistence port."""

    def __init__(self, initial: ModelRoutingConfiguration | None = None) -> None:
        self._value = initial or ModelRoutingConfiguration(revision=0)
        self._lock = RLock()

    def load(self) -> ModelRoutingConfiguration:
        with self._lock:
            return self._value

    def save_if_revision(self, expected_revision: int, value: ModelRoutingConfiguration) -> bool:
        with self._lock:
            if self._value.revision != expected_revision:
                return False
            self._value = value
            return True


class ModelRoutingAssignmentService:
    def __init__(
        self,
        *,
        repository: ModelRoutingConfigurationPort,
        consumers: ModelConsumerRegistry,
        known_profile_ids: Iterable[str],
    ) -> None:
        self._repository = repository
        self._consumers = consumers
        self._profiles = frozenset(str(value) for value in known_profile_ids)

    def read(self) -> ModelRoutingConfiguration:
        return self._repository.load()

    def validate(self, command: ModelRoutingMutationCommand) -> None:
        self._validate(command.assignments, command.fallback_groups)

    def apply(self, command: ModelRoutingMutationCommand) -> ModelRoutingConfiguration:
        current = self._repository.load()
        if current.revision != command.expected_revision:
            raise ModelRoutingConflict(
                "model_routing_revision_conflict", current_revision=current.revision
            )
        self.validate(command)
        updated = ModelRoutingConfiguration(
            revision=current.revision + 1,
            assignments=command.assignments,
            fallback_groups=command.fallback_groups,
        )
        if not self._repository.save_if_revision(current.revision, updated):
            actual = self._repository.load().revision
            raise ModelRoutingConflict(
                "model_routing_revision_conflict", current_revision=actual
            )
        return updated

    def _validate(
        self,
        assignments: tuple[ModelAssignment, ...],
        groups: tuple[ModelFallbackGroup, ...],
    ) -> None:
        for assignment in assignments:
            consumer = self._consumers.require(assignment.consumer_id)
            if assignment.scope not in consumer.allowed_scopes:
                raise ValueError("model_assignment_scope_not_allowed")
            if assignment.profile_id and assignment.profile_id not in self._profiles:
                raise ValueError("model_assignment_profile_unknown")
        for group in groups:
            if any(item.profile_id not in self._profiles for item in group.candidates):
                raise ValueError("model_fallback_profile_unknown")


@dataclass(frozen=True, slots=True)
class StyleFitDecision:
    score: float
    confidence: float
    contributions: tuple[tuple[str, float], ...]
    eligible: bool = True
    grants_authority: bool = False


class CognitiveStyleFitPolicy:
    """Pure soft-ranking policy. It cannot grant capabilities or authority."""

    @staticmethod
    def _dimension(value: float, target: StyleRange) -> float:
        if target.minimum <= value <= target.maximum:
            return 1.0
        distance = target.minimum - value if value < target.minimum else value - target.maximum
        return max(0.0, 1.0 - distance)

    def evaluate(self, profile: AgentStyleProfile, target: RoleStyleTarget) -> StyleFitDecision:
        pairs = (
            ("rule_correctness", profile.scores.rule_correctness, target.rule_correctness),
            ("truth_exploration", profile.scores.truth_exploration, target.truth_exploration),
            ("initiative_assertiveness", profile.scores.initiative_assertiveness, target.initiative_assertiveness),
        )
        weighted = tuple((name, self._dimension(value, expected) * expected.weight) for name, value, expected in pairs)
        denominator = sum(expected.weight for _, _, expected in pairs)
        score = sum(value for _, value in weighted) / denominator if denominator else 0.0
        return StyleFitDecision(
            score=round(score * profile.confidence, 6),
            confidence=profile.confidence,
            contributions=weighted,
        )


__all__ = [
    "CognitiveStyleFitPolicy", "InMemoryModelRoutingConfigurationRepository",
    "ModelConsumerRegistry", "ModelRoutingAssignmentService",
    "ModelRoutingConflict", "StyleFitDecision",
]

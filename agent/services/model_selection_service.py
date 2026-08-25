"""Hub-owned model consumer, assignment and cognitive-style policies."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
import re
from typing import Iterable, Protocol

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    ResolutionResult,
    RoutingContext,
)
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    EffectiveModelRoute,
    ModelAssignment,
    ModelConsumer,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingConfiguration,
    ModelRouteDecision,
    ModelRoutingDryRunCommand,
    ModelRoutingMutationCommand,
    RoleStyleTarget,
    StyleRange,
    ModelRoutingValidationIssue,
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


class ModelConsumerExtensionPort(Protocol):
    namespace: str

    def consumers(self) -> Iterable[ModelConsumer]: ...


class ModelRoutingValidationPort(Protocol):
    def validate(
        self,
        assignments: tuple[ModelAssignment, ...],
        groups: tuple[ModelFallbackGroup, ...],
    ) -> tuple[ModelRoutingValidationIssue, ...]: ...


class ModelConsumerRegistry:
    """Immutable Hub registry; workers cannot register consumers."""

    def __init__(self, consumers: Iterable[ModelConsumer]) -> None:
        values = tuple(consumers)
        indexed = {item.consumer_id: item for item in values}
        if len(indexed) != len(values):
            raise ValueError("model_consumer_duplicate")
        self._consumers = indexed

    @classmethod
    def defaults(
        cls,
        extensions: Iterable[ModelConsumerExtensionPort] = (),
    ) -> "ModelConsumerRegistry":
        scopes = (
            "global", "organization", "project", "workflow", "agent", "role",
            "task_kind", "step",
        )

        def item(
            identifier: str,
            label: str,
            category: str,
            *caps: str,
            role: str = "any",
            legacy: tuple[str, ...] = (),
        ) -> ModelConsumer:
            return ModelConsumer(
                consumer_id=identifier,
                label=label,
                category=category,
                required_capabilities=tuple(caps),
                allowed_scopes=scopes,
                default_model_role=role,
                legacy_config_paths=legacy,
            )

        def external(
            identifier: str,
            label: str,
            category: str,
            reason: str,
            *caps: str,
            legacy: tuple[str, ...] = (),
        ) -> ModelConsumer:
            return ModelConsumer(
                consumer_id=identifier,
                label=label,
                category=category,
                required_capabilities=tuple(caps),
                allowed_scopes=(),
                routable=False,
                legacy_config_paths=legacy,
                non_routable_reason=reason,
            )

        builtins = [
            item("task.planning", "Planung", "tasks", "reasoning", role="planner"),
            item("task.coding", "Coding", "tasks", "code", role="coder"),
            item("task.debugging", "Debugging", "tasks", "code", role="coder"),
            item("task.review", "Review", "tasks", "reasoning", role="reviewer"),
            item("task.research", "Research", "tasks", "reasoning", role="reasoning"),
            item("task.repo_analysis", "Repository-Analyse", "tasks", "code", role="coder"),
            item("task.summarization", "Zusammenfassung", "tasks", "chat", role="summarizer"),
            item("chat.ai_snake", "AI-Snake", "chat", "chat", role="chat", legacy=("hub_copilot",)),
            item("chat.general", "Allgemeiner Chat", "chat", "chat", role="chat", legacy=("default_provider", "default_model")),
            item("chat.code_help", "Code-Hilfe", "chat", "code", role="coder"),
            item("planning.autoplanner", "Auto-Planner", "planning", "reasoning", role="planner", legacy=("planning",)),
            item("planning.goal", "Goal-Planung", "planning", "reasoning", role="planner"),
            item("planning.context_compaction", "Planungskontext-Kompaktierung", "planning", "chat", role="summarizer"),
            item("evaluation.judge", "Evaluator", "evaluation", "json", role="reviewer"),
            item("evaluation.verifier", "Verifikation", "evaluation", "reasoning", role="reviewer"),
            item("research.synthesis", "Research-Synthese", "research", "reasoning", role="reasoning"),
            item("knowledge.rag_answer", "RAG-Antwort", "knowledge", "chat", role="chat"),
            item("visual_process.step", "Visual-Process-Schritt", "visual_process", "reasoning"),
            item("voice.corrector", "Voice-Korrektur", "voice", "chat", role="chat"),
            item("evolver.proposal", "Evolver", "evolution", "reasoning", role="reasoning"),
            external(
                "knowledge.embedding", "Embeddings", "knowledge",
                "dedicated_embedding_provider_domain", "embeddings",
                legacy=("knowledge_context.embedding",),
            ),
            external(
                "voice.transcription", "Voice-Transkription", "voice",
                "dedicated_voice_runtime_domain", "audio",
                legacy=("voice_runtime",),
            ),
            external(
                "vision.analysis", "Vision-Analyse", "vision",
                "vision_capability_not_in_model_profile_v1", "vision",
            ),
            external(
                "tiny_router.action", "Needle Tool-Router", "tools",
                "dedicated_tiny_router_domain", "tools",
                legacy=("tiny_action_model_profiles",),
            ),
        ]
        builtins.extend(cls._plugin_consumers(extensions))
        return cls(builtins)

    @staticmethod
    def _plugin_consumers(
        extensions: Iterable[ModelConsumerExtensionPort],
    ) -> list[ModelConsumer]:
        result: list[ModelConsumer] = []
        for extension in extensions:
            namespace = str(extension.namespace or "").strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", namespace):
                raise ValueError("model_consumer_extension_namespace_invalid")
            prefix = f"plugin.{namespace}."
            for consumer in extension.consumers():
                if not consumer.consumer_id.startswith(prefix):
                    raise ValueError("model_consumer_extension_id_not_namespaced")
                result.append(consumer.model_copy(update={
                    "registration_source": f"plugin:{namespace}",
                }))
        return result

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
        known_models: Iterable[tuple[str, str]] = (),
        validation_policy: ModelRoutingValidationPort | None = None,
    ) -> None:
        self._repository = repository
        self._consumers = consumers
        self._profiles = frozenset(str(value) for value in known_profile_ids)
        self._models = frozenset(
            (str(provider_id), str(model_id))
            for provider_id, model_id in known_models
        )
        self._validation_policy = validation_policy

    def read(self) -> ModelRoutingConfiguration:
        return self._repository.load()

    def validate(self, command: ModelRoutingMutationCommand) -> None:
        issues = self.validation_issues(command)
        error = next((item for item in issues if item.severity == "error"), None)
        if error is not None:
            raise ValueError(error.reason_code)

    def validation_issues(
        self, command: ModelRoutingMutationCommand
    ) -> tuple[ModelRoutingValidationIssue, ...]:
        issues = list(self._identity_issues(
            command.assignments, command.fallback_groups
        ))
        if self._validation_policy is not None:
            issues.extend(self._validation_policy.validate(
                command.assignments, command.fallback_groups
            ))
        return tuple(issues)

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

    def _identity_issues(
        self,
        assignments: tuple[ModelAssignment, ...],
        groups: tuple[ModelFallbackGroup, ...],
    ) -> tuple[ModelRoutingValidationIssue, ...]:
        issues: list[ModelRoutingValidationIssue] = []

        def error(reason: str, reference: str | None = None) -> None:
            issues.append(ModelRoutingValidationIssue(
                severity="error", reason_code=reason, reference=reference
            ))

        for assignment in assignments:
            try:
                consumer = self._consumers.require(assignment.consumer_id)
            except ValueError:
                error("model_consumer_unknown", assignment.consumer_id)
                continue
            if not consumer.routable:
                error("model_consumer_not_routable", assignment.consumer_id)
            if assignment.scope not in consumer.allowed_scopes:
                error("model_assignment_scope_not_allowed", assignment.consumer_id)
            if assignment.profile_id and assignment.profile_id not in self._profiles:
                error("model_assignment_profile_unknown", assignment.profile_id)
            if (
                assignment.mode == "model"
                and (assignment.provider_id, assignment.model_id) not in self._models
            ):
                error(
                    "model_assignment_model_unknown",
                    f"{assignment.provider_id}:{assignment.model_id}",
                )
        for group in groups:
            for item in group.candidates:
                if item.profile_id not in self._profiles:
                    error("model_fallback_profile_unknown", item.profile_id)
            if (
                group.escalation_profile_id
                and group.escalation_profile_id not in self._profiles
            ):
                error(
                    "model_fallback_escalation_profile_unknown",
                    group.escalation_profile_id,
                )
        return tuple(issues)


class EffectiveModelRoutingService:
    """Projects Hub-owned assignments into the canonical technical resolver."""

    _SCOPE_PRECEDENCE = (
        ("step", "step_id"),
        ("task_kind", "task_kind"),
        ("role", "role_id"),
        ("agent", "agent_id"),
        ("workflow", "workflow_id"),
        ("project", "project_id"),
        ("organization", "organization_id"),
    )
    _CONSUMER_MODEL_ROLES = {
        "task.planning": "planner",
        "task.coding": "coder",
        "task.debugging": "coder",
        "task.review": "reviewer",
        "task.research": "reasoning",
        "task.repo_analysis": "coder",
        "chat.ai_snake": "chat",
        "planning.autoplanner": "planner",
        "evaluation.judge": "reviewer",
        "voice.corrector": "chat",
        "knowledge.embedding": "embedder",
        "evolver.proposal": "reasoning",
    }

    def __init__(
        self,
        *,
        repository: ModelRoutingConfigurationPort,
        consumers: ModelConsumerRegistry,
        resolver: ModelProfileResolver,
    ) -> None:
        self._repository = repository
        self._consumers = consumers
        self._resolver = resolver

    def dry_run(self, command: ModelRoutingDryRunCommand) -> EffectiveModelRoute:
        route, _candidates = self.resolve_route(command)
        return route

    def resolve_route(
        self,
        command: ModelRoutingDryRunCommand,
        *,
        base_context: RoutingContext | None = None,
    ) -> tuple[EffectiveModelRoute, list[ModelProfile]]:
        """Resolve once for both previews and Hub-signed runtime plans."""

        consumer = self._consumers.require(command.consumer_id)
        if not consumer.routable:
            return EffectiveModelRoute(
                configuration_revision=(
                    command.configuration or self._repository.load()
                ).revision,
                consumer_id=consumer.consumer_id,
                assignment_source="consumer_registry",
                assignment_mode="disabled",
                decisions=(ModelRouteDecision(
                    rank=0,
                    source="consumer_registry",
                    profile_id=None,
                    accepted=False,
                    reason=consumer.non_routable_reason or "model_consumer_not_routable",
                ),),
                executable=False,
            ), []
        configuration = command.configuration or self._repository.load()
        assignment, source, inheritance_sources = self._effective_assignment(
            configuration, command
        )
        if assignment.mode == "disabled":
            return EffectiveModelRoute(
                configuration_revision=configuration.revision,
                consumer_id=consumer.consumer_id,
                assignment_source=source,
                inheritance_sources=inheritance_sources,
                assignment_mode="disabled",
                fallback_group_id=assignment.fallback_group_id,
                maximum_total_retries=0,
                decisions=(ModelRouteDecision(
                    rank=0,
                    source="hub_assignment",
                    profile_id=None,
                    accepted=False,
                    reason="model_routing_consumer_disabled",
                ),),
                executable=False,
            ), []

        requested_profile = self._requested_profile(assignment)
        fallback = next(
            (
                group for group in configuration.fallback_groups
                if group.group_id == assignment.fallback_group_id
            ),
            None,
        )
        fallback_candidates = list(fallback.candidates) if fallback else []
        if fallback and fallback.escalation_profile_id:
            fallback_candidates.append(ModelFallbackCandidate(
                profile_id=fallback.escalation_profile_id,
                cloud_allowed=command.allow_cloud,
            ))
        base = base_context or RoutingContext()
        configured_role = consumer.default_model_role or self._CONSUMER_MODEL_ROLES.get(
            consumer.consumer_id, "any"
        )
        model_role = (
            base.model_role
            if base.model_role and base.model_role != "any"
            else configured_role
        )
        explicit_profile_id = requested_profile.profile_id if requested_profile else None
        request_profile_id = (
            explicit_profile_id
            if assignment.mode in {"profile", "model"}
            else base.request_profile_id
        )
        context = RoutingContext(
            model_role=model_role,
            blueprint_id=base.blueprint_id,
            template_id=base.template_id,
            team_id=base.team_id,
            task_kind=command.task_kind or base.task_kind,
            risk_class=command.risk_class or base.risk_class,
            context_text=base.context_text,
            request_profile_id=request_profile_id,
            user_profile_id=base.user_profile_id,
            env_profile_id=base.env_profile_id,
            requires_tools=(
                command.requires_tools
                or base.requires_tools
                or "tools" in consumer.required_capabilities
            ),
            requires_json=(
                command.requires_json
                or base.requires_json
                or "json" in consumer.required_capabilities
            ),
            requires_streaming=command.requires_streaming or base.requires_streaming,
            approximate_context_tokens=(
                command.approximate_context_tokens
                if command.approximate_context_tokens > 0
                else base.approximate_context_tokens
            ),
            contains_secrets=command.contains_secrets or base.contains_secrets,
            data_class=command.data_class,
            step_kind=base.step_kind,
            allow_cloud=(
                command.allow_cloud
                if base_context is None
                else command.allow_cloud and base.allow_cloud
            ),
            fallback_group_id=(
                fallback.group_id if fallback else base.fallback_group_id
            ),
            fallback_profile_ids=tuple(item.profile_id for item in fallback_candidates),
            fallback_candidate_max_context_tokens={
                item.profile_id: item.max_context_tokens
                for item in fallback_candidates
                if item.max_context_tokens is not None
            } if fallback else {},
            fallback_candidate_cloud_allowed={
                item.profile_id: item.cloud_allowed for item in fallback_candidates
            } if fallback else {},
            fallback_candidate_retry_budgets={
                item.profile_id: item.retry_budget for item in fallback_candidates
            } if fallback else {},
            fallback_candidate_triggers={
                item.profile_id: item.triggers for item in fallback_candidates
                if item.triggers
            } if fallback else {},
            fallback_candidate_max_costs={
                item.profile_id: item.max_estimated_cost_per_step
                for item in fallback_candidates
                if item.max_estimated_cost_per_step is not None
            } if fallback else {},
            fallback_candidate_requires_tools={
                item.profile_id: item.requires_tools for item in fallback_candidates
            } if fallback else {},
            fallback_candidate_requires_json={
                item.profile_id: item.requires_json for item in fallback_candidates
            } if fallback else {},
            fallback_max_total_retries=(fallback.max_total_retries if fallback else None),
            fallback_stop_on_policy_block=(
                fallback.stop_on_policy_block
                if fallback
                else base.fallback_stop_on_policy_block
            ),
            max_estimated_cost_per_step=base.max_estimated_cost_per_step,
            previous_error_type=base.previous_error_type,
            repeated_failure_count=base.repeated_failure_count,
            metadata={
                **dict(base.metadata),
                "style_role_id": command.role_id or model_role,
                "style_project_id": command.project_id,
                "style_organization_id": command.organization_id,
            },
        )
        result, candidates = self._resolver.resolve_candidate_chain(context)
        route = self._read_model(
            configuration=configuration,
            consumer=consumer,
            assignment=assignment,
            assignment_source=source,
            inheritance_sources=inheritance_sources,
            result=result,
            candidates=candidates,
            maximum_total_retries=(
                fallback.max_total_retries if fallback else None
            ),
        )
        return route, candidates

    def _effective_assignment(
        self,
        configuration: ModelRoutingConfiguration,
        command: ModelRoutingDryRunCommand,
    ) -> tuple[ModelAssignment, str, tuple[str, ...]]:
        assignments = tuple(
            item for item in configuration.assignments
            if item.consumer_id == command.consumer_id
        )
        inheritance_sources: list[str] = []
        inherited_fallback_group_id: str | None = None
        for scope, field_name in self._SCOPE_PRECEDENCE:
            scope_id = getattr(command, field_name)
            if not scope_id:
                continue
            assignment = next(
                (
                    item for item in assignments
                    if item.scope == scope and item.scope_id == scope_id
                ),
                None,
            )
            if assignment is None:
                continue
            scoped_source = f"{scope}:{scope_id}"
            if assignment.mode == "inherit":
                inheritance_sources.append(scoped_source)
                inherited_fallback_group_id = (
                    inherited_fallback_group_id or assignment.fallback_group_id
                )
                continue
            effective = (
                assignment.model_copy(update={
                    "fallback_group_id": inherited_fallback_group_id,
                })
                if inherited_fallback_group_id
                else assignment
            )
            return effective, scoped_source, tuple(inheritance_sources)
        global_assignment = next(
            (
                item for item in assignments
                if item.scope == "global" and item.scope_id == "global"
            ),
            None,
        )
        if global_assignment is not None and global_assignment.mode != "inherit":
            effective = (
                global_assignment.model_copy(update={
                    "fallback_group_id": inherited_fallback_group_id,
                })
                if inherited_fallback_group_id
                else global_assignment
            )
            return effective, "global", tuple(inheritance_sources)
        if global_assignment is not None:
            inheritance_sources.append("global")
            inherited_fallback_group_id = (
                inherited_fallback_group_id or global_assignment.fallback_group_id
            )
        return ModelAssignment(
            consumer_id=command.consumer_id,
            scope="global",
            mode="inherit",
            fallback_group_id=inherited_fallback_group_id,
        ), "resolver_default", tuple(inheritance_sources)

    def _requested_profile(self, assignment: ModelAssignment) -> ModelProfile | None:
        if assignment.mode == "profile" and assignment.profile_id:
            return self._resolver.profile_by_id(assignment.profile_id)
        if assignment.mode == "model" and assignment.provider_id and assignment.model_id:
            return self._resolver.profile_for_model(assignment.provider_id, assignment.model_id)
        return None

    @staticmethod
    def _read_model(
        *,
        configuration: ModelRoutingConfiguration,
        consumer: ModelConsumer,
        assignment: ModelAssignment,
        assignment_source: str,
        inheritance_sources: tuple[str, ...],
        result: ResolutionResult,
        candidates: list[ModelProfile],
        maximum_total_retries: int | None,
    ) -> EffectiveModelRoute:
        profile = result.profile
        return EffectiveModelRoute(
            configuration_revision=configuration.revision,
            consumer_id=consumer.consumer_id,
            assignment_source=assignment_source,
            inheritance_sources=inheritance_sources,
            assignment_mode=assignment.mode,
            resolved_profile_id=profile.profile_id if profile else None,
            provider_id=profile.provider_id if profile else None,
            model_id=profile.model if profile else None,
            fallback_group_id=assignment.fallback_group_id,
            candidate_profile_ids=tuple(item.profile_id for item in candidates),
            blocked_candidates=tuple(result.blocked_candidates),
            decisions=tuple(ModelRouteDecision(
                rank=item.rank,
                source=item.source,
                profile_id=item.profile_id,
                accepted=item.accepted,
                reason=item.reason,
            ) for item in result.decisions),
            maximum_total_retries=maximum_total_retries,
            executable=profile is not None,
        )


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
        contributions = [
            (name, self._dimension(value, expected) * expected.weight)
            for name, value, expected in pairs
        ]
        denominator = sum(expected.weight for _, _, expected in pairs)
        score = sum(value for _, value in contributions) / denominator if denominator else 0.0
        values = {name: value for name, value, _expected in pairs}
        for name, expected in target.must_have.items():
            fit = self._dimension(values[name], expected)
            contributions.append((f"must_have:{name}", fit * expected.weight))
            denominator += expected.weight
            score = (
                (score * (denominator - expected.weight)) + fit * expected.weight
            ) / denominator if denominator else 0.0
        for name, ranges in target.avoid_ranges.items():
            for avoided in ranges:
                if avoided.minimum <= values[name] <= avoided.maximum:
                    penalty = max(avoided.weight, 1.0) / 10.0
                    contributions.append((f"avoid:{name}", -penalty))
                    score = max(0.0, score - penalty)
        return StyleFitDecision(
            score=round(score * profile.confidence, 6),
            confidence=profile.confidence,
            contributions=tuple(contributions),
            # Must-have and avoid ranges remain advisory: capability, safety and
            # permission eligibility is owned by the hard gates evaluated first.
            eligible=True,
        )


__all__ = [
    "CognitiveStyleFitPolicy", "EffectiveModelRoutingService",
    "InMemoryModelRoutingConfigurationRepository",
    "ModelConsumerExtensionPort", "ModelConsumerRegistry",
    "ModelRoutingAssignmentService", "ModelRoutingValidationPort",
    "ModelRoutingConflict", "StyleFitDecision",
]

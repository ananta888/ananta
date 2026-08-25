"""Safe, secret-free templates for the Hub-owned model-routing domain."""

from __future__ import annotations

from collections.abc import Iterable

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import ModelConsumerRegistry
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelConsumer,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingConfiguration,
    ModelRoutingTemplate,
    ModelRoutingTemplateCatalog,
    ModelRoutingValidationIssue,
)

_CLI_PROVIDERS = frozenset({
    "aider", "claude", "claude_code", "codex", "mistral_code", "opencode",
})
_QUALITY_RANK = {
    "frontier": 5, "high": 4, "medium_high": 3, "medium": 2,
    "low_medium": 1, "low": 0,
}


class ModelRoutingTemplateService:
    """Builds drafts only; persistence and policy remain separate services."""

    def __init__(
        self,
        *,
        consumers: ModelConsumerRegistry,
        profiles: Iterable[ModelProfile],
    ) -> None:
        self._consumers = consumers
        self._profiles = tuple(profile for profile in profiles if profile.enabled)

    def catalog(self, *, configuration_revision: int) -> ModelRoutingTemplateCatalog:
        return ModelRoutingTemplateCatalog(
            configuration_revision=configuration_revision,
            templates=(
                self._build(
                    "local-only", "Nur lokal",
                    "Explizite lokale Profile und ausschließlich lokale Fallbacks.",
                    configuration_revision,
                ),
                self._build(
                    "local-first-cloud-fallback", "Lokal zuerst, Cloud als Fallback",
                    "Lokale Primärprofile; erlaubte Cloud-Profile erst nach lokalen Kandidaten.",
                    configuration_revision,
                ),
                self._build(
                    "cloud-only", "Nur Cloud",
                    "Explizite, als Cloud erlaubt markierte Profile; Laufzeit-Policy bleibt bindend.",
                    configuration_revision,
                ),
                self._build(
                    "cli-first", "CLI zuerst",
                    "Registrierte CLI-Profile zuerst; lokale API-Profile dienen als Fallback.",
                    configuration_revision,
                ),
            ),
        )

    def _build(
        self,
        template_id: str,
        label: str,
        description: str,
        configuration_revision: int,
    ) -> ModelRoutingTemplate:
        primaries, fallbacks = self._profile_sets(template_id)
        assignments: list[ModelAssignment] = []
        groups: list[ModelFallbackGroup] = []
        issues: list[ModelRoutingValidationIssue] = []
        for consumer in self._consumers.all():
            compatible_primaries = self._compatible(consumer, primaries)
            if not compatible_primaries:
                issues.append(ModelRoutingValidationIssue(
                    severity="warning",
                    reason_code="model_routing_template_consumer_unresolved",
                    reference=consumer.consumer_id,
                ))
                continue
            primary = compatible_primaries[0]
            compatible_fallbacks = [
                profile for profile in self._compatible(consumer, fallbacks)
                if profile.profile_id != primary.profile_id
            ]
            group_id = None
            if compatible_fallbacks:
                group_id = f"template.{template_id}.{consumer.consumer_id}"
                groups.append(ModelFallbackGroup(
                    group_id=group_id,
                    candidates=tuple(self._candidate(profile) for profile in compatible_fallbacks),
                    max_total_retries=min(
                        64, sum(profile.retry_budget for profile in compatible_fallbacks)
                    ),
                ))
            assignments.append(ModelAssignment(
                consumer_id=consumer.consumer_id,
                scope="global",
                scope_id="global",
                mode="profile",
                profile_id=primary.profile_id,
                fallback_group_id=group_id,
            ))
        if not assignments:
            issues.append(ModelRoutingValidationIssue(
                severity="error",
                reason_code="model_routing_template_no_compatible_profiles",
                reference=template_id,
            ))
        return ModelRoutingTemplate(
            template_id=template_id,
            label=label,
            description=description,
            applicable=bool(assignments) and not any(issue.severity == "error" for issue in issues),
            configuration=ModelRoutingConfiguration(
                revision=configuration_revision,
                assignments=tuple(assignments),
                fallback_groups=tuple(groups),
            ),
            issues=tuple(issues),
        )

    def _profile_sets(
        self, template_id: str
    ) -> tuple[tuple[ModelProfile, ...], tuple[ModelProfile, ...]]:
        local = self._rank(profile for profile in self._profiles if not profile.is_cloud())
        cloud = self._rank(
            profile for profile in self._profiles
            if profile.is_cloud() and profile.cloud_allowed
        )
        cli = self._rank(
            profile for profile in self._profiles
            if profile.provider_id in _CLI_PROVIDERS
            or str(profile.extra.get("executor_id") or "").startswith("cli:")
        )
        if template_id == "local-only":
            return local, local
        if template_id == "local-first-cloud-fallback":
            return local, (*local, *cloud)
        if template_id == "cloud-only":
            return cloud, cloud
        return cli, (*cli, *local)

    @staticmethod
    def _rank(profiles: Iterable[ModelProfile]) -> tuple[ModelProfile, ...]:
        return tuple(sorted(profiles, key=lambda profile: (
            -_QUALITY_RANK.get(profile.quality_class, 2),
            profile.fallback_rank if profile.fallback_rank is not None else 10_000,
            profile.profile_id,
        )))

    @staticmethod
    def _compatible(
        consumer: ModelConsumer, profiles: Iterable[ModelProfile]
    ) -> list[ModelProfile]:
        def supports(profile: ModelProfile) -> bool:
            capabilities = set(consumer.required_capabilities)
            return (
                ("tools" not in capabilities or profile.supports_tools)
                and ("json" not in capabilities or profile.supports_json)
                and ("embeddings" not in capabilities or profile.model_role == "embedder")
                and ("code" not in capabilities or profile.model_role in {"any", "coder"})
            )

        role = {
            "task.planning": "planner", "planning.autoplanner": "planner",
            "task.coding": "coder", "task.debugging": "coder",
            "task.repo_analysis": "coder", "task.review": "reviewer",
            "evaluation.judge": "reviewer", "knowledge.embedding": "embedder",
        }.get(consumer.consumer_id)
        compatible = [profile for profile in profiles if supports(profile)]
        indexed = list(enumerate(compatible))
        return [
            profile for _index, profile in sorted(
                indexed,
                key=lambda item: (
                    item[1].model_role not in {"any", role}, item[0]
                ),
            )
        ]

    @staticmethod
    def _candidate(profile: ModelProfile) -> ModelFallbackCandidate:
        return ModelFallbackCandidate(
            profile_id=profile.profile_id,
            retry_budget=min(8, max(0, profile.retry_budget)),
            max_context_tokens=profile.max_context_for_profile,
            requires_tools=profile.supports_tools,
            requires_json=profile.supports_json,
            cloud_allowed=profile.is_cloud() and profile.cloud_allowed,
        )


__all__ = ["ModelRoutingTemplateService"]

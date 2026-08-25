"""Pure metadata and policy validation for Hub-owned model routing drafts."""

from __future__ import annotations

from collections.abc import Iterable

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import ModelConsumerRegistry
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelFallbackGroup,
    ModelRoutingValidationIssue,
)


class ModelRoutingValidationPolicy:
    """Validates hard profile constraints without persistence or I/O."""

    def __init__(
        self,
        *,
        consumers: ModelConsumerRegistry,
        profiles: Iterable[ModelProfile],
    ) -> None:
        self._consumers = consumers
        self._profiles = {profile.profile_id: profile for profile in profiles}
        self._models = {
            (profile.provider_id, profile.model): profile for profile in profiles
        }

    def validate(
        self,
        assignments: tuple[ModelAssignment, ...],
        groups: tuple[ModelFallbackGroup, ...],
    ) -> tuple[ModelRoutingValidationIssue, ...]:
        issues: list[ModelRoutingValidationIssue] = []
        groups_by_id = {group.group_id: group for group in groups}
        for assignment in assignments:
            consumer = self._consumers.require(assignment.consumer_id)
            profile = self._assignment_profile(assignment)
            if profile is not None:
                issues.extend(self._compatibility_issues(
                    profile,
                    consumer.required_capabilities,
                    reference=self._assignment_reference(assignment),
                ))
            if assignment.fallback_group_id:
                group = groups_by_id.get(assignment.fallback_group_id)
                if group is not None:
                    issues.extend(self._group_issues(
                        group,
                        required_capabilities=consumer.required_capabilities,
                    ))
        return self._unique(issues)

    def _assignment_profile(self, assignment: ModelAssignment) -> ModelProfile | None:
        if assignment.profile_id:
            return self._profiles.get(assignment.profile_id)
        if assignment.provider_id and assignment.model_id:
            return self._models.get((assignment.provider_id, assignment.model_id))
        return None

    def _group_issues(
        self,
        group: ModelFallbackGroup,
        *,
        required_capabilities: tuple[str, ...],
    ) -> list[ModelRoutingValidationIssue]:
        issues: list[ModelRoutingValidationIssue] = []
        for candidate in group.candidates:
            profile = self._profiles.get(candidate.profile_id)
            if profile is None:
                continue
            reference = f"{group.group_id}:{candidate.profile_id}"
            issues.extend(self._compatibility_issues(
                profile,
                required_capabilities,
                reference=reference,
            ))
            candidate_requirements = tuple(
                value for value, required in (
                    ("tools", candidate.requires_tools),
                    ("json", candidate.requires_json),
                ) if required
            )
            issues.extend(self._compatibility_issues(
                profile,
                candidate_requirements,
                reference=reference,
            ))
            if profile.is_cloud() and not candidate.cloud_allowed:
                issues.append(self._error(
                    "model_fallback_cloud_candidate_not_allowed", reference
                ))
            if (
                candidate.max_context_tokens is not None
                and candidate.max_context_tokens > profile.context_tokens
            ):
                issues.append(ModelRoutingValidationIssue(
                    severity="warning",
                    reason_code="model_fallback_context_limit_exceeds_profile",
                    reference=reference,
                ))
        if group.escalation_profile_id:
            escalation = self._profiles.get(group.escalation_profile_id)
            if escalation is not None:
                issues.extend(self._compatibility_issues(
                    escalation,
                    required_capabilities,
                    reference=f"{group.group_id}:escalation:{escalation.profile_id}",
                ))
        return issues

    @staticmethod
    def _supports(profile: ModelProfile, capability: str) -> bool:
        if capability == "tools":
            return profile.supports_tools or profile.supports_prompt_json_tools()
        if capability == "json":
            return profile.supports_json
        if capability == "code":
            return profile.model_role in {"any", "coder"}
        if capability == "embeddings":
            return profile.model_role == "embedder"
        if capability in {"chat", "reasoning"}:
            return profile.model_role != "embedder"
        return True

    def _compatibility_issues(
        self,
        profile: ModelProfile,
        capabilities: tuple[str, ...],
        *,
        reference: str,
    ) -> list[ModelRoutingValidationIssue]:
        return [
            self._error(
                f"model_profile_capability_mismatch:{capability}", reference
            )
            for capability in capabilities
            if not self._supports(profile, capability)
        ]

    @staticmethod
    def _assignment_reference(assignment: ModelAssignment) -> str:
        return f"{assignment.consumer_id}@{assignment.scope}:{assignment.scope_id}"

    @staticmethod
    def _error(reason_code: str, reference: str) -> ModelRoutingValidationIssue:
        return ModelRoutingValidationIssue(
            severity="error", reason_code=reason_code, reference=reference
        )

    @staticmethod
    def _unique(
        issues: list[ModelRoutingValidationIssue],
    ) -> tuple[ModelRoutingValidationIssue, ...]:
        indexed = {
            (issue.severity, issue.reason_code, issue.reference): issue
            for issue in issues
        }
        return tuple(indexed[key] for key in sorted(indexed))


__all__ = ["ModelRoutingValidationPolicy"]

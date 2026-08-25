"""Idempotent migration and shadow checks for legacy model picker fields."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import ModelRoutingAssignmentService
from ananta_contracts.model_selection import (
    LegacyModelMigrationEntry,
    ModelAssignment,
    ModelRoutingConfiguration,
    ModelRoutingLegacyMigrationApplyCommand,
    ModelRoutingLegacyMigrationPreview,
    ModelRoutingMutationCommand,
    ModelRoutingReleaseGateCheck,
    ModelRoutingReleaseGateReport,
    ModelRoutingShadowEntry,
    ModelRoutingShadowReport,
    ModelRoutingValidationIssue,
)


class ModelRoutingLegacyMigrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _LegacyChoice:
    consumer_id: str
    source: str
    provider_id: str | None
    model_id: str | None


class ModelRoutingLegacyMigrationService:
    """Maps exact legacy identities to profiles without overwriting Hub state."""

    def __init__(
        self,
        *,
        assignments: ModelRoutingAssignmentService,
        profiles: tuple[ModelProfile, ...],
        legacy_config: dict[str, Any],
    ) -> None:
        self._assignments = assignments
        self._profiles = profiles
        self._legacy_config = legacy_config

    def preview(self) -> ModelRoutingLegacyMigrationPreview:
        current = self._assignments.read()
        proposed = list(current.assignments)
        entries: list[LegacyModelMigrationEntry] = []
        issues: list[ModelRoutingValidationIssue] = []
        existing = {
            (item.consumer_id, item.scope, item.scope_id): item
            for item in current.assignments
        }
        for choice in self._legacy_choices():
            key = (choice.consumer_id, "global", "global")
            if key in existing:
                assigned = existing[key]
                entries.append(LegacyModelMigrationEntry(
                    consumer_id=choice.consumer_id,
                    legacy_source=choice.source,
                    legacy_provider_id=choice.provider_id,
                    legacy_model_id=choice.model_id,
                    matched_profile_id=assigned.profile_id,
                    status="preserved",
                    reason_code="legacy_migration_existing_assignment_preserved",
                ))
                continue
            entry = self._entry(choice)
            entries.append(entry)
            if entry.status == "proposed" and entry.matched_profile_id:
                proposed.append(ModelAssignment(
                    consumer_id=choice.consumer_id,
                    scope="global",
                    mode="profile",
                    profile_id=entry.matched_profile_id,
                ))
            elif entry.status in {"incomplete", "unresolved", "ambiguous"}:
                issues.append(ModelRoutingValidationIssue(
                    severity="error",
                    reason_code=entry.reason_code,
                    reference=choice.consumer_id,
                ))
        proposed_config = ModelRoutingConfiguration(
            revision=current.revision,
            assignments=tuple(proposed),
            fallback_groups=current.fallback_groups,
        )
        mutation = ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=current.revision,
            assignments=proposed_config.assignments,
            fallback_groups=proposed_config.fallback_groups,
        )
        issues.extend(self._assignments.validation_issues(mutation))
        applicable = not any(issue.severity == "error" for issue in issues)
        return ModelRoutingLegacyMigrationPreview(
            current_revision=current.revision,
            applicable=applicable,
            confirmation_digest=self._digest(mutation),
            entries=tuple(entries),
            proposed_configuration=proposed_config,
            issues=tuple(issues),
        )

    def apply(
        self,
        command: ModelRoutingLegacyMigrationApplyCommand,
    ) -> ModelRoutingConfiguration:
        preview = self.preview()
        if command.expected_revision != preview.current_revision:
            raise ModelRoutingLegacyMigrationError("model_routing_revision_conflict")
        if command.confirmation_digest != preview.confirmation_digest:
            raise ModelRoutingLegacyMigrationError(
                "model_routing_legacy_migration_confirmation_invalid"
            )
        if not preview.applicable:
            raise ModelRoutingLegacyMigrationError(
                "model_routing_legacy_migration_not_applicable"
            )
        current = self._assignments.read()
        if (
            current.assignments == preview.proposed_configuration.assignments
            and current.fallback_groups == preview.proposed_configuration.fallback_groups
        ):
            return current
        return self._assignments.apply(ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=command.expected_revision,
            assignments=preview.proposed_configuration.assignments,
            fallback_groups=preview.proposed_configuration.fallback_groups,
        ))

    def shadow_report(self) -> ModelRoutingShadowReport:
        current = self._assignments.read()
        assignments = {
            item.consumer_id: item
            for item in current.assignments
            if item.scope == "global" and item.scope_id == "global"
        }
        profiles = {profile.profile_id: profile for profile in self._profiles}
        entries = tuple(
            self._shadow_entry(choice, assignments.get(choice.consumer_id), profiles)
            for choice in self._legacy_choices()
        )
        return ModelRoutingShadowReport(
            configuration_revision=current.revision,
            matches=all(item.matches is not False for item in entries),
            entries=entries,
        )

    def release_gate(self) -> ModelRoutingReleaseGateReport:
        preview = self.preview()
        shadow = self.shadow_report()
        checks = (
            ModelRoutingReleaseGateCheck(
                check_id="legacy_migration_resolved",
                passed=preview.applicable,
                reason_code=(
                    "legacy_migration_resolved"
                    if preview.applicable else "legacy_migration_unresolved"
                ),
            ),
            ModelRoutingReleaseGateCheck(
                check_id="legacy_shadow_match",
                passed=shadow.matches,
                reason_code=(
                    "legacy_shadow_matches"
                    if shadow.matches else "legacy_shadow_mismatch"
                ),
            ),
            ModelRoutingReleaseGateCheck(
                check_id="routing_configuration_valid",
                passed=not any(
                    issue.severity == "error"
                    for issue in self._assignments.validation_issues(
                        ModelRoutingMutationCommand(
                            schema="ananta.model-routing-mutation-command.v1",
                            expected_revision=shadow.configuration_revision,
                            assignments=self._assignments.read().assignments,
                            fallback_groups=self._assignments.read().fallback_groups,
                        )
                    )
                ),
                reason_code="routing_configuration_valid",
            ),
        )
        return ModelRoutingReleaseGateReport(
            configuration_revision=shadow.configuration_revision,
            ready=all(check.passed for check in checks),
            checks=checks,
        )

    def _entry(self, choice: _LegacyChoice) -> LegacyModelMigrationEntry:
        base = dict(
            consumer_id=choice.consumer_id,
            legacy_source=choice.source,
            legacy_provider_id=choice.provider_id,
            legacy_model_id=choice.model_id,
        )
        if not choice.provider_id and not choice.model_id:
            return LegacyModelMigrationEntry(
                **base, status="missing", reason_code="legacy_model_selection_missing"
            )
        if not choice.provider_id or not choice.model_id:
            return LegacyModelMigrationEntry(
                **base, status="incomplete", reason_code="legacy_model_selection_incomplete"
            )
        matches = tuple(
            profile for profile in self._profiles
            if profile.provider_id.lower() == choice.provider_id.lower()
            and profile.model == choice.model_id
        )
        if not matches:
            return LegacyModelMigrationEntry(
                **base, status="unresolved", reason_code="legacy_model_profile_not_found"
            )
        if len(matches) > 1:
            return LegacyModelMigrationEntry(
                **base, status="ambiguous", reason_code="legacy_model_profile_ambiguous"
            )
        return LegacyModelMigrationEntry(
            **base,
            matched_profile_id=matches[0].profile_id,
            status="proposed",
            reason_code="legacy_model_profile_matched",
        )

    @staticmethod
    def _shadow_entry(
        choice: _LegacyChoice,
        assignment: ModelAssignment | None,
        profiles: dict[str, ModelProfile],
    ) -> ModelRoutingShadowEntry:
        base = dict(
            consumer_id=choice.consumer_id,
            legacy_provider_id=choice.provider_id,
            legacy_model_id=choice.model_id,
        )
        if not choice.provider_id and not choice.model_id:
            return ModelRoutingShadowEntry(
                **base, status="legacy_missing", matches=None
            )
        if assignment is None or assignment.mode == "inherit":
            return ModelRoutingShadowEntry(
                **base, status="central_missing", matches=False
            )
        if assignment.mode == "disabled":
            return ModelRoutingShadowEntry(
                **base, central_assignment_source="global",
                status="central_disabled", matches=False,
            )
        if assignment.mode == "model":
            provider_id, model_id = assignment.provider_id, assignment.model_id
        else:
            profile = profiles.get(str(assignment.profile_id or ""))
            if profile is None:
                return ModelRoutingShadowEntry(
                    **base, central_assignment_source="global",
                    status="central_profile_unknown", matches=False,
                )
            provider_id, model_id = profile.provider_id, profile.model
        matches = (
            str(provider_id).lower() == str(choice.provider_id).lower()
            and model_id == choice.model_id
        )
        return ModelRoutingShadowEntry(
            **base,
            central_provider_id=provider_id,
            central_model_id=model_id,
            central_assignment_source="global",
            status="match" if matches else "mismatch",
            matches=matches,
        )

    def _legacy_choices(self) -> tuple[_LegacyChoice, ...]:
        cfg = self._legacy_config
        llm = dict(cfg.get("llm_config") or {})
        copilot = dict(cfg.get("hub_copilot") or {})
        default_provider = self._text(cfg.get("default_provider"))
        default_model = self._text(cfg.get("default_model"))
        llm_provider = self._text(llm.get("provider")) or default_provider
        llm_model = self._text(llm.get("model")) or default_model
        copilot_provider = self._text(copilot.get("provider")) or llm_provider
        copilot_model = self._text(copilot.get("model")) or llm_model
        return (
            _LegacyChoice("chat.general", "default_provider/default_model", default_provider, default_model),
            _LegacyChoice("task.planning", "llm_config", llm_provider, llm_model),
            _LegacyChoice("planning.autoplanner", "hub_copilot", copilot_provider, copilot_model),
            _LegacyChoice("chat.ai_snake", "hub_copilot", copilot_provider, copilot_model),
        )

    @staticmethod
    def _text(value: object) -> str | None:
        return str(value).strip() or None if value is not None else None

    @staticmethod
    def _digest(command: ModelRoutingMutationCommand) -> str:
        payload = command.model_dump_json(by_alias=True)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "build_model_routing_legacy_migration_service",
    "ModelRoutingLegacyMigrationError",
    "ModelRoutingLegacyMigrationService",
]


def build_model_routing_legacy_migration_service(
    *,
    legacy_config: dict[str, Any],
    model_profiles_path: str,
) -> ModelRoutingLegacyMigrationService:
    """Compose the migration domain from production Hub adapters."""

    from agent.repositories.model_routing_configuration import (
        SqlModelRoutingConfigurationRepository,
    )
    from agent.services.model_profile_loader import ModelProfileLoader
    from agent.services.model_routing_validation_policy import (
        ModelRoutingValidationPolicy,
    )
    from agent.services.model_selection_service import ModelConsumerRegistry

    loaded = ModelProfileLoader().load_file(model_profiles_path) if model_profiles_path else None
    profiles = tuple(
        profile for profile in (loaded.profiles if loaded is not None else ())
        if profile.enabled
    )
    consumers = ModelConsumerRegistry.defaults()
    assignments = ModelRoutingAssignmentService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=consumers,
        known_profile_ids=(profile.profile_id for profile in profiles),
        known_models=((profile.provider_id, profile.model) for profile in profiles),
        validation_policy=ModelRoutingValidationPolicy(
            consumers=consumers,
            profiles=profiles,
        ),
    )
    return ModelRoutingLegacyMigrationService(
        assignments=assignments,
        profiles=profiles,
        legacy_config=legacy_config,
    )

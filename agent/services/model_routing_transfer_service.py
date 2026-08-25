"""Safe import/export lifecycle for Hub-owned model routing configuration."""

from __future__ import annotations

import hashlib

from agent.services.model_selection_service import (
    ModelRoutingAssignmentService,
)
from ananta_contracts.model_selection import (
    ModelRoutingConfiguration,
    ModelRoutingDiff,
    ModelRoutingExportBundle,
    ModelRoutingImportCommand,
    ModelRoutingImportPreview,
    ModelRoutingMutationCommand,
    ModelRoutingValidationIssue,
    ModelRoutingValidationReport,
)


class ModelRoutingConfirmationError(ValueError):
    pass


class ModelRoutingTransferService:
    """Coordinates validate-then-apply without owning persistence rules."""

    def __init__(self, assignments: ModelRoutingAssignmentService) -> None:
        self._assignments = assignments

    def export(self) -> ModelRoutingExportBundle:
        return ModelRoutingExportBundle(configuration=self._assignments.read())

    def validate(
        self,
        command: ModelRoutingMutationCommand,
    ) -> ModelRoutingValidationReport:
        current = self._assignments.read()
        issues: list[ModelRoutingValidationIssue] = []
        if command.expected_revision != current.revision:
            issues.append(ModelRoutingValidationIssue(
                severity="error",
                reason_code="model_routing_revision_conflict",
                reference=str(current.revision),
            ))
        issues.extend(self._assignments.validation_issues(command))
        return ModelRoutingValidationReport(
            valid=not any(issue.severity == "error" for issue in issues),
            expected_revision=command.expected_revision,
            current_revision=current.revision,
            issues=tuple(issues),
        )

    def preview(self, command: ModelRoutingImportCommand) -> ModelRoutingImportPreview:
        current = self._assignments.read()
        mutation = self._mutation(command)
        report = self.validate(mutation)
        return ModelRoutingImportPreview(
            current_revision=current.revision,
            source_revision=command.configuration.revision,
            applicable=report.valid,
            confirmation_digest=self._confirmation_digest(command),
            diff=self._diff(current, command.configuration),
            issues=report.issues,
        )

    def apply(self, command: ModelRoutingImportCommand) -> ModelRoutingConfiguration:
        expected_digest = self._confirmation_digest(command)
        if command.confirmation_digest != expected_digest:
            raise ModelRoutingConfirmationError(
                "model_routing_import_confirmation_invalid"
            )
        return self._assignments.apply(self._mutation(command))

    @staticmethod
    def _mutation(command: ModelRoutingImportCommand) -> ModelRoutingMutationCommand:
        return ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=command.expected_revision,
            assignments=command.configuration.assignments,
            fallback_groups=command.configuration.fallback_groups,
        )

    @staticmethod
    def _confirmation_digest(command: ModelRoutingImportCommand) -> str:
        payload = ModelRoutingMutationCommand(
            schema="ananta.model-routing-mutation-command.v1",
            expected_revision=command.expected_revision,
            assignments=command.configuration.assignments,
            fallback_groups=command.configuration.fallback_groups,
        ).model_dump_json(by_alias=True)
        return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _diff(
        current: ModelRoutingConfiguration,
        imported: ModelRoutingConfiguration,
    ) -> ModelRoutingDiff:
        def assignment_key(item) -> str:
            return f"{item.consumer_id}@{item.scope}:{item.scope_id}"

        current_assignments = {
            assignment_key(item): item for item in current.assignments
        }
        imported_assignments = {
            assignment_key(item): item for item in imported.assignments
        }
        current_groups = {item.group_id: item for item in current.fallback_groups}
        imported_groups = {item.group_id: item for item in imported.fallback_groups}

        return ModelRoutingDiff(
            added_assignment_keys=tuple(sorted(
                imported_assignments.keys() - current_assignments.keys()
            )),
            changed_assignment_keys=tuple(sorted(
                key for key in imported_assignments.keys() & current_assignments.keys()
                if imported_assignments[key] != current_assignments[key]
            )),
            removed_assignment_keys=tuple(sorted(
                current_assignments.keys() - imported_assignments.keys()
            )),
            added_fallback_group_ids=tuple(sorted(
                imported_groups.keys() - current_groups.keys()
            )),
            changed_fallback_group_ids=tuple(sorted(
                key for key in imported_groups.keys() & current_groups.keys()
                if imported_groups[key] != current_groups[key]
            )),
            removed_fallback_group_ids=tuple(sorted(
                current_groups.keys() - imported_groups.keys()
            )),
        )


__all__ = [
    "ModelRoutingConfirmationError",
    "ModelRoutingTransferService",
]

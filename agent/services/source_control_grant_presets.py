"""Immutable catalog of reviewed Source Control grant shapes."""

from __future__ import annotations

from agent.services.source_control_grant_admin_contracts import GrantPreset
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class SourceControlGrantPresetCatalog:
    """Immutable, read-only catalog of reviewed grant shapes."""

    _PRESETS = (
        GrantPreset(
            preset_id="worker_index_redacted",
            label="Redacted worker index",
            description="Index redacted source material on an authorized worker.",
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
            purpose="knowledge-index",
            consumption_mode="one_time",
            max_duration_seconds=86_400,
        ),
        GrantPreset(
            preset_id="chat_context_redacted",
            label="Redacted chat context",
            description="Provide redacted source context to an authorized model.",
            operation=GrantOperation.CHAT_CONTEXT,
            transformation=GrantTransformation.REDACTED,
            purpose="assisted_code_review",
            consumption_mode="reusable",
            max_duration_seconds=14_400,
        ),
        GrantPreset(
            preset_id="tool_read_summary",
            label="Summary-only tool read",
            description="Expose source summaries to an authorized tool target.",
            operation=GrantOperation.EXPORT,
            transformation=GrantTransformation.SUMMARY,
            purpose="tool_assisted_review",
            consumption_mode="reusable",
            max_duration_seconds=3_600,
        ),
    )

    def list(self) -> tuple[GrantPreset, ...]:
        return self._PRESETS

    def get(self, preset_id: str) -> GrantPreset | None:
        return next(
            (
                preset
                for preset in self._PRESETS
                if preset.preset_id == preset_id
            ),
            None,
        )

    def matching_id(
        self, *, operation: str, transformation: str, purpose: str
    ) -> str | None:
        return next(
            (
                preset.preset_id
                for preset in self._PRESETS
                if preset.operation.value == operation
                and preset.transformation.value == transformation
                and preset.purpose == purpose
            ),
            None,
        )


__all__ = ["SourceControlGrantPresetCatalog"]

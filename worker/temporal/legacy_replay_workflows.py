"""Historical workflows registered only by replay and compatibility gates."""

from __future__ import annotations

from typing import Any

from temporalio import workflow

from worker.temporal.workflows import AnantaWorkflow


@workflow.defn(name="AnantaWorkflow", sandboxed=True)
class LegacyV0AnantaWorkflow(AnantaWorkflow):
    """N-1 state machine before the v1 compatibility marker existed."""

    _record_compatibility_patch = False

    @workflow.run
    async def run(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        return await super().run(raw_input)


__all__ = ["LegacyV0AnantaWorkflow"]

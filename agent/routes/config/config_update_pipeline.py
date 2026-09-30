"""Ordered normalization pipeline of the POST /config update.

The order of the steps is part of the contract: validation errors are
reported in the same order as before, and later steps (block merges) see the
values normalized by earlier ones. New config sections are added as a new
step instead of growing the route handler (OCP).
"""

from __future__ import annotations

from typing import Any, Callable

from agent.routes.config import config_update_policy_normalizers as policy
from agent.routes.config import config_update_runtime_normalizers as runtime
from agent.routes.config.config_update_contract import (
    ConfigUpdateContext,
    ConfigUpdateRejected,
    ConfigUpdateStep,
)

CONFIG_UPDATE_STEPS: tuple[ConfigUpdateStep, ...] = (
    policy.normalize_feature_flags,
    policy.check_model_routing_editor_release_gate,
    policy.normalize_context_window,
    policy.prepare_operation_policy_revision,
    policy.normalize_profile_and_mode_selectors,
    policy.normalize_execution_fallback_policy,
    policy.normalize_routing_memory_and_federation_policies,
    policy.normalize_exposure_policy,
    policy.normalize_terminal_policy,
    runtime.normalize_cli_session_mode,
    runtime.normalize_opencode_runtime,
    runtime.normalize_aider_cli,
    runtime.normalize_worker_runtime,
    runtime.normalize_planning_policy_and_model_overrides,
    runtime.merge_nested_blocks,
    *runtime.MERGED_BLOCK_STEPS,
    policy.normalize_context_strategy,
    policy.normalize_decision_providers,
    policy.normalize_embedding_provider,
)


def normalize_config_update(
    new_cfg: dict,
    *,
    current_cfg: dict,
    actor: Callable[[], str],
    model_routing_migration_factory: Callable[..., Any],
    steps: tuple[ConfigUpdateStep, ...] = CONFIG_UPDATE_STEPS,
) -> tuple[dict, Any]:
    """Run all steps; return ``(normalized_update, operation_policy_revision_update)``.

    ``model_routing_migration_factory`` builds the legacy model-routing
    migration whose release gate guards enabling the routing editor.
    Raises :class:`ConfigUpdateRejected` for the first invalid section.
    """

    ctx = ConfigUpdateContext(
        current_cfg=current_cfg,
        actor=actor,
        model_routing_migration_factory=model_routing_migration_factory,
    )
    for step in steps:
        new_cfg = step(new_cfg, ctx)
    return new_cfg, ctx.policy_revision_update


__all__ = ["CONFIG_UPDATE_STEPS", "ConfigUpdateRejected", "normalize_config_update"]

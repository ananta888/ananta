"""Backward-compat re-export shim for the old team_blueprint_service.

The 765-line monolith was split in WFG-029 into 5 SRP modules:

  - agent.services.team_template_bootstrap_service
      TemplateBootstrapSpec, RoleLinkSpec, ensure_default_templates,
      _ensure_default_templates_once
  - agent.services.team_blueprint_persistence_service
      PersistBlueprintChildrenResult, BlueprintSaveResult,
      persist_blueprint_children, persist_blueprint_children_in_session,
      save_blueprint, serialize_blueprint_snapshot
  - agent.services.team_blueprint_reconciliation_service
      reconcile_seed_blueprints, _reconcile_seed_blueprints_once,
      reconcile_seed_templates, _reconcile_seed_templates_once
  - agent.services.team_blueprint_instantiation_service
      instantiate_blueprint, _ensure_role_for_blueprint_role_in_session,
      _materialize_blueprint_artifacts_in_session
  - agent.services.team_system_prompt_reconciliation_service
      reconcile_system_prompts, _reconcile_system_prompts_once

This module stays as a 12-month compatibility shim so that existing
imports (`from agent.services.team_blueprint_service import X`) keep
working. The CI detector script `scripts/check_shim_imports.py`
monitors usage; once 0 imports remain, this file is removed in a
cleanup PR.
"""
from __future__ import annotations

import warnings

from agent.services.team_blueprint_instantiation_service import (
    _ensure_role_for_blueprint_role_in_session,
    _materialize_blueprint_artifacts_in_session,
    instantiate_blueprint,
)
from agent.services.team_blueprint_persistence_service import (
    BlueprintSaveResult,
    PersistBlueprintChildrenResult,
    persist_blueprint_children,
    persist_blueprint_children_in_session,
    save_blueprint,
    serialize_blueprint_snapshot,
)
from agent.services.team_blueprint_reconciliation_service import (
    _reconcile_seed_blueprints_once,
    _reconcile_seed_templates_once,
    reconcile_seed_blueprints,
    reconcile_seed_templates,
)
from agent.services.team_system_prompt_reconciliation_service import (
    _reconcile_system_prompts_once,
    reconcile_system_prompts,
)
from agent.services.team_template_bootstrap_service import (
    RoleLinkSpec,
    TemplateBootstrapSpec,
    _ensure_default_templates_once,
    ensure_default_templates,
)

# Backward-compat alias: the original module had a private
# _serialize_blueprint_snapshot; we expose serialize_blueprint_snapshot
# as the public API in the persistence module, but the original private
# name is kept as an alias for any monkeypatchers / tests.
_serialize_blueprint_snapshot = serialize_blueprint_snapshot

__all__ = [
    "BlueprintSaveResult",
    "PersistBlueprintChildrenResult",
    "RoleLinkSpec",
    "TemplateBootstrapSpec",
    "_ensure_default_templates_once",
    "_ensure_role_for_blueprint_role_in_session",
    "_materialize_blueprint_artifacts_in_session",
    "_reconcile_seed_blueprints_once",
    "_reconcile_seed_templates_once",
    "_reconcile_system_prompts_once",
    "_serialize_blueprint_snapshot",
    "ensure_default_templates",
    "instantiate_blueprint",
    "persist_blueprint_children",
    "persist_blueprint_children_in_session",
    "reconcile_seed_blueprints",
    "reconcile_seed_templates",
    "reconcile_system_prompts",
    "save_blueprint",
    "serialize_blueprint_snapshot",
]


warnings.warn(
    "agent.services.team_blueprint_service is deprecated; "
    "import from agent.services.team_{template_bootstrap,"
    "blueprint_persistence,blueprint_reconciliation,"
    "blueprint_instantiation,system_prompt_reconciliation}_service "
    "directly. This shim will be removed in 12 months.",
    DeprecationWarning,
    stacklevel=2,
)

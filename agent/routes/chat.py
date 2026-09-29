"""Chat API blueprint (``/api/chat``) - public entry point.

The former 1982-line module is split into single-responsibility siblings:

* :mod:`agent.routes.chat_blueprint` - the shared ``chat_bp`` blueprint object.
* :mod:`agent.routes.chat_route_dependencies` - the collaborators the chat
  handlers delegate to, and their per-application override seam.
* :mod:`agent.routes.chat_route_access` - request principal, ownership and
  authorization guards (decorators) for chat records.
* :mod:`agent.routes.chat_route_settings` - setting validation, redaction and
  v3 settings migration.
* :mod:`agent.routes.chat_route_persistence` - loading/saving sessions, folders,
  profiles and chat types from the user config.
* :mod:`agent.routes.chat_profile_routes` - profile, setting-schema and chat-type
  endpoints.
* :mod:`agent.routes.chat_session_routes` - session CRUD/activation endpoints.
* :mod:`agent.routes.chat_session_process_routes` - session workflow process and
  run/gate endpoints.
* :mod:`agent.routes.chat_organization_routes` - folder and organization-proposal
  endpoints.
* :mod:`agent.routes.chat_session_ai_routes` - AI reorganize, context overview,
  partial summary and prompt-preview endpoints.

Importing this module registers every route on ``chat_bp``. All names that
used to live here stay importable from this module.

The handlers' collaborators (``get_manager``, ``resolve_effective_process``,
``start_session_process``, ``runtime_overlay``, ``signal_session_gate``) are
bundled in :class:`agent.routes.chat_route_dependencies.ChatRouteDependencies`
and resolved per application through ``CHAT_ROUTE_DEPENDENCIES``; tests replace
them with ``CHAT_ROUTE_DEPENDENCIES.override(app, ...)`` instead of patching
names on this module. The re-exported service functions below are kept only
for import compatibility.
"""

from __future__ import annotations

from agent.routes.chat_blueprint import (
    chat_bp,
)
from agent.routes.chat_organization_routes import (
    apply_organization_proposal,
    create_folder,
    delete_folder,
    get_organization_snapshot,
    list_folders,
    organization_history,
    organization_proposal,
    organization_proposals,
    organization_revision,
    revert_organization_revision,
    update_folder,
    validate_organization_proposal,
)
from agent.routes.chat_profile_routes import (
    create_chat_profile,
    create_chat_type,
    delete_chat_profile,
    discover_chat_profile_models,
    get_chat_setting_schema,
    get_effective_chat_profile,
    list_chat_profiles,
    list_chat_types,
    mutate_chat_type,
    preview_effective_chat_profile,
    test_chat_profile_connection,
    update_chat_profile,
)
from agent.routes.chat_route_access import (
    _chat_workflow_principal,
    _chat_workflow_run_is_owned_by,
    _legacy_chat_owner,
    _log,
    _organization_error,
    _organization_service,
    _owned_session,
    _owned_sessions,
    _public_process_payload,
    _require_global_chat_admin,
    _serialized_chat_mutation,
)
from agent.routes.chat_route_persistence import (
    _apply_profile,
    _load_chat,
    _load_chat_types,
    _load_folders,
    _load_profiles,
    _profile_by_id,
    _save_chat,
    _save_custom_profiles,
    _save_folders,
)
from agent.routes.chat_route_settings import (
    _chat_setting_contract,
    _migrate_profile_settings_v3,
    _migrate_session_settings_v3,
    _provider_setting_issues,
    _redact_settings,
    _validated_process_ref,
    _validated_profile_settings,
)
from agent.routes.chat_session_ai_routes import (
    _heuristic_reorganize,
    _llm_reorganize,
    _strip_json_fences,
    ai_reorganize_sessions,
    get_session_context_overview,
    preview_session_prompt,
    summarize_session_messages,
)
from agent.routes.chat_session_process_routes import (
    clone_effective_session_process,
    get_effective_session_process,
    get_session_process_run,
    list_session_process_runs,
    signal_session_process_run_gate,
    start_session_process_run,
)
from agent.routes.chat_session_routes import (
    activate_chat_session,
    create_chat_session,
    delete_chat_session,
    get_single_chat_session,
    list_chat_sessions,
    update_chat_session,
)
from agent.services.chat_process_binding import (
    resolve_effective_process,
    runtime_overlay,
    signal_session_gate,
    start_session_process,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager

__all__ = [
    "_apply_profile",
    "_chat_setting_contract",
    "_chat_workflow_principal",
    "_chat_workflow_run_is_owned_by",
    "_heuristic_reorganize",
    "_legacy_chat_owner",
    "_llm_reorganize",
    "_load_chat",
    "_load_chat_types",
    "_load_folders",
    "_load_profiles",
    "_log",
    "_migrate_profile_settings_v3",
    "_migrate_session_settings_v3",
    "_organization_error",
    "_organization_service",
    "_owned_session",
    "_owned_sessions",
    "_profile_by_id",
    "_provider_setting_issues",
    "_public_process_payload",
    "_redact_settings",
    "_require_global_chat_admin",
    "_save_chat",
    "_save_custom_profiles",
    "_save_folders",
    "_serialized_chat_mutation",
    "_strip_json_fences",
    "_validated_process_ref",
    "_validated_profile_settings",
    "activate_chat_session",
    "ai_reorganize_sessions",
    "apply_organization_proposal",
    "chat_bp",
    "clone_effective_session_process",
    "create_chat_profile",
    "create_chat_session",
    "create_chat_type",
    "create_folder",
    "delete_chat_profile",
    "delete_chat_session",
    "delete_folder",
    "discover_chat_profile_models",
    "get_chat_setting_schema",
    "get_effective_chat_profile",
    "get_effective_session_process",
    "get_manager",
    "get_organization_snapshot",
    "get_session_context_overview",
    "get_session_process_run",
    "get_single_chat_session",
    "list_chat_profiles",
    "list_chat_sessions",
    "list_chat_types",
    "list_folders",
    "list_session_process_runs",
    "mutate_chat_type",
    "organization_history",
    "organization_proposal",
    "organization_proposals",
    "organization_revision",
    "preview_effective_chat_profile",
    "preview_session_prompt",
    "resolve_effective_process",
    "revert_organization_revision",
    "runtime_overlay",
    "signal_session_gate",
    "signal_session_process_run_gate",
    "start_session_process",
    "start_session_process_run",
    "summarize_session_messages",
    "test_chat_profile_connection",
    "update_chat_profile",
    "update_chat_session",
    "update_folder",
    "validate_organization_proposal",
]

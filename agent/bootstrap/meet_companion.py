"""Opt-in Task-bound grant foundation; runtime migration remains separately gated."""

import os

from agent.services.meet_companion_task_grants import MeetCompanionTaskGrants


def configure_companion_task_grants(app, authority, issuer):
    flag = app.config.get("ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED",
                          os.environ.get("ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED", "0"))
    if str(flag).lower() not in {"1", "true"}:
        return
    if app.config.get("ROLE") != "hub" or authority is None or issuer is None:
        raise ValueError("meet_companion_task_grants_hub_required")
    app.extensions["meet_companion_task_grants"] = MeetCompanionTaskGrants(authority, issuer)
    configure_companion_task_tools(app, authority)


def configure_companion_task_tools(app, authority):
    flag = app.config.get("ANANTA_MEET_COMPANION_TASK_TOOLS_ENABLED",
                          os.environ.get("ANANTA_MEET_COMPANION_TASK_TOOLS_ENABLED", "0"))
    if str(flag).lower() not in {"1", "true"}:
        return
    if app.config.get("ROLE") != "hub" or "meet_companion_task_grants" not in app.extensions:
        raise ValueError("meet_companion_task_tools_hub_required")
    from agent.database import engine
    from agent.repositories.meet_companion_tool_sources import SqlCompanionToolAttempts, SqlCompanionToolSources
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.mcp_registry_service import get_mcp_registry_service
    from agent.services.meet_companion_task_tools import MeetCompanionTaskTools
    from agent.services.meet_companion_tool_execution import HubCompanionToolExecution, HubCompanionToolPolicy
    from agent.services.operation_policy_observability_service import get_operation_policy_observability_service
    from agent.services.operation_policy_service import get_operation_policy_service
    from agent.services.operation_registry_service import get_operation_registry_service

    sources = SqlCompanionToolSources(engine)
    sources.initialize()
    attempts = SqlCompanionToolAttempts(engine)
    attempts.initialize()
    app.extensions["meet_companion_task_tools"] = MeetCompanionTaskTools(
        authority, HubCompanionToolPolicy(app, get_operation_registry_service(), get_operation_policy_service(),
                                         observer=get_operation_policy_observability_service()),
        HubCompanionToolExecution(app, get_mcp_registry_service()), sources, get_hub_evidence_registry_service(),
        attempts=attempts,
    )
    configure_companion_model_runs(app, authority, app.extensions["meet_companion_task_tools"], engine)


def configure_companion_model_runs(app, authority, tools, database):
    flag = app.config.get("ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED",
                          os.environ.get("ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED", "0"))
    if str(flag).lower() not in {"1", "true"}:
        return
    if app.config.get("ROLE") != "hub" or app.extensions.get("meet_companion_task_tools") is not tools:
        raise ValueError("meet_companion_model_runs_hub_required")
    from agent.bootstrap.meet_companion_model_profile import configure_model_profile

    configure_model_profile(app)
    from agent.repositories.meet_companion_tool_sources import SqlCompanionToolSources
    from agent.services.meet_companion_model_inputs import MeetCompanionModelInputs
    from agent.services.meet_companion_model_policy import HubCompanionModelPolicy
    from agent.services.meet_companion_model_runs import MeetCompanionModelRuns

    sources = SqlCompanionToolSources(database, maximum=128_000)
    sources.initialize()
    app.extensions["meet_companion_model_runs"] = MeetCompanionModelRuns(
        authority, HubCompanionModelPolicy(app), MeetCompanionModelInputs(tools.registry, tools.sources, tools.policy),
        sources, tools.registry, attempts=tools.attempts,
    )
    grants = app.extensions.get("meet_companion_task_grants")
    if grants is not None:
        grants.model_policy = app.extensions["meet_companion_model_runs"].policy
    from agent.repositories.meet_companion_run_cleanup import SqlCompanionRunCleanup
    from agent.services.meet_companion_run_cleanup import MeetCompanionRunCleanup
    from agent.services.meet_companion_run_reconciliation import MeetCompanionRunReconciliation

    cleanup_repository = SqlCompanionRunCleanup(database)
    authority.tasks.run_cleanup = MeetCompanionRunCleanup(cleanup_repository, tools.registry)
    app.extensions["meet_companion_run_reconciliation"] = MeetCompanionRunReconciliation(
        cleanup_repository, authority.tasks.run_cleanup)

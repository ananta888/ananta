"""Compose Meet integration only on explicitly enabled Hub instances."""

import os

from agent.services.meet_contract import MeetError, MeetProfile


def _task_access(principal, project, task_id):
    from agent.routes.tasks.task_read_access import task_read_access_context
    from agent.services.repository_registry import get_repository_registry

    task = get_repository_registry().task_repo.get_by_id(task_id)
    if (
        task is None
        or getattr(task, "project_id", None) != project
        or getattr(task, "tenant_id", None) != principal.tenant_id
        or getattr(task, "archived", False)
    ):
        raise MeetError("meet_task_not_found", 404)
    task_read_access_context().require(task.model_dump())


def configure_meet(app):
    from agent.routes.meet import meet_bp

    app.register_blueprint(meet_bp)
    enabled = app.config.get("ANANTA_MEET_ENABLED", os.environ.get("ANANTA_MEET_ENABLED", "0"))
    if str(enabled).lower() not in {"1", "true"} or app.config.get("ROLE") != "hub":
        return
    from agent.database import engine
    from agent.repositories.meet_bindings import SqlMeetingStore
    from agent.services.meet_binding_service import MeetBindingService
    from agent.services.meet_health_probe import MeetHealthProbe

    profile = MeetProfile(
        app.config.get("ANANTA_MEET_ORIGIN") or os.environ.get("ANANTA_MEET_ORIGIN", "https://webrtc.ananta.de")
    )
    store = SqlMeetingStore(engine, profile.origin)
    store.initialize()
    app.extensions["meet_binding_service"] = MeetBindingService(
        profile, store, app.extensions["project_access_authority"], _task_access
    )
    app.extensions["meet_health_probe"] = MeetHealthProbe(profile)

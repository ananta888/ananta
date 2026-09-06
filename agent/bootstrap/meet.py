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
    configure_meet_media(app)


def configure_meet_media(app):
    """Explicit tenant/project preauthorization; no inferred approval policy."""
    import json

    if os.environ.get("ANANTA_MEET_MEDIA_ENABLED") != "1":
        return
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
    from ananta_contracts.meet_speech import speech_profile
    from worker.meet_media.contract import load_key

    scopes = json.loads(os.environ.get("ANANTA_MEET_MEDIA_ALLOWED_SCOPES", "[]"))
    if not isinstance(scopes, list) or any(
        not isinstance(item, list) or len(item) != 2 or any(not isinstance(v, str) or not v for v in item)
        for item in scopes
    ):
        raise ValueError("meet_media_scope_policy_invalid")
    key = load_key(os.environ["ANANTA_MEET_MEDIA_KEY_FILE"])
    app.extensions["meet_media_worker_key"] = key
    worker = HttpMediaWorker(os.environ["ANANTA_MEET_MEDIA_WORKER_URL"], key)
    issuer = None
    if os.environ.get("ANANTA_MEET_MACHINE_ENABLED") == "1":
        from agent.services.meet_machine_grant import MeetMachineGrantIssuer

        issuer = MeetMachineGrantIssuer(
            os.environ["ANANTA_MEET_MACHINE_ISSUER"], os.environ["ANANTA_MEET_MACHINE_KEY_FILE"]
        )
    images = _persona_images(app)
    profiles = None
    if images is not None and app.extensions.get("persona_profiles") is not None:
        from agent.services.meet_persona_profiles import MeetPersonaProfiles

        profiles = MeetPersonaProfiles(app.extensions["persona_profiles"], images)
    app.extensions["meet_turn_service"] = MeetTurnService(
        app.extensions["meet_binding_service"],
        worker,
        HubMediaTasks(),
        map(tuple, scopes),
        grant_issuer=issuer,
        persona_images=images,
        persona_profiles=profiles,
        speech_profile=speech_profile(max_seconds=int(os.environ.get("ANANTA_MEET_SPEECH_MAX_SECONDS", "40"))),
    )


def _persona_images(app):
    from agent.services.meet_persona_images import MeetPersonaImages

    assets = app.extensions.get("persona_assets")
    return MeetPersonaImages(assets) if assets is not None else None

"""Compose Meet integration only on explicitly enabled Hub instances."""

import os

from agent.services.meet_contract import MeetError, MeetProfile


def _task_access(principal, project, task_id):
    from flask import current_app
    from agent.services.organization_membership_service import OrganizationMembershipService
    from agent.services.task_read_access_service import TaskReadAccessContext, get_task_read_access_service
    from agent.services.repository_registry import get_repository_registry

    task = get_repository_registry().task_repo.get_by_id(task_id)
    if (
        task is None
        or getattr(task, "project_id", None) != project
        or getattr(task, "tenant_id", None) != principal.tenant_id
        or getattr(task, "archived", False)
    ):
        raise MeetError("meet_task_not_found", 404)
    # Internal HMAC callbacks have no human HTTP bearer. Revalidate the owner
    # carried by the current Hub task, never an unrelated request principal.
    TaskReadAccessContext(principal=principal,
        project_access=current_app.extensions.get("project_access_authority"),
        organization_membership=current_app.extensions.get("organization_membership_service") or OrganizationMembershipService(),
        service=current_app.extensions.get("task_read_access_service") or get_task_read_access_service()).require(task.model_dump())


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
    from agent.database import engine
    from agent.repositories.meet_capacity import SqlMeetCapacity
    from agent.services.meet_capacity_admission import MeetCapacityAdmission

    tasks = HubMediaTasks()
    slots = SqlMeetCapacity(engine, os.environ.get("ANANTA_MEET_MEDIA_CAPACITY_POOL", "local-meet-media"))
    slots.initialize()
    capacity = MeetCapacityAdmission(slots, tasks)
    app.extensions["meet_media_capacity"] = capacity
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
        tasks,
        map(tuple, scopes),
        grant_issuer=issuer,
        persona_images=images,
        persona_profiles=profiles,
        speech_profile=speech_profile(max_seconds=int(os.environ.get("ANANTA_MEET_SPEECH_MAX_SECONDS", "40"))),
        capacity=capacity,
    )
    configure_meet_dialog(app, worker, issuer)


def configure_meet_dialog(app, worker, issuer):
    """Separate opt-in: old publish-only scope grants never grant receive rights."""
    import json
    if os.environ.get("ANANTA_MEET_DIALOG_ENABLED") != "1":
        return
    if issuer is None or app.config.get("ROLE") != "hub":
        raise ValueError("meet_dialog_machine_hub_required")
    from agent.database import engine
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_authorization_client import MeetAuthorizationClient

    rows = json.loads(os.environ.get("ANANTA_MEET_DIALOG_POLICIES", "[]"))
    policies = {}
    if not isinstance(rows, list):
        raise ValueError("meet_dialog_policy_invalid")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"tenant_id", "project_id", "capabilities"}
                or not all(isinstance(row[k], str) and row[k] for k in ("tenant_id", "project_id"))):
            raise ValueError("meet_dialog_policy_invalid")
        scope = row["tenant_id"], row["project_id"]
        if scope in policies:
            raise ValueError("meet_dialog_policy_duplicate")
        policies[scope] = row["capabilities"]
    tasks = HubDialogTasks()
    authority = MeetDialogAuthority(tasks, app.extensions["meet_binding_service"], policies)
    reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize(); dispatches.initialize()
    app.extensions["meet_dialog_service"] = MeetDialogService(authority, tasks,
        MeetAuthorizationClient(authority, issuer), issuer, worker, worker, reservations, dispatches)


def _persona_images(app):
    from agent.services.meet_persona_images import MeetPersonaImages

    assets = app.extensions.get("persona_assets")
    return MeetPersonaImages(assets) if assets is not None else None

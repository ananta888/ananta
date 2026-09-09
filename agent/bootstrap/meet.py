"""Compose Meet integration only on explicitly enabled Hub instances."""

import os

from agent.services.meet_contract import MeetError, MeetProfile


def _task_access(principal, project, task_id):
    from flask import current_app

    from agent.services.organization_membership_service import OrganizationMembershipService
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_read_access_service import TaskReadAccessContext, get_task_read_access_service

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
    TaskReadAccessContext(
        principal=principal,
        project_access=current_app.extensions.get("project_access_authority"),
        organization_membership=current_app.extensions.get("organization_membership_service")
        or OrganizationMembershipService(),
        service=current_app.extensions.get("task_read_access_service") or get_task_read_access_service(),
    ).require(task.model_dump())


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
    from agent.bootstrap.meet_room_allocation import configure_meet_room_allocation

    configure_meet_room_allocation(app)
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
            os.environ["ANANTA_MEET_MACHINE_ISSUER"],
            os.environ["ANANTA_MEET_MACHINE_KEY_FILE"],
            key_id=os.environ.get("ANANTA_MEET_MACHINE_KEY_ID") or None,
        )
    images = _persona_images(app)
    from agent.services.meet_persona_videos import MeetPersonaVideos

    video_assets = app.extensions.get("persona_video_assets")
    videos = MeetPersonaVideos(video_assets) if video_assets is not None else None
    profiles = None
    avatar_profiles = None
    video_profiles = None
    if images is not None and app.extensions.get("persona_profiles") is not None:
        from agent.services.meet_persona_profiles import MeetPersonaProfiles

        profiles = MeetPersonaProfiles(app.extensions["persona_profiles"], images)
        from agent.services.meet_avatar_profiles import MeetAvatarProfiles

        avatar_profiles = MeetAvatarProfiles(app.extensions["persona_profiles"], images)
    if videos is not None and app.extensions.get("persona_profiles") is not None:
        from agent.services.meet_persona_video_profiles import MeetPersonaVideoProfiles

        video_profiles = MeetPersonaVideoProfiles(app.extensions["persona_profiles"], videos)
    voice = speech_profile(max_seconds=int(os.environ.get("ANANTA_MEET_SPEECH_MAX_SECONDS", "40")))
    app.extensions["meet_turn_service"] = MeetTurnService(
        app.extensions["meet_binding_service"],
        worker,
        tasks,
        map(tuple, scopes),
        grant_issuer=issuer,
        persona_images=images,
        persona_profiles=profiles,
        speech_profile=voice,
        capacity=capacity,
        persona_videos=videos,
        persona_video_profiles=video_profiles,
    )
    configure_meet_dialog(app, worker, issuer, capacity=capacity, speech_profile=voice, avatar_profiles=avatar_profiles)


def configure_meet_dialog(app, worker, issuer, *, capacity=None, speech_profile=None, avatar_profiles=None):
    """Separate opt-in: old publish-only scope grants never grant receive rights."""
    import json

    if os.environ.get("ANANTA_MEET_DIALOG_ENABLED") != "1":
        return
    if issuer is None or app.config.get("ROLE") != "hub":
        raise ValueError("meet_dialog_machine_hub_required")
    if capacity is None or speech_profile is None:
        raise ValueError("meet_dialog_media_budgets_required")
    from agent.database import engine
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_replies import MeetDialogReplies
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_turn_service import HubMediaTasks

    rows = json.loads(os.environ.get("ANANTA_MEET_DIALOG_POLICIES", "[]"))
    policies = {}
    if not isinstance(rows, list):
        raise ValueError("meet_dialog_policy_invalid")
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"tenant_id", "project_id", "capabilities"}
            or not all(isinstance(row[k], str) and row[k] for k in ("tenant_id", "project_id"))
        ):
            raise ValueError("meet_dialog_policy_invalid")
        scope = row["tenant_id"], row["project_id"]
        if scope in policies:
            raise ValueError("meet_dialog_policy_duplicate")
        policies[scope] = row["capabilities"]
    from agent.bootstrap.meet_dialog_publishers import configured_dialog_workers
    from agent.services.meet_role_assignment import get_meet_role_assignments

    assignments = get_meet_role_assignments()
    publishers, dialog_workers = configured_dialog_workers(
        os.environ.get("ANANTA_MEET_DIALOG_WORKER_URLS"), worker, assignments.rows
    )
    tasks = HubDialogTasks(
        publisher_url=worker.publisher_url,
        organization_principals=os.environ.get("ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED") == "1",
        role_assignments=assignments,
        publishers=publishers,
    )
    from agent.bootstrap.meet_preauthorizations import configure_meet_preauthorizations

    authority = MeetDialogAuthority(
        tasks,
        app.extensions["meet_binding_service"],
        policies,
        preauthorization=configure_meet_preauthorizations(app, engine),
    )
    from agent.services.meet_dialog_principal_receipts import MeetDialogPrincipalReceipts

    app.extensions["meet_dialog_principals"] = MeetDialogPrincipalReceipts(authority, tasks, issuer.issuer)
    if tasks.organization_principals:
        from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle
        from agent.services.meet_organization_principal_preflight import MeetOrganizationPrincipalPreflight

        lifecycle = MeetDialogLifecycle(tasks, role_assignments=assignments)
        app.extensions["meet_organization_principal_preflight"] = MeetOrganizationPrincipalPreflight(
            authority.binding,
            lifecycle,
            assignments,
            publisher_url=tasks.publisher_url,
            issuer=issuer.issuer,
            publishers=publishers,
        )
    from agent.repositories.meet_dialog_phases import TaskDialogPhases
    from agent.services.meet_dialog_phases import MeetDialogPhases
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    meet = MeetAuthorizationClient(authority, issuer)
    phases = MeetDialogPhases(
        TaskDialogPhases(tasks, task_status_cas=compare_and_set_local_task_status), authority, meet
    )
    app.extensions["meet_dialog_phases"] = phases
    from agent.bootstrap.meet_dialog_diagnostics import configure_dialog_diagnostics

    configure_dialog_diagnostics(app, engine, authority.binding)
    reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize()
    dispatches.initialize()
    media_tasks = HubMediaTasks()
    replies = MeetDialogReplies(
        app.extensions["meet_binding_service"],
        worker,
        media_tasks,
        dispatches,
        capacity=capacity,
        speech_profile=speech_profile,
    )
    voice_profiles = None
    voice_assets = app.extensions.get("persona_voice_assets")
    profiles = app.extensions.get("persona_profiles")
    if voice_assets is not None and profiles is not None:
        from agent.services.meet_persona_voice_profiles import MeetPersonaVoiceProfiles
        from agent.services.meet_persona_voices import MeetPersonaVoices

        voice_profiles = MeetPersonaVoiceProfiles(profiles, MeetPersonaVoices(voice_assets))
    dialog_worker = worker
    avatar_video_profiles = None
    video_assets = app.extensions.get("persona_video_assets")
    if video_assets is not None and app.extensions.get("persona_profiles") is not None:
        from agent.services.meet_avatar_video_profiles import MeetAvatarVideoProfiles
        from agent.services.meet_persona_videos import MeetPersonaVideos

        avatar_video_profiles = MeetAvatarVideoProfiles(
            app.extensions["persona_profiles"], MeetPersonaVideos(video_assets)
        )
    from agent.bootstrap.meet_dialog_capacity import configured_dialog_capacity
    from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter

    dialog_worker = MeetDialogWorkerRouter(
        authority,
        tasks,
        dialog_workers or {worker.publisher_url: worker},
        worker.publisher_url,
        capacity=configured_dialog_capacity(app, engine, authority),
    )
    from agent.bootstrap.meet_browser import configured_browser_workspaces
    from agent.bootstrap.meet_media_timing import configured_media_timing
    from agent.bootstrap.meet_recovery import configured_dialog_recovery
    from agent.bootstrap.meet_speaker import configured_speaker_floor

    speaker_floor = configured_speaker_floor(engine)
    recovery = configured_dialog_recovery(engine, authority, meet, issuer, phases, speaker_floor=speaker_floor)
    meet.recovery = recovery
    app.extensions["meet_dialog_service"] = MeetDialogService(
        authority,
        tasks,
        meet,
        issuer,
        dialog_worker,
        worker,
        reservations,
        dispatches,
        media_tasks=media_tasks,
        replies=replies,
        avatar_profiles=avatar_profiles,
        voice_profiles=voice_profiles,
        phases=phases,
        avatar_video_profiles=avatar_video_profiles,
        browser_workspaces=configured_browser_workspaces(authority, tasks),
        speaker_floor=speaker_floor,
        recovery=recovery,
        media_timing=configured_media_timing(),
    )
    from agent.repositories.meet_dialog_starts import SqlDialogStarts
    from agent.services.meet_dialog_starts import MeetDialogStarts

    starts = SqlDialogStarts(engine)
    starts.initialize()
    app.extensions["meet_dialog_starts"] = MeetDialogStarts(
        app.extensions["meet_dialog_service"], starts, app.extensions["meet_binding_service"]
    )
    from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
    from agent.services.meet_dialog_deadlines import MeetDialogDeadlines
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    app.extensions["meet_dialog_deadlines"] = MeetDialogDeadlines(
        SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)
    )


def _persona_images(app):
    from agent.services.meet_persona_images import MeetPersonaImages

    assets = app.extensions.get("persona_assets")
    return MeetPersonaImages(assets) if assets is not None else None

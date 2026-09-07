"""Opt-in video-only composition; transport configuration grants no project use."""

import os


def configure_persona_videos(app):
    if app.config.get("ROLE") != "hub" or os.environ.get("ANANTA_PERSONA_VIDEOS_ENABLED") != "1":
        return
    from agent.database import engine
    from agent.repositories.persona_video_assets import create_video_asset_catalog
    from agent.repositories.persona_video_cursors import SqlPersonaVideoCursors
    from agent.repositories.persona_video_policies import create_video_policy_repository
    from agent.repositories.persona_video_retention import create_video_retention_store
    from agent.services.artifact_store import ArtifactStore
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.persona_asset_query import PersonaAssetQuery
    from agent.services.persona_inspection_formats import PersonaVideoInspectionFormat
    from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
    from agent.services.persona_inspection_task_state import HubPersonaTaskState
    from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
    from agent.services.persona_profile_videos import PersonaProfileVideos
    from agent.services.persona_retention_runner import PersonaRetentionRunner
    from agent.services.persona_retention_service import PersonaRetentionService
    from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
    from agent.services.persona_video_asset_service import PersonaVideoAssetService
    from agent.services.persona_video_erasure import create_video_erasure_service
    from agent.services.persona_video_policy_service import create_video_policy_service
    from agent.services.persona_video_storage import PersonaVideoStorage
    from agent.services.persona_video_transport import HttpPersonaVideoWorker
    from worker.meet_media.contract import load_key

    key = load_key(os.environ["ANANTA_PERSONA_VIDEO_KEY_FILE"])
    worker = HttpPersonaVideoWorker(os.environ["ANANTA_PERSONA_VIDEO_WORKER_URL"], key)
    registry, state, format = (
        get_hub_evidence_registry_service(),
        HubPersonaTaskState(kind="video"),
        PersonaVideoInspectionFormat(),
    )
    policies, catalog = create_video_policy_repository(engine), create_video_asset_catalog(engine)
    receipts = HubPersonaInspectionReceipts(state=state, registry=registry, format=format)
    policy = create_video_policy_service(
        access=app.extensions["project_access_authority"],
        policies=policies,
        sources=registry,
        inspection_receipts=receipts,
    )
    tasks = HubPersonaInspectionTasks(
        policy=policy,
        worker=worker,
        state=state,
        registry=registry,
        format=format,
        repository_revision=os.environ["ANANTA_PERSONA_VIDEO_REPOSITORY_REVISION"],
        execution_profile_digest=os.environ["ANANTA_PERSONA_VIDEO_EXECUTION_PROFILE_DIGEST"],
        environment_digest=os.environ["ANANTA_PERSONA_VIDEO_ENVIRONMENT_DIGEST"],
    )
    policies.initialize()
    catalog.initialize()
    store = ArtifactStore()
    erasure = create_video_erasure_service(policy=policy, catalog=catalog, base_dir=store.base_dir)
    retention = create_video_retention_store(engine)
    retention.initialize()
    app.extensions.update(
        persona_video_policy=policy,
        persona_video_worker_key=key,
        persona_video_leases=HubPersonaInspectionLeases(state=state, policy=policy, registry=registry, kind="video"),
        persona_video_assets=PersonaVideoAssetService(
            policy=policy, tasks=tasks, catalog=catalog, storage=PersonaVideoStorage(store)
        ),
        persona_video_erasure=erasure,
        persona_video_retention=PersonaRetentionService(policy=policy, catalog=catalog, store=retention),
        persona_video_retention_runner=PersonaRetentionRunner(
            policy=policy,
            catalog=catalog,
            store=retention,
            erasure=erasure,
            tasks=HubPersonaRetentionTasks(kind="video"),
        ),
    )
    app.extensions["persona_profile_videos"] = PersonaProfileVideos(app.extensions["persona_video_assets"])
    cursors = SqlPersonaVideoCursors(engine)
    cursors.initialize()
    app.extensions["persona_video_query"] = PersonaAssetQuery(
        policy=policy,
        catalog=catalog,
        references=app.extensions["persona_profile_videos"],
        cursors=cursors,
        kind="video",
    )

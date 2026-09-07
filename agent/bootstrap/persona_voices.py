"""Opt-in voice composition; transport and local model availability grant no project use."""

import os


def configure_persona_voices(app):
    if app.config.get("ROLE") != "hub" or os.environ.get("ANANTA_PERSONA_VOICES_ENABLED") != "1":
        return
    from agent.database import engine
    from agent.repositories.persona_voice_assets import create_voice_asset_catalog
    from agent.repositories.persona_voice_cursors import create_voice_cursors
    from agent.repositories.persona_voice_policies import create_voice_policy_repository
    from agent.repositories.persona_voice_retention import create_voice_retention_store
    from agent.services.artifact_store import ArtifactStore
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.persona_asset_query import PersonaAssetQuery
    from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
    from agent.services.persona_inspection_task_state import HubPersonaTaskState
    from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
    from agent.services.persona_profile_voices import PersonaProfileVoices
    from agent.services.persona_retention_runner import PersonaRetentionRunner
    from agent.services.persona_retention_service import PersonaRetentionService
    from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
    from agent.services.persona_voice_asset_service import create_voice_asset_service
    from agent.services.persona_voice_erasure import create_voice_erasure_service
    from agent.services.persona_voice_inspection import PersonaVoiceInspectionFormat
    from agent.services.persona_voice_policy_service import create_voice_policy_service
    from agent.services.persona_voice_storage import PersonaVoiceStorage
    from agent.services.persona_voice_transport import create_voice_worker_transport
    from worker.meet_media.contract import load_key

    key = load_key(os.environ["ANANTA_PERSONA_VOICE_KEY_FILE"])
    worker = create_voice_worker_transport(os.environ["ANANTA_PERSONA_VOICE_WORKER_URL"], key)
    registry = get_hub_evidence_registry_service()
    state, format = HubPersonaTaskState(kind="voice"), PersonaVoiceInspectionFormat()
    policies, catalog = create_voice_policy_repository(engine), create_voice_asset_catalog(engine)
    receipts = HubPersonaInspectionReceipts(state=state, registry=registry, format=format)
    policy = create_voice_policy_service(
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
        repository_revision=os.environ["ANANTA_PERSONA_VOICE_REPOSITORY_REVISION"],
        execution_profile_digest=os.environ["ANANTA_PERSONA_VOICE_EXECUTION_PROFILE_DIGEST"],
        environment_digest=os.environ["ANANTA_PERSONA_VOICE_ENVIRONMENT_DIGEST"],
    )
    # Validate all operator execution pins before any schema/extension activation.
    store = ArtifactStore()
    erasure = create_voice_erasure_service(policy=policy, catalog=catalog, base_dir=store.base_dir)
    retention, cursors = create_voice_retention_store(engine), create_voice_cursors(engine)
    assets = create_voice_asset_service(policy=policy, tasks=tasks, catalog=catalog, storage=PersonaVoiceStorage(store))
    references = PersonaProfileVoices(assets)
    for repository in (policies, catalog, retention, cursors):
        repository.initialize()
    app.extensions.update(
        persona_voice_policy=policy,
        persona_voice_worker_key=key,
        persona_voice_leases=HubPersonaInspectionLeases(state=state, policy=policy, registry=registry, kind="voice"),
        persona_voice_assets=assets,
        persona_voice_erasure=erasure,
        persona_voice_retention=PersonaRetentionService(policy=policy, catalog=catalog, store=retention),
        persona_voice_retention_runner=PersonaRetentionRunner(
            policy=policy,
            catalog=catalog,
            store=retention,
            erasure=erasure,
            tasks=HubPersonaRetentionTasks(kind="voice"),
        ),
        persona_profile_voices=references,
        persona_voice_query=PersonaAssetQuery(
            policy=policy, catalog=catalog, references=references, cursors=cursors, kind="voice"
        ),
    )

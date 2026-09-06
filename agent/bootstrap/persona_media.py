"""Explicit image-only Hub composition; configuration never grants project use."""

import os


def configure_persona_media(app):
    from agent.routes.persona_media import persona_media_bp

    app.register_blueprint(persona_media_bp)
    if app.config.get("ROLE") != "hub" or os.environ.get("ANANTA_PERSONA_IMAGES_ENABLED") != "1":
        return
    from agent.database import engine
    from agent.repositories.persona_asset_policy import SqlPersonaImagePolicies
    from agent.repositories.persona_assets import SqlPersonaAssets
    from agent.services.artifact_store import ArtifactStore
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
    from agent.services.persona_asset_service import PersonaAssetService
    from agent.services.persona_asset_storage import PersonaAssetStorage
    from agent.services.persona_image_transport import HttpPersonaImageWorker
    from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
    from agent.services.persona_inspection_task_state import HubPersonaTaskState
    from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
    from worker.meet_media.contract import load_key

    key = load_key(os.environ["ANANTA_PERSONA_IMAGE_KEY_FILE"])
    worker = HttpPersonaImageWorker(os.environ["ANANTA_PERSONA_IMAGE_WORKER_URL"], key)
    registry, state = get_hub_evidence_registry_service(), HubPersonaTaskState()
    policies, catalog = SqlPersonaImagePolicies(engine), SqlPersonaAssets(engine)
    policies.initialize()
    catalog.initialize()
    receipts = HubPersonaInspectionReceipts(state=state, registry=registry)
    policy = PersonaAssetPolicyService(
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
        repository_revision=os.environ["ANANTA_PERSONA_IMAGE_REPOSITORY_REVISION"],
        execution_profile_digest=os.environ["ANANTA_PERSONA_IMAGE_EXECUTION_PROFILE_DIGEST"],
        environment_digest=os.environ["ANANTA_PERSONA_IMAGE_ENVIRONMENT_DIGEST"],
    )
    app.extensions.update(
        persona_image_policy=policy,
        persona_image_worker_key=key,
        persona_image_leases=HubPersonaInspectionLeases(state=state, policy=policy, registry=registry),
        persona_assets=PersonaAssetService(
            policy=policy, tasks=tasks, catalog=catalog, storage=PersonaAssetStorage(ArtifactStore())
        ),
    )

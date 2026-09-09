"""Optional Hub composition: explicit generator and separately enabled asset kinds."""

import os


def configure_persona_generation(app):
    if app.config.get("ROLE") != "hub" or os.environ.get("ANANTA_PERSONA_GENERATION_ENABLED") != "1":
        return
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.persona_generated_assets import PersonaGeneratedAssets
    from agent.services.persona_generated_source import PersonaGeneratedSourceAdmission
    from agent.services.persona_generation_leases import HubPersonaGenerationLeases
    from agent.services.persona_generation_policy import PersonaGenerationPolicy
    from agent.services.persona_generation_service import HubPersonaGenerationService
    from agent.services.persona_generation_state import HubPersonaGenerationState
    from agent.services.persona_generation_transport import HttpPersonaGenerationWorker
    from worker.meet_media.contract import load_key

    key = load_key(os.environ["ANANTA_PERSONA_GENERATION_KEY_FILE"])
    worker = HttpPersonaGenerationWorker(os.environ["ANANTA_PERSONA_GENERATION_WORKER_URL"], key)
    registry, state = get_hub_evidence_registry_service(), HubPersonaGenerationState()
    access = app.extensions["project_access_authority"]
    policy = PersonaGenerationPolicy(access=access, registry=registry)
    leases = HubPersonaGenerationLeases(state=state, policy=policy, registry=registry)
    generator = HubPersonaGenerationService(
        policy=policy,
        registry=registry,
        state=state,
        leases=leases,
        worker=worker,
        admission=PersonaGeneratedSourceAdmission(access=access, registry=registry),
        repository_revision=os.environ["ANANTA_PERSONA_GENERATION_REPOSITORY_REVISION"],
        execution_profile_digest=os.environ["ANANTA_PERSONA_GENERATION_EXECUTION_PROFILE_DIGEST"],
        environment_digest=os.environ["ANANTA_PERSONA_GENERATION_ENVIRONMENT_DIGEST"],
    )
    app.extensions.update(
        persona_generation_worker_key=key,
        persona_generation_leases=leases,
        persona_generated_assets=PersonaGeneratedAssets(
            generator=generator,
            image_assets=app.extensions.get("persona_assets"),
            image_policy=app.extensions.get("persona_image_policy"),
            video_assets=app.extensions.get("persona_video_assets"),
            video_policy=app.extensions.get("persona_video_policy"),
        ),
    )

"""Small target composition reused by native generation/inspection chain tests."""

from types import SimpleNamespace

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.repositories.persona_asset_policy import SqlPersonaImagePolicies
from agent.repositories.persona_assets import SqlPersonaAssets
from agent.repositories.persona_video_assets import create_video_asset_catalog
from agent.repositories.persona_video_policies import create_video_policy_repository
from agent.services.artifact_store import ArtifactStore
from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.persona_asset_service import PersonaAssetService
from agent.services.persona_asset_storage import PersonaAssetStorage
from agent.services.persona_inspection_formats import PersonaImageInspectionFormat, PersonaVideoInspectionFormat
from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
from agent.services.persona_inspection_task_state import HubPersonaTaskState
from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
from agent.services.persona_policy_domains import PersonaImagePolicyDomain, PersonaVideoPolicyDomain
from agent.services.persona_video_asset_service import PersonaVideoAssetService
from agent.services.persona_video_storage import PersonaVideoStorage


def inspection_target(base, kind, directory, *, access, worker):
    assert kind in {"image", "video"}
    image = kind == "image"
    for model in (ArtifactDB, ArtifactVersionDB):
        model.__table__.create(base.engine, checkfirst=True)
    format = PersonaImageInspectionFormat() if image else PersonaVideoInspectionFormat()
    domain = PersonaImagePolicyDomain() if image else PersonaVideoPolicyDomain()
    state = HubPersonaTaskState(kind=kind)
    receipts = HubPersonaInspectionReceipts(state=state, registry=base.registry, format=format)
    policies = SqlPersonaImagePolicies(base.engine) if image else create_video_policy_repository(base.engine)
    policies.initialize()
    policy = PersonaAssetPolicyService(
        access=access,
        policies=policies,
        sources=base.registry,
        inspection_receipts=receipts,
        domain=domain,
    )
    tasks = HubPersonaInspectionTasks(
        policy=policy,
        worker=worker,
        state=state,
        registry=base.registry,
        format=format,
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    )
    leases = HubPersonaInspectionLeases(state=state, policy=policy, registry=base.registry, kind=kind)
    catalog = SqlPersonaAssets(base.engine) if image else create_video_asset_catalog(base.engine)
    catalog.initialize()
    store = ArtifactStore(directory)
    storage = PersonaAssetStorage(store) if image else PersonaVideoStorage(store)
    assets = (
        PersonaAssetService(policy=policy, tasks=tasks, catalog=catalog, storage=storage)
        if image
        else PersonaVideoAssetService(policy=policy, tasks=tasks, catalog=catalog, storage=storage)
    )
    return SimpleNamespace(assets=assets, policy=policy, tasks=tasks, leases=leases, state=state, store=store)

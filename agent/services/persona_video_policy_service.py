"""Video-scoped policy composition with explicit Registry and receipt ports."""

import time

from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.persona_policy_domains import PersonaVideoPolicyDomain


def create_video_policy_service(*, access, policies, sources, inspection_receipts, clock=time.time):
    return PersonaAssetPolicyService(
        access=access,
        policies=policies,
        sources=sources,
        inspection_receipts=inspection_receipts,
        clock=clock,
        domain=PersonaVideoPolicyDomain(),
    )

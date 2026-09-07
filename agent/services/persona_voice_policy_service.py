"""Voice policy composition with explicit Hub Registry and inspection receipt ports."""

import time

from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.persona_policy_domains import PersonaVoicePolicyDomain


def create_voice_policy_service(*, access, policies, sources, inspection_receipts, clock=time.time):
    return PersonaAssetPolicyService(
        access=access,
        policies=policies,
        sources=sources,
        inspection_receipts=inspection_receipts,
        clock=clock,
        domain=PersonaVoicePolicyDomain(),
    )

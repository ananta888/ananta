"""Pure presentation resolution, separate from admission and persistence."""

from agent.models.persona_media import (
    PersonaMediaProfile,
    PersonaMediaResolution,
    PersonaMembership,
    ResolvedMedia,
    SelectionOrigin,
)


def resolve_media(membership: PersonaMembership, profiles: tuple[PersonaMediaProfile, ...]) -> PersonaMediaResolution:
    """Pure deterministic resolution, not asset admission or authorization.

    A Hub service must obtain current membership and revision-pinned profiles
    through authorized repositories, then recheck asset grants before use.
    """
    owners = {
        "organization": membership.organization_id,
        "team": membership.team_id,
        "agent": membership.agent_id,
    }
    layers = {}
    for profile in profiles:
        if (
            profile.tenant_id != membership.tenant_id
            or profile.project_id != membership.project_id
            or profile.owner_id != owners[profile.owner_kind]
            or profile.owner_kind in layers
        ):
            raise ValueError("persona_profile_membership_mismatch")
        layers[profile.owner_kind] = profile
    resolved = []
    for kind in ("image", "voice", "video", "style"):
        origins = []
        asset, state = None, "missing"
        for owner_kind in ("agent", "team", "organization"):
            profile = layers.get(owner_kind)
            if profile is None:
                continue
            selection = getattr(profile, kind)
            origins.append(
                SelectionOrigin(
                    owner_kind=owner_kind,
                    owner_id=profile.owner_id,
                    persona_id=profile.persona_id,
                    profile_revision=profile.revision,
                    selection_state=selection.state,
                )
            )
            if selection.state in ("asset", "disabled"):
                asset, state = selection.asset, selection.state
                break
        resolved.append(ResolvedMedia(kind=kind, state=state, asset=asset, origins=tuple(origins)))
    return PersonaMediaResolution(membership=membership, media=tuple(resolved))

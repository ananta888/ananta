"""Profile metadata composition independent of image/video transport enablement."""


def configure_persona_profiles(app):
    images, videos, voices = (
        app.extensions.get(key)
        for key in ("persona_profile_images", "persona_profile_videos", "persona_profile_voices")
    )
    if app.config.get("ROLE") != "hub" or (images is None and videos is None and voices is None):
        return
    from sqlmodel import Session

    from agent.database import engine
    from agent.repositories.persona_media import SqlPersonaProfiles
    from agent.services.organization_membership_service import OrganizationMembershipService
    from agent.services.persona_profile_owners import SqlPersonaProfileOwners
    from agent.services.persona_profile_service import PersonaProfileService

    profiles = SqlPersonaProfiles(engine)
    profiles.initialize()
    app.extensions["persona_profiles"] = PersonaProfileService(
        access=app.extensions["project_access_authority"],
        memberships=OrganizationMembershipService(session_factory=lambda: Session(engine)),
        owners=SqlPersonaProfileOwners(lambda: Session(engine)),
        profiles=profiles,
        images=images,
        videos=videos,
        voices=voices,
    )

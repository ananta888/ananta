"""Opt-in Hub receipt admission, never an automatic publication policy."""

import os


def configure_persona_generated_sources(app):
    if app.config.get("ROLE") != "hub" or os.environ.get("ANANTA_PERSONA_GENERATED_SOURCES_ENABLED") != "1":
        return
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from agent.services.persona_generated_source import PersonaGeneratedSourceAdmission

    app.extensions["persona_generated_sources"] = PersonaGeneratedSourceAdmission(
        access=app.extensions["project_access_authority"],
        registry=get_hub_evidence_registry_service(),
    )

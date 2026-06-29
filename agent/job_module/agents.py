"""Job Module Agent Skill Profiles — registered into the VisualProcess SkillProfileRegistry."""
from __future__ import annotations


def register_job_skill_profiles() -> None:
    """Register all job-domain agent skill profiles into the global registry."""
    from agent.visual_process.skill_profiles import AgentSkillProfile, get_skill_profile_registry

    registry = get_skill_profile_registry()

    profiles = [
        AgentSkillProfile(
            id="job_discovery_agent",
            name="Job Discovery Agent",
            description="Findet neue Stellenangebote über konfigurierte Quellen.",
            role="discovery",
            task_kinds=["discovery"],
            capabilities=["web_search", "rss"],
            allowed_tools=["search_web", "fetch_rss", "read_file"],
            tags=["job", "discovery", "read-only"],
        ),
        AgentSkillProfile(
            id="job_posting_parser_agent",
            name="Job Posting Parser Agent",
            description="Parst und normalisiert Stellenanzeigen.",
            role="parser",
            task_kinds=["extraction"],
            capabilities=["read_only"],
            allowed_tools=["read_file", "fetch_url"],
            tags=["job", "parsing", "read-only"],
        ),
        AgentSkillProfile(
            id="company_research_agent",
            name="Company Research Agent",
            description="Recherchiert Informationen über das Unternehmen.",
            role="researcher",
            task_kinds=["research"],
            capabilities=["web_search"],
            allowed_tools=["search_web", "fetch_url"],
            tags=["job", "research", "read-only"],
        ),
        AgentSkillProfile(
            id="fit_evaluator_agent",
            name="Fit Evaluator Agent",
            description="Bewertet die Passgenauigkeit der Bewerbung.",
            role="evaluator",
            task_kinds=["evaluation", "scoring"],
            capabilities=["read_only", "llm_generate"],
            allowed_tools=["read_artifact", "read_case"],
            tags=["job", "evaluation", "read-only"],
        ),
        AgentSkillProfile(
            id="cv_matcher_agent",
            name="CV Matcher Agent",
            description="Gleicht CV gegen Stellenanforderungen ab.",
            role="matcher",
            task_kinds=["matching"],
            capabilities=["read_only", "llm_generate"],
            allowed_tools=["read_artifact"],
            tags=["job", "cv", "matching", "read-only"],
        ),
        AgentSkillProfile(
            id="cover_letter_agent",
            name="Cover Letter Agent",
            description="Erstellt Anschreiben-Entwürfe. Sendet nie selbst.",
            role="writer",
            task_kinds=["generation"],
            capabilities=["llm_generate", "write_artifact"],
            allowed_tools=["read_artifact", "write_artifact"],
            forbidden_tools=["send_email", "send_message"],
            tags=["job", "cover_letter", "generation"],
        ),
        AgentSkillProfile(
            id="email_draft_agent",
            name="Email Draft Agent",
            description="Erstellt E-Mail-Entwürfe. Sendet nie selbst.",
            role="writer",
            task_kinds=["generation"],
            capabilities=["llm_generate", "write_artifact"],
            allowed_tools=["read_artifact", "write_artifact"],
            forbidden_tools=["send_email", "send_message"],
            tags=["job", "email", "generation"],
        ),
        AgentSkillProfile(
            id="followup_agent",
            name="Follow-up Agent",
            description="Erstellt Nachfass-E-Mail-Entwürfe.",
            role="writer",
            task_kinds=["generation"],
            capabilities=["llm_generate", "write_artifact"],
            allowed_tools=["read_artifact", "write_artifact"],
            forbidden_tools=["send_email", "send_message"],
            tags=["job", "followup", "generation"],
        ),
        AgentSkillProfile(
            id="interview_prep_agent",
            name="Interview Prep Agent",
            description="Bereitet Interviewfragen und Antworten vor.",
            role="coach",
            task_kinds=["preparation"],
            capabilities=["llm_generate", "write_artifact", "web_search"],
            allowed_tools=["read_artifact", "write_artifact", "search_web"],
            tags=["job", "interview", "preparation"],
        ),
        AgentSkillProfile(
            id="rejection_analysis_agent",
            name="Rejection Analysis Agent",
            description="Analysiert Absagen und gibt Feedback.",
            role="analyst",
            task_kinds=["analysis"],
            capabilities=["read_only", "llm_generate"],
            allowed_tools=["read_artifact", "read_case"],
            tags=["job", "rejection", "analysis", "read-only"],
        ),
        AgentSkillProfile(
            id="application_audit_agent",
            name="Application Audit Agent",
            description="Auditiert Cases, Artifacts und Traces. Nur-Lesen.",
            role="auditor",
            task_kinds=["audit"],
            capabilities=["read_only"],
            allowed_tools=["read_artifact", "read_case", "read_timeline", "read_trace"],
            tags=["job", "audit", "read-only"],
        ),
    ]

    for profile in profiles:
        registry.register(profile)

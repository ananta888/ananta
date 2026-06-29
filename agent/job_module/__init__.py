# Job Application Module - First Domain Specialization of CaseFlow


def setup() -> None:
    """Register job module status machine, skill profiles, and blueprints."""
    from agent.caseflow.status_machine import (
        CaseStatusDefinition,
        register_status_machine,
    )
    from agent.job_module.models import (
        JOB_APPLICATION_STATUSES,
        JOB_APPLICATION_INITIAL,
        JOB_APPLICATION_TERMINAL,
        JOB_APPLICATION_TRANSITIONS,
    )
    from agent.job_module.agents import register_job_skill_profiles
    from agent.job_module.blueprints import register_job_blueprints
    from agent.caseflow.domain import register_case_type
    from agent.caseflow.models import CaseTypeDefinition

    # Register case type definition
    defn = CaseTypeDefinition(
        case_type="job_application",
        statuses=JOB_APPLICATION_STATUSES,
        initial_status=JOB_APPLICATION_INITIAL,
        terminal_statuses=JOB_APPLICATION_TERMINAL,
    )
    register_case_type(defn)

    # Register status machine
    machine = CaseStatusDefinition(
        statuses=JOB_APPLICATION_STATUSES,
        initial_status=JOB_APPLICATION_INITIAL,
        terminal_statuses=JOB_APPLICATION_TERMINAL,
        transitions=JOB_APPLICATION_TRANSITIONS,
    )
    register_status_machine("job_application", machine)

    # Register agent skill profiles
    register_job_skill_profiles()

    # Register VisualProcess blueprints
    register_job_blueprints()

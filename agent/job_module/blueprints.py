"""Job Module VisualProcess Blueprints — 5 workflow presets for job applications."""
from __future__ import annotations

from agent.visual_process.models import (
    ArtifactRef,
    StepIOContract,
    TransitionCondition,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
    StepPosition,
)
from agent.visual_process.presets import _step, _edge, _failure_edge


def _job_step(id: str, label: str, kind: str, skill_profile: str | None = None,
              inputs: list[ArtifactRef] | None = None,
              outputs: list[ArtifactRef] | None = None,
              x: float = 0, y: float = 0,
              gate: bool = False,
              policy_hints: list[str] | None = None) -> VisualProcessStep:
    return _step(
        id=id, label=label, kind=kind, skill_profile=skill_profile,
        inputs=inputs, outputs=outputs, x=x, y=y, gate=gate,
        policy_hints=policy_hints,
        metadata={"case_type": "job_application"},
    )


def preset_job_application_intake_flow() -> VisualProcessGraph:
    """Stellenanzeige importieren → Normalisieren → FitScore → Human Gate → Case erstellen."""
    raw_posting = ArtifactRef(name="raw_posting", kind="text")
    normalized = ArtifactRef(name="normalized_posting", kind="json")
    fit_score = ArtifactRef(name="fit_score", kind="json")
    case_ref = ArtifactRef(name="case_ref", kind="json")
    return VisualProcessGraph(
        id="preset-job-application-intake",
        name="Job Application Intake Flow",
        description="Stellenanzeige importieren, normalisieren, bewerten, genehmigen und Case anlegen.",
        tags=["job", "intake", "gate"],
        steps=[
            _job_step("s1", "Stellenanzeige importieren", "extraction",
                      skill_profile="job_posting_parser_agent",
                      inputs=[ArtifactRef(name="posting_text", kind="text", required=False)],
                      outputs=[raw_posting], x=0, y=0),
            _job_step("s2", "Normalisieren", "extraction",
                      skill_profile="job_posting_parser_agent",
                      inputs=[raw_posting], outputs=[normalized], x=200, y=0),
            _job_step("s3", "FitScore berechnen", "scoring",
                      skill_profile="fit_evaluator_agent",
                      inputs=[normalized], outputs=[fit_score], x=400, y=0),
            _job_step("s4", "Human Approval Gate", "review",
                      gate=True, policy_hints=["requires_approval", "human_gate"],
                      inputs=[normalized, fit_score], outputs=[case_ref], x=600, y=0),
            _job_step("s5", "Case erstellen", "extraction",
                      skill_profile="job_posting_parser_agent",
                      inputs=[case_ref], x=800, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3"),
            _edge("e3", "s3", "s4"),
            _edge("e4", "s4", "s5", kind="on_success"),
        ],
    )


def preset_job_discovery_to_case_flow() -> VisualProcessGraph:
    """Discovery → Dedup → Human Gate → Case."""
    discovery_results = ArtifactRef(name="discovery_results", kind="dataset")
    dedup_results = ArtifactRef(name="dedup_results", kind="dataset")
    approved_result = ArtifactRef(name="approved_result", kind="json")
    return VisualProcessGraph(
        id="preset-job-discovery-to-case",
        name="Job Discovery to Case Flow",
        description="Stellenangebote entdecken, deduplizieren, prüfen und als Case anlegen.",
        tags=["job", "discovery", "gate"],
        steps=[
            _job_step("s1", "Stellenangebote suchen", "discovery",
                      skill_profile="job_discovery_agent",
                      inputs=[ArtifactRef(name="search_profile", kind="json", required=False)],
                      outputs=[discovery_results], x=0, y=0),
            _job_step("s2", "Deduplizieren", "extraction",
                      outputs=[dedup_results], inputs=[discovery_results], x=200, y=0),
            _job_step("s3", "Human Review Gate", "review",
                      gate=True, policy_hints=["requires_approval", "human_gate", "convert_discovery_result_to_case"],
                      inputs=[dedup_results], outputs=[approved_result], x=400, y=0),
            _job_step("s4", "Case anlegen", "extraction",
                      inputs=[approved_result], x=600, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3"),
            _edge("e3", "s3", "s4", kind="on_success"),
        ],
    )


def preset_cover_letter_generation_flow() -> VisualProcessGraph:
    """CoverLetterAgent → Draft → Human Review Gate → Approved."""
    # Context inputs: provided by the case at runtime (not by a predecessor step)
    cv_art = ArtifactRef(name="cv", kind="text", required=False)
    job_posting = ArtifactRef(name="job_posting", kind="text", required=False)
    draft = ArtifactRef(name="cover_letter_draft", kind="text")
    approved = ArtifactRef(name="cover_letter_approved", kind="text")
    return VisualProcessGraph(
        id="preset-cover-letter-generation",
        name="Cover Letter Generation Flow",
        description="Anschreiben erzeugen, reviewen und freigeben.",
        tags=["job", "cover_letter", "generation", "gate"],
        steps=[
            _job_step("s1", "Anschreiben erstellen", "generation",
                      skill_profile="cover_letter_agent",
                      inputs=[cv_art, job_posting], outputs=[draft], x=0, y=0),
            _job_step("s2", "Human Review Gate", "review",
                      gate=True, policy_hints=["requires_approval", "human_gate"],
                      inputs=[draft], outputs=[approved], x=200, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
        ],
    )


def preset_interview_preparation_flow() -> VisualProcessGraph:
    """InterviewPrepAgent → Material → Human Review."""
    # Context inputs: provided by the case at runtime (not by a predecessor step)
    job_posting = ArtifactRef(name="job_posting", kind="text", required=False)
    cv_art = ArtifactRef(name="cv", kind="text", required=False)
    prep_material = ArtifactRef(name="interview_prep_material", kind="report")
    return VisualProcessGraph(
        id="preset-interview-preparation",
        name="Interview Preparation Flow",
        description="Interviewvorbereitung erstellen und reviewen.",
        tags=["job", "interview", "preparation", "gate"],
        steps=[
            _job_step("s1", "Interview vorbereiten", "preparation",
                      skill_profile="interview_prep_agent",
                      inputs=[job_posting, cv_art], outputs=[prep_material], x=0, y=0),
            _job_step("s2", "Human Review", "review",
                      gate=True, policy_hints=["requires_approval"],
                      inputs=[prep_material], x=200, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
        ],
    )


def preset_followup_flow() -> VisualProcessGraph:
    """FollowupAgent → Draft → Human Approval Gate → (manuelles Senden)."""
    # Context input: provided by the case at runtime (not by a predecessor step)
    applied_case = ArtifactRef(name="applied_case", kind="json", required=False)
    followup_draft = ArtifactRef(name="followup_draft", kind="text")
    approved_draft = ArtifactRef(name="approved_followup", kind="text")
    return VisualProcessGraph(
        id="preset-followup",
        name="Follow-up Flow",
        description="Nachfass-E-Mail erstellen und manuell senden (nach Freigabe).",
        tags=["job", "followup", "email", "gate"],
        steps=[
            _job_step("s1", "Nachfass-Entwurf erstellen", "generation",
                      skill_profile="followup_agent",
                      inputs=[applied_case], outputs=[followup_draft], x=0, y=0),
            _job_step("s2", "Human Approval Gate", "review",
                      gate=True,
                      policy_hints=["requires_approval", "human_gate", "send_followup_email"],
                      inputs=[followup_draft], outputs=[approved_draft], x=200, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
        ],
    )


JOB_APPLICATION_BLUEPRINTS: dict[str, VisualProcessGraph] = {}


def register_job_blueprints() -> None:
    """Register all job blueprints into the VisualProcess preset registry."""
    from agent.visual_process import presets as preset_module

    graphs = [
        preset_job_application_intake_flow(),
        preset_job_discovery_to_case_flow(),
        preset_cover_letter_generation_flow(),
        preset_interview_preparation_flow(),
        preset_followup_flow(),
    ]
    for graph in graphs:
        JOB_APPLICATION_BLUEPRINTS[graph.id] = graph
        preset_module._PRESETS[graph.id] = graph

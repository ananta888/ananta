"""Process Presets (VPAD-007).

Ready-made VisualProcessGraph templates for common workflows.
"""
from __future__ import annotations

from agent.visual_process.models import (
    ArtifactRef,
    LoopPolicy,
    StepIOContract,
    TransitionCondition,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
    StepPosition,
)


def _step(id: str, label: str, kind: str, role: str | None = None,
          inputs: list[ArtifactRef] | None = None,
          outputs: list[ArtifactRef] | None = None,
          x: float = 0, y: float = 0,
          skill_profile: str | None = None,
          gate: bool = False,
          policy_hints: list[str] | None = None) -> VisualProcessStep:
    return VisualProcessStep(
        id=id, label=label, kind=kind, role=role,
        agent_skill_profile_id=skill_profile,
        io=StepIOContract(inputs=inputs or [], outputs=outputs or []),
        position=StepPosition(x=x, y=y),
        gate=gate,
        policy_hints=policy_hints or [],
    )


def _edge(id: str, src: str, tgt: str, kind: str = "always", label: str | None = None) -> VisualProcessEdge:
    return VisualProcessEdge(
        id=id, source=src, target=tgt,
        condition=TransitionCondition(kind=kind),
        label=label,
    )


def _back_edge(id: str, src: str, tgt: str, max_iter: int = 3, label: str | None = None) -> VisualProcessEdge:
    return VisualProcessEdge(
        id=id, source=src, target=tgt,
        condition=TransitionCondition(
            kind="back_edge",
            loop_policy=LoopPolicy(kind="fixed", max_iterations=max_iter),
        ),
        label=label,
    )


# ── Preset definitions ────────────────────────────────────────────────────────

def preset_code_review_pipeline() -> VisualProcessGraph:
    """Analyse → Implement → Test → Review (4-step linear pipeline)."""
    code_out = ArtifactRef(name="code", kind="code")
    report_out = ArtifactRef(name="review_report", kind="report")
    test_out = ArtifactRef(name="test_results", kind="report")
    return VisualProcessGraph(
        id="preset-code-review",
        name="Code Review Pipeline",
        description="Analyse existing code, implement changes, run tests, review.",
        tags=["code", "review", "pipeline"],
        steps=[
            _step("s1", "Analyse", "analysis", "analyst", skill_profile="analyst",
                  outputs=[ArtifactRef(name="analysis_report", kind="report")], x=0, y=0),
            _step("s2", "Implement", "coding", "developer", skill_profile="coder",
                  inputs=[ArtifactRef(name="analysis_report", kind="report")],
                  outputs=[code_out], x=200, y=0),
            _step("s3", "Run Tests", "run_tests", "qa", skill_profile="tester",
                  inputs=[code_out], outputs=[test_out], x=400, y=0),
            _step("s4", "Review", "code_review", "reviewer", skill_profile="reviewer",
                  inputs=[code_out, test_out], outputs=[report_out],
                  x=600, y=0, gate=True),
        ],
        edges=[
            _edge("e1", "s1", "s2"), _edge("e2", "s2", "s3"),
            _edge("e3", "s3", "s4", kind="on_success"),
            _back_edge("e3f", "s3", "s2", max_iter=3, label="fix & retry"),
        ],
    )


def preset_tdd_loop() -> VisualProcessGraph:
    """Write tests → Implement → Run (loop until green)."""
    test_file = ArtifactRef(name="test_file", kind="code")
    impl_file = ArtifactRef(name="impl_file", kind="code")
    results = ArtifactRef(name="test_results", kind="report")
    return VisualProcessGraph(
        id="preset-tdd-loop",
        name="TDD Loop",
        description="Write failing tests, implement until they pass.",
        tags=["tdd", "testing", "loop"],
        steps=[
            _step("s1", "Write Tests", "coding", "qa", skill_profile="tester",
                  outputs=[test_file], x=0, y=0),
            _step("s2", "Implement", "coding", "developer", skill_profile="coder",
                  inputs=[test_file], outputs=[impl_file], x=200, y=0),
            _step("s3", "Run Tests", "run_tests", "qa", skill_profile="tester",
                  inputs=[test_file, impl_file], outputs=[results], x=400, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3"),
            _back_edge("e3", "s3", "s2", max_iter=5, label="fix & retry"),
        ],
    )


def preset_research_and_report() -> VisualProcessGraph:
    """Research → Summarise → Write Report."""
    findings = ArtifactRef(name="findings", kind="text")
    summary = ArtifactRef(name="summary", kind="text")
    report = ArtifactRef(name="report", kind="report")
    return VisualProcessGraph(
        id="preset-research-report",
        name="Research and Report",
        description="Gather information, summarise, produce a final report.",
        tags=["research", "report"],
        steps=[
            _step("s1", "Research", "analysis", "analyst", skill_profile="analyst",
                  outputs=[findings], x=0, y=0),
            _step("s2", "Summarise", "llm_generate", "analyst", skill_profile="analyst",
                  inputs=[findings], outputs=[summary], x=200, y=0),
            _step("s3", "Write Report", "llm_generate", "analyst", skill_profile="planner",
                  inputs=[summary], outputs=[report], x=400, y=0),
        ],
        edges=[_edge("e1", "s1", "s2"), _edge("e2", "s2", "s3")],
    )


def preset_deploy_pipeline() -> VisualProcessGraph:
    """Test → Build → Deploy (gate before deploy)."""
    build_out = ArtifactRef(name="build_artifact", kind="binary")
    test_out = ArtifactRef(name="test_results", kind="report")
    return VisualProcessGraph(
        id="preset-deploy-pipeline",
        name="Deploy Pipeline",
        description="Run tests, build, then gate-guarded deploy.",
        tags=["deploy", "devops", "ci"],
        steps=[
            _step("s1", "Run Tests", "run_tests", "qa", skill_profile="tester",
                  outputs=[test_out], x=0, y=0),
            _step("s2", "Build", "coding", "devops", skill_profile="devops",
                  inputs=[test_out], outputs=[build_out], x=200, y=0),
            _step("s3", "Deploy", "deploy", "devops", skill_profile="devops",
                  inputs=[build_out], x=400, y=0, gate=True,
                  policy_hints=["requires_approval", "mutates_production"]),
        ],
        edges=[
            _edge("e1", "s1", "s2", kind="on_success"),
            _edge("e2", "s2", "s3"),
        ],
    )


# ── Registry ──────────────────────────────────────────────────────────────────

_PRESETS: dict[str, VisualProcessGraph] = {}


def _load() -> None:
    for fn in [preset_code_review_pipeline, preset_tdd_loop,
               preset_research_and_report, preset_deploy_pipeline]:
        g = fn()
        _PRESETS[g.id] = g


_load()


def get_preset(preset_id: str) -> VisualProcessGraph | None:
    return _PRESETS.get(preset_id)


def list_presets() -> list[dict]:
    return [
        {"id": g.id, "name": g.name, "description": g.description, "tags": g.tags}
        for g in _PRESETS.values()
    ]

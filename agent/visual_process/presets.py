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
          policy_hints: list[str] | None = None,
          metadata: dict | None = None) -> VisualProcessStep:
    return VisualProcessStep(
        id=id, label=label, kind=kind, role=role,
        agent_skill_profile_id=skill_profile,
        io=StepIOContract(inputs=inputs or [], outputs=outputs or []),
        position=StepPosition(x=x, y=y),
        gate=gate,
        policy_hints=policy_hints or [],
        metadata=metadata or {},
    )


def _edge(id: str, src: str, tgt: str, kind: str = "always",
          label: str | None = None, output_name: str | None = None) -> VisualProcessEdge:
    cond = TransitionCondition(kind=kind, output_name=output_name)
    return VisualProcessEdge(id=id, source=src, target=tgt, condition=cond, label=label)


def _back_edge(id: str, src: str, tgt: str, max_iter: int = 3,
               loop_kind: str = "fixed", condition: str | None = None,
               label: str | None = None) -> VisualProcessEdge:
    lp = LoopPolicy(kind=loop_kind, max_iterations=max_iter, condition=condition)  # type: ignore[arg-type]
    return VisualProcessEdge(
        id=id, source=src, target=tgt,
        condition=TransitionCondition(kind="back_edge", loop_policy=lp),
        label=label,
    )


def _failure_edge(id: str, src: str, tgt: str, label: str | None = None) -> VisualProcessEdge:
    return VisualProcessEdge(
        id=id, source=src, target=tgt,
        condition=TransitionCondition(kind="on_failure"),
        label=label,
    )


# ── Software Engineering presets ──────────────────────────────────────────────

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
            _step("s1", "Analyse", "review", "analyst", skill_profile="analyst",
                  outputs=[ArtifactRef(name="analysis_report", kind="report")], x=0, y=0),
            _step("s2", "Implement", "patch_propose", "developer", skill_profile="coder",
                  inputs=[ArtifactRef(name="analysis_report", kind="report")],
                  outputs=[code_out], x=200, y=0),
            _step("s3", "Run Tests", "run_tests", "qa", skill_profile="tester",
                  inputs=[code_out], outputs=[test_out], x=400, y=0),
            _step("s4", "Review", "review", "reviewer", skill_profile="reviewer",
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
            _step("s1", "Write Tests", "patch_propose", "qa", skill_profile="tester",
                  outputs=[test_file], x=0, y=0),
            _step("s2", "Implement", "patch_propose", "developer", skill_profile="coder",
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
            _step("s1", "Research", "research_limited", "analyst", skill_profile="analyst",
                  outputs=[findings], x=0, y=0),
            _step("s2", "Summarise", "summarize", "analyst", skill_profile="analyst",
                  inputs=[findings], outputs=[summary], x=200, y=0),
            _step("s3", "Write Report", "summarize", "analyst", skill_profile="planner",
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
            _step("s2", "Build", "script", "devops", skill_profile="devops",
                  inputs=[test_out], outputs=[build_out], x=200, y=0),
            _step("s3", "Deploy", "shell_execute", "devops", skill_profile="devops",
                  inputs=[build_out], x=400, y=0, gate=True,
                  policy_hints=["requires_approval", "mutates_production"]),
        ],
        edges=[
            _edge("e1", "s1", "s2", kind="on_success"),
            _edge("e2", "s2", "s3"),
        ],
    )


# ── ML / AI presets ───────────────────────────────────────────────────────────

def preset_rag_pipeline() -> VisualProcessGraph:
    """query_rewrite → rag_retrieve → rerank → summarize (VPPRE-001)."""
    return VisualProcessGraph(
        id="preset-rag-pipeline",
        name="RAG-Pipeline",
        description="Vollständige RAG-Pipeline: Query-Optimierung, Abruf, Reranking und Zusammenfassung.",
        tags=["rag", "retrieval", "ml", "pipeline"],
        steps=[
            _step("s1", "Query optimieren", "query_rewrite", "ml_engineer",
                  skill_profile="ml_engineer",
                  inputs=[ArtifactRef(name="query", kind="text", required=False)],
                  outputs=[ArtifactRef(name="rewritten_query", kind="text")],
                  x=0, y=0),
            _step("s2", "RAG Abruf", "rag_retrieve", "ml_engineer",
                  skill_profile="ml_engineer",
                  inputs=[ArtifactRef(name="rewritten_query", kind="text", required=True)],
                  outputs=[ArtifactRef(name="candidates", kind="dataset")],
                  x=220, y=0,
                  metadata={"channels": ["dense", "lexical"], "top_k": 20}),
            _step("s3", "Reranking", "rerank", "ml_engineer",
                  skill_profile="ml_engineer",
                  inputs=[
                      ArtifactRef(name="rewritten_query", kind="text", required=True),
                      ArtifactRef(name="candidates", kind="dataset", required=True),
                  ],
                  outputs=[ArtifactRef(name="reranked", kind="dataset")],
                  x=440, y=0,
                  metadata={"reranker_weight": 0.15, "reranker_type": "token_overlap"}),
            _step("s4", "Zusammenfassen", "summarize", "analyst",
                  skill_profile="analyst",
                  inputs=[ArtifactRef(name="reranked", kind="dataset", required=True)],
                  outputs=[ArtifactRef(name="answer", kind="text")],
                  x=660, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"), _edge("e2", "s2", "s3"), _edge("e3", "s3", "s4"),
        ],
    )


def preset_knowledge_index_pipeline() -> VisualProcessGraph:
    """embed_chunk → turboquant_encode → script (index store) (VPPRE-002)."""
    return VisualProcessGraph(
        id="preset-knowledge-index",
        name="Wissensindex-Aufbau",
        description="Dokumente chunken, einbetten, TurboQuant-komprimieren und als Index speichern.",
        tags=["embeddings", "index", "turboquant", "ml"],
        steps=[
            _step("s1", "Chunk + Einbetten", "embed_chunk", "ml_engineer",
                  skill_profile="ml_engineer",
                  inputs=[ArtifactRef(name="raw_documents", kind="dataset", required=False)],
                  outputs=[ArtifactRef(name="chunks_with_vectors", kind="dataset")],
                  x=0, y=0,
                  metadata={"chunk_size": 512, "chunk_overlap": 64, "embedding_model": "nomic-embed-text"}),
            _step("s2", "TurboQuant 4-bit (TQ-012 PoC)", "turboquant_mse", "ml_engineer",
                  skill_profile="ml_engineer",
                  inputs=[ArtifactRef(name="chunks_with_vectors", kind="dataset", required=True)],
                  outputs=[ArtifactRef(name="quantized_vectors", kind="vector")],
                  x=240, y=0,
                  metadata={"seed": 888, "levels": 7, "store_original": False}),
            _step("s3", "Index speichern", "script", "devops",
                  skill_profile="devops",
                  inputs=[ArtifactRef(name="quantized_vectors", kind="vector", required=True)],
                  outputs=[ArtifactRef(name="index_done", kind="report")],
                  x=480, y=0),
        ],
        edges=[_edge("e1", "s1", "s2"), _edge("e2", "s2", "s3")],
    )


def preset_self_improving_agent() -> VisualProcessGraph:
    """plan_only → patch_propose → run_tests -on_failure→ evolve_prompt -back_edge(while)→ plan_only (VPPRE-003).

    Mirrors planning_service._maybe_evolve_prompt + controlled_worker_loop as an explicit VP-Graph.
    """
    return VisualProcessGraph(
        id="preset-self-improving-agent",
        name="Self-Improving Agent Loop",
        description=(
            "Autonomer Verbesserungsloop: Planen, Implementieren, Testen – "
            "bei Fehler Prompt via Evolver verbessern und erneut starten."
        ),
        tags=["self-improving", "evolution", "loop", "ml"],
        steps=[
            _step("s1", "Planen", "plan_only", "planner",
                  skill_profile="planner",
                  outputs=[ArtifactRef(name="plan", kind="text")],
                  x=0, y=0),
            _step("s2", "Implementieren", "patch_propose", "developer",
                  skill_profile="coder",
                  inputs=[ArtifactRef(name="plan", kind="text", required=True)],
                  outputs=[ArtifactRef(name="patch", kind="code")],
                  x=220, y=0),
            _step("s3", "Tests ausführen", "run_tests", "qa",
                  skill_profile="tester",
                  inputs=[ArtifactRef(name="patch", kind="code", required=True)],
                  outputs=[ArtifactRef(name="test_results", kind="report")],
                  x=440, y=0),
            _step("s4", "Prompt evolvieren", "evolve_prompt", "evolver",
                  skill_profile="evolver_agent",
                  inputs=[ArtifactRef(name="test_results", kind="report", required=True)],
                  outputs=[ArtifactRef(name="evolved_prompt", kind="text")],
                  x=330, y=120,
                  metadata={"trigger_type": "verification_failure", "analyze_only": True, "output_format": "json"}),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3"),
            _failure_edge("e3", "s3", "s4", label="Fehler → evolvieren"),
            _back_edge("e4", "s4", "s1", max_iter=3, loop_kind="while",
                       condition="output.evolved==True", label="verbessert → neu starten"),
        ],
    )


def preset_evolution_pipeline() -> VisualProcessGraph:
    """Vollständige EvolutionService-Pipeline: analyze → validate → (gate) → apply."""
    analysis_out = ArtifactRef(name="proposals", kind="json")
    validated_out = ArtifactRef(name="validated_proposal", kind="json")
    return VisualProcessGraph(
        id="preset-evolution-pipeline",
        name="Evolution Pipeline",
        description=(
            "Vollständige EvolutionService-Pipeline: Kontext analysieren, "
            "Proposal validieren, dann mit Gate-Freigabe anwenden."
        ),
        tags=["evolution", "self-improving", "gate"],
        steps=[
            _step("s1", "Evolution analysieren", "evolution_analyze", "evolver",
                  skill_profile="evolver_agent",
                  inputs=[ArtifactRef(name="context", kind="json", required=False)],
                  outputs=[analysis_out],
                  x=0, y=0,
                  metadata={"trigger_type": "manual", "analyze_only": True}),
            _step("s2", "Proposal validieren", "evolution_validate", "evolver",
                  skill_profile="evolver_agent",
                  inputs=[ArtifactRef(name="proposals", kind="json", required=True)],
                  outputs=[validated_out],
                  x=240, y=0),
            _step("s3", "Änderungen anwenden", "evolution_apply", "evolver",
                  skill_profile="evolver_agent",
                  inputs=[ArtifactRef(name="validated_proposal", kind="json", required=True)],
                  outputs=[ArtifactRef(name="apply_result", kind="report")],
                  x=480, y=0, gate=True,
                  policy_hints=["requires_approval", "self_modifying", "evolution"]),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3", kind="on_success"),
        ],
    )


def preset_codecompass_search_pipeline() -> VisualProcessGraph:
    """CodeCompass Suche: index_build → vector_search + fts_search → rerank → summarize."""
    vector_out  = ArtifactRef(name="vector_candidates", kind="dataset")
    fts_out     = ArtifactRef(name="fts_candidates",    kind="dataset")
    reranked    = ArtifactRef(name="reranked",          kind="dataset")
    answer      = ArtifactRef(name="answer",            kind="text")
    return VisualProcessGraph(
        id="preset-codecompass-search",
        name="CodeCompass Suche",
        description=(
            "CodeCompass-Pipeline: Delta-Index aufbauen, "
            "semantische + FTS-Suche parallel, dann reranken und zusammenfassen."
        ),
        tags=["codecompass", "retrieval", "search", "rag"],
        steps=[
            _step("s1", "CC: Index aufbauen", "codecompass_index_build", "retrieval_engineer",
                  skill_profile="retrieval_engineer",
                  inputs=[ArtifactRef(name="workspace_root", kind="text", required=False)],
                  outputs=[ArtifactRef(name="index_ready", kind="report")],
                  x=0, y=0,
                  metadata={"incremental": True}),
            _step("s2", "CC: Semantische Suche", "codecompass_vector_search", "retrieval_engineer",
                  skill_profile="retrieval_engineer",
                  inputs=[ArtifactRef(name="query", kind="text", required=False)],
                  outputs=[vector_out],
                  x=240, y=-60,
                  metadata={"top_k": 20, "retrieval_intent": "fuzzy_semantic"}),
            _step("s3", "CC: Full-Text Suche", "codecompass_fts_search", "retrieval_engineer",
                  skill_profile="retrieval_engineer",
                  inputs=[ArtifactRef(name="query", kind="text", required=False)],
                  outputs=[fts_out],
                  x=240, y=60,
                  metadata={"top_k": 20, "retrieval_intent": "exact_symbol"}),
            _step("s4", "Merge + Reranking", "rerank", "retrieval_engineer",
                  skill_profile="retrieval_engineer",
                  inputs=[
                      ArtifactRef(name="vector_candidates", kind="dataset", required=False),
                      ArtifactRef(name="fts_candidates",    kind="dataset", required=False),
                  ],
                  outputs=[reranked],
                  x=480, y=0,
                  metadata={"reranker_weight": 0.15, "reranker_type": "token_overlap"}),
            _step("s5", "Zusammenfassen", "summarize", "analyst",
                  skill_profile="analyst",
                  inputs=[ArtifactRef(name="reranked", kind="dataset", required=True)],
                  outputs=[answer],
                  x=720, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s1", "s3"),
            _edge("e3", "s2", "s4"),
            _edge("e4", "s3", "s4"),
            _edge("e5", "s4", "s5"),
        ],
    )


def preset_workspace_snapshot_and_diff() -> VisualProcessGraph:
    """workspace_snapshot → patch_apply → workspace_diff → review (Änderungen sichtbar machen)."""
    snapshot_out = ArtifactRef(name="before_snapshot", kind="json")
    patch_out    = ArtifactRef(name="patch",           kind="code")
    diff_out     = ArtifactRef(name="change_manifest", kind="json")
    return VisualProcessGraph(
        id="preset-workspace-snapshot-diff",
        name="Workspace Diff Pipeline",
        description=(
            "Snapshot vor Änderung → Patch anwenden → Diff berechnen → "
            "artifact_manifest.v1 erzeugen → Review der Änderungen."
        ),
        tags=["workspace", "diff", "patch", "audit"],
        steps=[
            _step("s1", "Snapshot (vorher)", "workspace_snapshot", "workspace",
                  skill_profile="workspace_agent",
                  outputs=[snapshot_out],
                  x=0, y=0),
            _step("s2", "Patch anwenden", "patch_apply", "developer",
                  skill_profile="coder",
                  inputs=[ArtifactRef(name="before_snapshot", kind="json", required=False)],
                  outputs=[patch_out],
                  x=220, y=0,
                  policy_hints=["writes_files"]),
            _step("s3", "Workspace Diff", "workspace_diff", "workspace",
                  skill_profile="workspace_agent",
                  inputs=[ArtifactRef(name="patch", kind="code", required=True)],
                  outputs=[diff_out],
                  x=440, y=0),
            _step("s4", "Änderungs-Review", "review", "reviewer",
                  skill_profile="reviewer",
                  inputs=[ArtifactRef(name="change_manifest", kind="json", required=True)],
                  outputs=[ArtifactRef(name="review_report", kind="report")],
                  x=660, y=0),
        ],
        edges=[
            _edge("e1", "s1", "s2"),
            _edge("e2", "s2", "s3"),
            _edge("e3", "s3", "s4"),
        ],
    )


def preset_self_improving_planner() -> VisualProcessGraph:
    """Minimal evolver feedback loop (VPEVOL-003)."""
    return VisualProcessGraph(
        id="preset-self-improving-planner",
        name="Self-Improving Planner",
        description="Einfacher Selbstverbesserungs-Loop: plan_only + evolve_prompt bei on_failure.",
        tags=["self-improving", "evolution", "planning"],
        steps=[
            _step("s1", "Planen", "plan_only", "planner",
                  skill_profile="planner",
                  outputs=[ArtifactRef(name="plan", kind="text")],
                  x=0, y=0),
            _step("s2", "Prompt evolvieren", "evolve_prompt", "evolver",
                  skill_profile="evolver_agent",
                  inputs=[ArtifactRef(name="plan", kind="text", required=False)],
                  outputs=[ArtifactRef(name="evolved_prompt", kind="text")],
                  x=200, y=80,
                  metadata={"trigger_type": "verification_failure", "analyze_only": True, "output_format": "json"}),
        ],
        edges=[
            _failure_edge("e1", "s1", "s2", label="Fehler → evolvieren"),
            _back_edge("e2", "s2", "s1", max_iter=3, loop_kind="while",
                       condition="output.evolved==True", label="verbessert → neu starten"),
        ],
    )


# ── Registry ──────────────────────────────────────────────────────────────────

_PRESETS: dict[str, VisualProcessGraph] = {}


def _load() -> None:
    for fn in [
        preset_code_review_pipeline, preset_tdd_loop,
        preset_research_and_report, preset_deploy_pipeline,
        preset_rag_pipeline, preset_knowledge_index_pipeline,
        preset_self_improving_agent, preset_self_improving_planner,
        preset_evolution_pipeline,
        preset_codecompass_search_pipeline,
        preset_workspace_snapshot_and_diff,
    ]:
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

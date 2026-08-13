"""VPAD-012: Tests for Visual Process Designer (all VPDF + VPAD backend tasks)."""
from __future__ import annotations

import uuid

import pytest

from agent.services.user_session_tokens import issue_user_access_token


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def simple_graph():
    from agent.visual_process.models import (
        ArtifactRef, StepIOContract, VisualProcessEdge,
        VisualProcessGraph, VisualProcessStep, TransitionCondition,
    )
    s1 = VisualProcessStep(id="s1", label="Analyse", kind="analysis",
                           io=StepIOContract(outputs=[ArtifactRef(name="report", kind="report")]))
    s2 = VisualProcessStep(id="s2", label="Implement", kind="coding",
                           io=StepIOContract(
                               inputs=[ArtifactRef(name="report", kind="report")],
                               outputs=[ArtifactRef(name="code", kind="code")]))
    edge = VisualProcessEdge(id="e1", source="s1", target="s2")
    return VisualProcessGraph(id="g1", name="Test Graph", steps=[s1, s2], edges=[edge])


@pytest.fixture
def flask_client():
    from flask import Flask
    from agent.routes.visual_process import vp_bp
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vp_bp)
    client = app.test_client()
    token = issue_user_access_token(username="admin", role="admin")
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client


def _visual_auth_headers(username: str) -> dict[str, str]:
    token = issue_user_access_token(username=username, role="admin")
    return {"Authorization": f"Bearer {token}"}


# ── VPAD-001 + VPDF-001: Schema ───────────────────────────────────────────────

class TestSchema:
    def test_graph_creation(self, simple_graph):
        assert simple_graph.name == "Test Graph"
        assert len(simple_graph.steps) == 2

    def test_step_io_contract(self):
        from agent.visual_process.models import ArtifactRef, StepIOContract
        io = StepIOContract(
            inputs=[ArtifactRef(name="src", kind="code", required=True)],
            outputs=[ArtifactRef(name="out", kind="report")],
        )
        assert "src" in io.input_names()
        assert "out" in io.output_names()
        assert len(io.required_inputs()) == 1

    def test_entry_steps(self, simple_graph):
        entries = simple_graph.entry_steps()
        assert len(entries) == 1
        assert entries[0].id == "s1"

    def test_no_cycle_simple(self, simple_graph):
        assert not simple_graph.has_cycles()

    def test_cycle_detection(self):
        from agent.visual_process.models import (
            VisualProcessEdge, VisualProcessGraph, VisualProcessStep, TransitionCondition,
        )
        s1 = VisualProcessStep(id="s1", label="A", kind="coding")
        s2 = VisualProcessStep(id="s2", label="B", kind="coding")
        e1 = VisualProcessEdge(id="e1", source="s1", target="s2")
        e2 = VisualProcessEdge(id="e2", source="s2", target="s1")  # forward cycle!
        g = VisualProcessGraph(id="g", name="Cyclic", steps=[s1, s2], edges=[e1, e2])
        assert g.has_cycles()

    def test_back_edge_no_cycle(self):
        from agent.visual_process.models import (
            LoopPolicy, TransitionCondition, VisualProcessEdge,
            VisualProcessGraph, VisualProcessStep,
        )
        s1 = VisualProcessStep(id="s1", label="A", kind="coding")
        s2 = VisualProcessStep(id="s2", label="B", kind="coding")
        e_fwd = VisualProcessEdge(id="e1", source="s1", target="s2")
        e_back = VisualProcessEdge(
            id="e2", source="s2", target="s1",
            condition=TransitionCondition(
                kind="back_edge",
                loop_policy=LoopPolicy(kind="fixed", max_iterations=3),
            ),
        )
        g = VisualProcessGraph(id="g", name="Loop", steps=[s1, s2], edges=[e_fwd, e_back])
        assert not g.has_cycles()

    def test_loop_policy_validation(self):
        from agent.visual_process.models import LoopPolicy
        with pytest.raises(Exception):
            LoopPolicy(kind="while", condition=None)  # condition required

    def test_transition_condition_expression(self):
        from agent.visual_process.models import TransitionCondition
        with pytest.raises(Exception):
            TransitionCondition(kind="expression", expression=None)


# ── VPDF-002 + VPAD-002: Validator ───────────────────────────────────────────

class TestValidator:
    def test_valid_graph(self, simple_graph):
        from agent.visual_process.validator import VisualProcessValidator
        r = VisualProcessValidator().validate(simple_graph)
        assert r.valid
        assert len(r.errors()) == 0

    def test_empty_graph(self):
        from agent.visual_process.models import VisualProcessGraph
        from agent.visual_process.validator import GraphValidator
        g = VisualProcessGraph(id="empty", name="Empty", steps=[])
        r = GraphValidator().validate(g)
        assert not r.valid
        assert any(i.code == "empty_graph" for i in r.issues)

    def test_dangling_edge(self, simple_graph):
        from agent.visual_process.models import VisualProcessEdge
        from agent.visual_process.validator import GraphValidator
        simple_graph.edges.append(VisualProcessEdge(id="bad", source="s1", target="ghost"))
        r = GraphValidator().validate(simple_graph)
        assert not r.valid
        assert any(i.code == "dangling_edge_target" for i in r.issues)

    def test_missing_required_input(self):
        from agent.visual_process.models import (
            ArtifactRef, StepIOContract, VisualProcessGraph, VisualProcessStep,
        )
        from agent.visual_process.validator import DataflowValidator
        s1 = VisualProcessStep(id="s1", label="Start", kind="coding",
                               io=StepIOContract())
        s2 = VisualProcessStep(id="s2", label="Need input", kind="coding",
                               io=StepIOContract(inputs=[ArtifactRef(name="missing", required=True)]))
        g = VisualProcessGraph(id="g", name="G", steps=[s1, s2])
        r = DataflowValidator().validate(g)
        assert not r.valid
        assert any(i.code == "unsatisfied_input" for i in r.issues)

    def test_satisfied_input(self, simple_graph):
        from agent.visual_process.validator import DataflowValidator
        r = DataflowValidator().validate(simple_graph)
        assert r.valid

    def test_warns_unused_output(self):
        from agent.visual_process.models import (
            ArtifactRef, StepIOContract, VisualProcessGraph, VisualProcessStep,
        )
        from agent.visual_process.validator import DataflowValidator
        s1 = VisualProcessStep(id="s1", label="A", kind="coding",
                               io=StepIOContract(outputs=[ArtifactRef(name="unused", kind="text")]))
        g = VisualProcessGraph(id="g", name="G", steps=[s1])
        r = DataflowValidator().validate(g)
        assert r.valid  # unused output is a warning, not error
        assert any(i.code == "unused_output" for i in r.issues)

    def test_unreachable_step_warning(self):
        from agent.visual_process.models import (
            VisualProcessEdge, VisualProcessGraph, VisualProcessStep,
        )
        from agent.visual_process.validator import GraphValidator
        s1 = VisualProcessStep(id="s1", label="A", kind="coding")
        s2 = VisualProcessStep(id="s2", label="B", kind="coding")
        s3 = VisualProcessStep(id="s3", label="Orphan", kind="coding")
        # s1→s2 connected; s3 has no edges
        edge = VisualProcessEdge(id="e1", source="s1", target="s2")
        g = VisualProcessGraph(id="g", name="G", steps=[s1, s2, s3], edges=[edge])
        r = GraphValidator().validate(g)
        assert any(i.code == "unreachable_step" and i.step_id == "s3" for i in r.warnings())

    def test_model_routing_and_runtime_truth_are_independent_validator_passes(self):
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
        from agent.visual_process.validator import GraphValidator

        step = VisualProcessStep(
            id="embed",
            label="Embed",
            kind="embed_api",
            metadata={
                "provider": "hash",
                "model_routing": {"allow_cloud": False},
            },
        )
        result = GraphValidator().validate(
            VisualProcessGraph(id="routing-runtime-truth", name="Routing", steps=[step])
        )

        assert result.valid
        assert any(issue.code == "step_uses_network" for issue in result.issues)

    def test_effective_graph_and_step_model_routing_is_validated(self):
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
        from agent.visual_process.validator import GraphValidator

        graph = VisualProcessGraph(
            id="effective-routing",
            name="Effective routing",
            metadata={
                "model_routing": {
                    "context_recovery_strategies": [
                        "propose_task_plan",
                        "require_approval",
                        "stop",
                    ],
                    "require_approval_for_generated_plan": True,
                }
            },
            steps=[
                VisualProcessStep(
                    id="unsafe-override",
                    label="Unsafe override",
                    kind="coding",
                    metadata={
                        "model_routing": {
                            "require_approval_for_generated_plan": False,
                        }
                    },
                )
            ],
        )

        result = GraphValidator().validate(graph)

        assert not result.valid
        assert any(
            issue.code == "model_routing_invalid"
            and issue.step_id == "unsafe-override"
            and "after graph/step merge" in issue.message
            for issue in result.errors()
        )


# ── VPDF-003: Context Assembly ────────────────────────────────────────────────

class TestContextAssembly:
    def test_basic_assembly(self, simple_graph):
        from agent.visual_process.context_assembly import StepContextAssembler
        assembler = StepContextAssembler(simple_graph)
        ctx = assembler.assemble("s2", runtime_artifacts={"report": "/tmp/report.md"})
        assert ctx.step_id == "s2"
        assert ctx.inputs.get("report") == "/tmp/report.md"
        assert "code" in ctx.expected_outputs

    def test_predecessor_ids(self, simple_graph):
        from agent.visual_process.context_assembly import StepContextAssembler
        ctx = StepContextAssembler(simple_graph).assemble("s2")
        assert "s1" in ctx.predecessor_step_ids

    def test_missing_required_input_resolved_to_none(self, simple_graph):
        from agent.visual_process.context_assembly import StepContextAssembler
        ctx = StepContextAssembler(simple_graph).assemble("s2", runtime_artifacts={})
        assert ctx.inputs.get("report") is None  # required but not in pool

    def test_unknown_step_raises(self, simple_graph):
        from agent.visual_process.context_assembly import StepContextAssembler
        with pytest.raises(ValueError, match="not found"):
            StepContextAssembler(simple_graph).assemble("ghost")

    def test_skill_profile_tools(self, simple_graph):
        from agent.visual_process.context_assembly import StepContextAssembler
        simple_graph.steps[0].agent_skill_profile_id = "myprofile"
        profiles = {"myprofile": {"allowed_tools": ["read_file", "git_diff"]}}
        ctx = StepContextAssembler(simple_graph, skill_profiles=profiles).assemble("s1")
        assert "read_file" in ctx.allowed_tools


# ── VPAD-003: Blueprint Mapper ────────────────────────────────────────────────

class TestBlueprintMapper:
    def test_steps_count(self, simple_graph):
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_steps
        steps = graph_to_blueprint_steps(simple_graph)
        assert len(steps) == 2

    def test_step_fields(self, simple_graph):
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_steps
        steps = graph_to_blueprint_steps(simple_graph)
        s1 = next(s for s in steps if s["step_id"] == "s1")
        assert s1["role_name"] == "default"
        assert s1["task_kind"] == "analysis"
        assert "report" in s1["produces"]

    def test_depends_on(self, simple_graph):
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_steps
        steps = graph_to_blueprint_steps(simple_graph)
        s2 = next(s for s in steps if s["step_id"] == "s2")
        assert "s1" in s2["depends_on"]

    def test_blueprint_dict(self, simple_graph):
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict
        bp = graph_to_blueprint_dict(simple_graph)
        assert "workflow" in bp
        assert len(bp["workflow"]["steps"]) == 2

    def test_gate_in_checks(self):
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_steps
        s = VisualProcessStep(id="s1", label="Deploy", kind="deploy", gate=True)
        g = VisualProcessGraph(id="g", name="G", steps=[s])
        steps = graph_to_blueprint_steps(g)
        assert steps[0]["checks"].get("approval_required") is True

    def test_model_routing_transferred_to_blueprint_step(self):
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
        from agent.visual_process.blueprint_mapper import graph_to_blueprint_steps

        s = VisualProcessStep(
            id="s1",
            label="Analyse",
            kind="analysis",
            metadata={"model_routing": {"preferred_profile_id": "local_lmstudio_phi_json_worker"}},
        )
        g = VisualProcessGraph(
            id="g",
            name="G",
            metadata={"model_routing": {"fallback_group_id": "local_first_cheap"}},
            steps=[s],
        )
        steps = graph_to_blueprint_steps(g)
        assert steps[0]["model_routing"]["fallback_group_id"] == "local_first_cheap"
        assert steps[0]["model_routing"]["preferred_profile_id"] == "local_lmstudio_phi_json_worker"


# ── VPAD-007: Presets ─────────────────────────────────────────────────────────

class TestPresets:
    def test_list_presets(self):
        from agent.visual_process.presets import list_presets
        presets = list_presets()
        assert len(presets) >= 4
        ids = [p["id"] for p in presets]
        assert "preset-code-review" in ids
        assert "preset-tdd-loop" in ids

    def test_get_preset(self):
        from agent.visual_process.presets import get_preset
        g = get_preset("preset-code-review")
        assert g is not None
        assert len(g.steps) == 4

    def test_tdd_loop_has_back_edge(self):
        from agent.visual_process.presets import get_preset
        g = get_preset("preset-tdd-loop")
        back = [e for e in g.edges if e.is_back_edge()]
        assert len(back) == 1

    def test_deploy_has_gate(self):
        from agent.visual_process.presets import get_preset
        g = get_preset("preset-deploy-pipeline")
        gate_steps = [s for s in g.steps if s.gate]
        assert len(gate_steps) == 1

    def test_preset_validates(self):
        from agent.visual_process.presets import get_preset, list_presets
        from agent.visual_process.validator import VisualProcessValidator
        v = VisualProcessValidator()
        for p in list_presets():
            g = get_preset(p["id"])
            r = v.validate(g)
            assert r.valid, f"Preset '{p['id']}' failed: {[i.as_dict() for i in r.errors()]}"


# ── VPDF-005 + VPDF-006: Skill Profiles ──────────────────────────────────────

class TestSkillProfiles:
    def test_builtin_profiles_loaded(self):
        from agent.visual_process.skill_profiles import get_skill_profile_registry
        reg = get_skill_profile_registry()
        assert reg.get("coder") is not None
        assert reg.get("analyst") is not None

    def test_register_custom(self):
        from agent.visual_process.skill_profiles import AgentSkillProfile, SkillProfileRegistry
        reg = SkillProfileRegistry()
        reg.register(AgentSkillProfile(id="custom", name="Custom", task_kinds=["custom_task"]))
        assert reg.get("custom") is not None

    def test_for_task_kind(self):
        from agent.visual_process.skill_profiles import get_skill_profile_registry
        reg = get_skill_profile_registry()
        profiles = reg.for_task_kind("run_tests")
        assert any(p.id == "tester" for p in profiles)

    def test_library_format(self):
        from agent.visual_process.skill_profiles import get_skill_profile_registry
        lib = get_skill_profile_registry().as_library()
        assert all("id" in p and "name" in p for p in lib)

    def test_supports_kind(self):
        from agent.visual_process.skill_profiles import get_skill_profile_registry
        coder = get_skill_profile_registry().get("coder")
        assert coder.supports_kind("coding")
        assert not coder.supports_kind("deploy")


# ── VPAD-008: Policy Hints ────────────────────────────────────────────────────

class TestPolicyHints:
    def test_deploy_gets_high_risk(self):
        from agent.visual_process.models import VisualProcessStep
        from agent.visual_process.policy_hints import classify_step, HINT_HIGH_RISK
        step = VisualProcessStep(id="s1", label="Deploy", kind="deploy")
        hints = classify_step(step)
        assert HINT_HIGH_RISK in hints

    def test_read_only_for_analysis(self):
        from agent.visual_process.models import VisualProcessStep
        from agent.visual_process.policy_hints import classify_step
        step = VisualProcessStep(id="s1", label="Analyse", kind="analysis")
        assert "read_only" in classify_step(step)

    def test_gate_adds_requires_approval(self):
        from agent.visual_process.models import VisualProcessStep
        from agent.visual_process.policy_hints import classify_step, HINT_REQUIRES_APPROVAL
        step = VisualProcessStep(id="s1", label="Review", kind="coding", gate=True)
        assert HINT_REQUIRES_APPROVAL in classify_step(step)

    def test_policy_summary(self, simple_graph):
        from agent.visual_process.policy_hints import policy_summary, annotate_graph
        summary = policy_summary(annotate_graph(simple_graph))
        assert "all_hints" in summary
        assert "has_llm_calls" in summary

    def test_annotate_graph(self, simple_graph):
        from agent.visual_process.policy_hints import annotate_graph
        annotated = annotate_graph(simple_graph)
        for step in annotated.steps:
            assert isinstance(step.policy_hints, list)


# ── VPAD-009: Mermaid Export ──────────────────────────────────────────────────

class TestMermaidExport:
    def test_mermaid_contains_steps(self, simple_graph):
        from agent.visual_process.mermaid_export import to_mermaid
        m = to_mermaid(simple_graph)
        assert "s1" in m and "s2" in m
        assert "flowchart LR" in m

    def test_mermaid_td_direction(self, simple_graph):
        from agent.visual_process.mermaid_export import to_mermaid
        m = to_mermaid(simple_graph, direction="TD")
        assert "flowchart TD" in m

    def test_tui_text_contains_labels(self, simple_graph):
        from agent.visual_process.mermaid_export import to_tui_text
        t = to_tui_text(simple_graph)
        assert "Analyse" in t
        assert "Implement" in t

    def test_back_edge_dotted_arrow(self):
        from agent.visual_process.presets import get_preset
        from agent.visual_process.mermaid_export import to_mermaid
        g = get_preset("preset-tdd-loop")
        m = to_mermaid(g)
        assert "-..->" in m


# ── VPAD-011: Run State ───────────────────────────────────────────────────────

class TestRunState:
    def test_initial_pending(self):
        from agent.visual_process.run_state import ProcessRunState
        rs = ProcessRunState(run_id="r1", graph_id="g1")
        rs.init_steps(["s1", "s2"])
        assert rs.overall_status() == "pending"

    def test_all_done(self):
        from agent.visual_process.run_state import ProcessRunState
        rs = ProcessRunState(run_id="r1", graph_id="g1")
        rs.init_steps(["s1", "s2"])
        rs.get_step("s1").complete()
        rs.get_step("s2").complete()
        assert rs.overall_status() == "done"

    def test_one_failed(self):
        from agent.visual_process.run_state import ProcessRunState
        rs = ProcessRunState(run_id="r1", graph_id="g1")
        rs.init_steps(["s1", "s2"])
        rs.get_step("s1").fail("some error")
        assert rs.overall_status() == "failed"

    def test_as_dict(self):
        from agent.visual_process.run_state import ProcessRunState
        rs = ProcessRunState(run_id="r1", graph_id="g1")
        rs.init_steps(["s1"])
        d = rs.as_dict()
        assert "overall_status" in d
        assert "steps" in d


# ── API routes ────────────────────────────────────────────────────────────────

class TestVisualProcessAPI:
    def test_model_routing_detail_endpoints_require_user_authentication(self):
        from flask import Flask

        from agent.routes.visual_process import vp_bp

        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(vp_bp)
        client = app.test_client()

        assert client.post("/api/visual-process/dry-run", json={}).status_code == 401
        assert client.post("/api/visual-process/model-routing/validate", json={}).status_code == 401
        assert client.post("/api/visual-process/model-routing/estimate-cost", json={}).status_code == 401

    def test_list_presets(self, flask_client):
        r = flask_client.get("/api/visual-process/presets")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) >= 4

    def test_get_preset(self, flask_client):
        r = flask_client.get("/api/visual-process/presets/preset-code-review")
        assert r.status_code == 200
        assert r.get_json()["name"] == "Code Review Pipeline"

    def test_get_preset_not_found(self, flask_client):
        r = flask_client.get("/api/visual-process/presets/does-not-exist")
        assert r.status_code == 404

    @pytest.mark.parametrize(
        "path",
        (
            "/api/visual-process/skill-profiles",
            "/api/visual-process/skill-profiles/coder",
        ),
    )
    def test_skill_profile_endpoints_require_user_authentication(self, flask_client, path):
        anonymous_client = flask_client.application.test_client()

        response = anonymous_client.get(path)

        assert response.status_code == 401

    def test_skill_profiles(self, flask_client):
        r = flask_client.get("/api/visual-process/skill-profiles")
        assert r.status_code == 200
        data = r.get_json()
        assert any(p["id"] == "coder" for p in data)

    def test_skill_profile_detail(self, flask_client):
        r = flask_client.get("/api/visual-process/skill-profiles/coder")
        assert r.status_code == 200
        assert r.get_json()["id"] == "coder"

    def test_validate_valid(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-code-review")
        r = flask_client.post("/api/visual-process/validate", json=graph.model_dump())
        assert r.status_code == 200
        assert r.get_json()["valid"] is True

    def test_validate_invalid(self, flask_client):
        r = flask_client.post("/api/visual-process/validate", json={"name": "bad", "steps": []})
        assert r.status_code == 422

    def test_dry_run(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-tdd-loop")
        r = flask_client.post("/api/visual-process/dry-run", json=graph.model_dump())
        assert r.status_code == 200
        data = r.get_json()
        assert data["dry_run"] is True
        assert "blueprint" in data
        assert "policy_summary" in data

    def test_dry_run_marks_ml_intern_train_lora_executable(self, flask_client, tmp_path):
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep

        graph = VisualProcessGraph(
            id="lora-flow",
            name="LoRA Flow",
            steps=[
                VisualProcessStep(
                    id="train",
                    label="Train LoRA",
                    kind="ml_intern_train_lora",
                    gate=True,
                    metadata={
                        "dataset_path": "train.jsonl",
                        "base_model": "qwen2.5-coder-7b",
                        "artifact_root": str(tmp_path / "artifacts"),
                        "dataset_root": str(tmp_path / "datasets"),
                    },
                )
            ],
        )

        r = flask_client.post("/api/visual-process/dry-run", json=graph.model_dump())
        assert r.status_code == 200
        plan = r.get_json()["step_execution_plan"][0]
        assert plan["kind"] == "ml_intern_train_lora"
        assert plan["execution_mode"] == "vp_adapter"
        assert plan["executable"] is True
        assert plan["requires_approval"] is True

    def test_dry_run_includes_model_plan(self, flask_client, monkeypatch):
        from agent.services.model_profile_loader import ModelProfile
        from agent.services.model_profile_resolver import ModelProfileResolver, RoutingRules
        from agent.services.model_invocation_service import ModelInvocationService
        from agent.visual_process.models import VisualProcessGraph, VisualProcessStep

        local = ModelProfile(
            profile_id="local_lmstudio_phi_json_worker",
            provider_id="lmstudio",
            model="auto",
            local=True,
            block_secret_context=False,
            supports_json=True,
            tool_calling_mode="prompt_json",
            fallback_group="local_first_cheap",
            fallback_rank=10,
        )
        gemma = ModelProfile(
            profile_id="openrouter_gemma3_4b_cheap_json",
            provider_id="openrouter",
            model="google/gemma-3-4b-it",
            cloud=True,
            cloud_allowed=True,
            block_secret_context=True,
            supports_json=True,
            fallback_group="local_first_cheap",
            fallback_rank=20,
        )
        resolver = ModelProfileResolver(
            [local, gemma],
            routing_rules=RoutingRules.from_dict({
                "fallback_groups": {
                    "local_first_cheap": {
                        "ordered_profiles": [local.profile_id, gemma.profile_id]
                    }
                }
            }),
        )
        monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
        graph = VisualProcessGraph(
            id="g",
            name="G",
            metadata={"model_routing": {"fallback_group_id": "local_first_cheap", "allow_cloud": True}},
            steps=[VisualProcessStep(id="s1", label="Analyse", kind="analysis")],
        )

        r = flask_client.post("/api/visual-process/dry-run", json=graph.model_dump())
        assert r.status_code == 200
        data = r.get_json()
        assert data["per_step_model_plan"][0]["selected_profile_id"] == "local_lmstudio_phi_json_worker"
        assert data["per_step_model_plan"][0]["candidate_chain"] == [
            "local_lmstudio_phi_json_worker",
            "openrouter_gemma3_4b_cheap_json",
        ]

    def test_mermaid_endpoint(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-code-review")
        r = flask_client.post("/api/visual-process/mermaid",
                              json={**graph.model_dump(), "include_tui": True})
        assert r.status_code == 200
        data = r.get_json()
        assert "flowchart" in data["mermaid"]
        assert "tui" in data

    def test_policy_summary_endpoint(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-deploy-pipeline")
        r = flask_client.post("/api/visual-process/policy-summary", json=graph.model_dump())
        assert r.status_code == 200
        data = r.get_json()
        assert data["summary"]["mutates_production"] is True

    def test_assemble_context_endpoint(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-code-review")
        r = flask_client.post("/api/visual-process/assemble-context", json={
            **graph.model_dump(),
            "step_id": "s2",
            "runtime_artifacts": {"analysis_report": "/tmp/report.md"},
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data["step_id"] == "s2"

    def test_assemble_context_missing_step(self, flask_client):
        from agent.visual_process.presets import get_preset
        graph = get_preset("preset-code-review")
        r = flask_client.post("/api/visual-process/assemble-context", json={
            **graph.model_dump(), "step_id": "ghost",
        })
        assert r.status_code == 404


def test_graph_crud_is_authenticated_and_tenant_owned(flask_client, simple_graph):
    from agent.services.chat_process_binding import load_graph

    graph_id = f"graph-security-{uuid.uuid4().hex[:12]}"
    graph = simple_graph.model_copy(update={"id": graph_id})
    owner_headers = _visual_auth_headers("admin")
    foreign_headers = _visual_auth_headers("foreign-admin")

    anonymous_app = flask_client.application.test_client()
    anonymous = anonymous_app.post("/api/visual-process/graphs", json=graph.model_dump())
    assert anonymous.status_code == 401

    created = flask_client.post(
        "/api/visual-process/graphs",
        json=graph.model_dump(),
        headers=owner_headers,
    )
    assert created.status_code == 200
    try:
        loaded = flask_client.get(f"/api/visual-process/graphs/{graph_id}", headers=owner_headers)
        foreign_get = flask_client.get(f"/api/visual-process/graphs/{graph_id}", headers=foreign_headers)
        foreign_list = flask_client.get("/api/visual-process/graphs", headers=foreign_headers)
        collision = flask_client.post(
            "/api/visual-process/graphs",
            json=graph.model_dump(),
            headers=foreign_headers,
        )
        foreign_update = flask_client.put(
            f"/api/visual-process/graphs/{graph_id}",
            json=graph.model_dump(),
            headers=foreign_headers,
        )
        foreign_delete = flask_client.delete(
            f"/api/visual-process/graphs/{graph_id}",
            headers=foreign_headers,
        )

        assert loaded.status_code == 200
        assert "owner_principal" not in loaded.json.get("metadata", {})
        assert foreign_get.status_code == foreign_update.status_code == foreign_delete.status_code == 404
        assert graph_id not in {item["id"] for item in foreign_list.json}
        assert collision.status_code == 409
        assert load_graph(graph_id, tenant_id="foreign-admin", subject_id="foreign-admin") is None
        assert load_graph(graph_id, tenant_id="admin", subject_id="admin") is not None
    finally:
        deleted = flask_client.delete(f"/api/visual-process/graphs/{graph_id}", headers=owner_headers)
        assert deleted.status_code == 204


def test_graph_v2_save_uses_authoritative_cas_and_etag(flask_client, simple_graph):
    graph_id = f"graph-cas-{uuid.uuid4().hex[:12]}"
    graph = simple_graph.model_copy(update={"id": graph_id})
    headers = _visual_auth_headers("admin")
    second_client = flask_client.application.test_client()
    assert second_client is not flask_client

    created = flask_client.post(
        "/api/visual-process/v2/graphs",
        json={"graph": graph.model_dump()},
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json["definition_revision"] == 1
    assert created.json["base_graph_hash"]
    assert created.headers["ETag"] == f'"{created.json["base_graph_hash"]}"'

    try:
        first_loaded = flask_client.get(f"/api/visual-process/graphs/{graph_id}", headers=headers)
        second_loaded = second_client.get(f"/api/visual-process/graphs/{graph_id}", headers=headers)
        assert first_loaded.status_code == second_loaded.status_code == 200
        assert first_loaded.json["definition_revision"] == second_loaded.json["definition_revision"] == 1
        assert (
            first_loaded.json["base_graph_hash"]
            == second_loaded.json["base_graph_hash"]
            == created.json["base_graph_hash"]
        )

        changed_graph = dict(first_loaded.json)
        changed_graph["name"] = "CAS update"
        changed = flask_client.put(
            f"/api/visual-process/v2/graphs/{graph_id}",
            json={"graph": changed_graph, "expected_revision": 1},
            headers={**headers, "If-Match": f'"{created.json["base_graph_hash"]}"'},
        )
        assert changed.status_code == 200
        assert changed.json["definition_revision"] == 2
        assert changed.json["version"] == "1.1"

        stale_graph = dict(second_loaded.json)
        stale_graph["name"] = "Second client local draft"
        stale = second_client.put(
            f"/api/visual-process/v2/graphs/{graph_id}",
            json={"graph": stale_graph, "expected_revision": 1},
            headers={**headers, "If-Match": f'"{created.json["base_graph_hash"]}"'},
        )
        assert stale.status_code == 409
        assert stale.json["error_code"] == "visual_process_definition_conflict"
        assert stale.json["actual_revision"] == 2
        assert stale_graph["name"] == "Second client local draft"
        assert stale_graph["definition_revision"] == 1

        current = second_client.get(f"/api/visual-process/graphs/{graph_id}", headers=headers)
        assert current.json["name"] == "CAS update"

        legacy_graph = dict(current.json)
        legacy_graph.pop("definition_revision", None)
        legacy_graph.pop("base_graph_hash", None)
        legacy_graph["name"] = "Compatibility update"
        compatible = second_client.put(
            f"/api/visual-process/graphs/{graph_id}",
            json=legacy_graph,
            headers=headers,
        )
        assert compatible.status_code == 200
        assert compatible.json["definition_revision"] == 3
    finally:
        flask_client.delete(f"/api/visual-process/graphs/{graph_id}", headers=headers)

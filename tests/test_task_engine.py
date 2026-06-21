"""te-014: End-to-end tests for TaskIntentRouter, TaskClassResolver, and read-only handlers."""
from __future__ import annotations

import json
import pytest


# ── TaskIntentRouter ──────────────────────────────────────────────────────────

class TestTaskIntentRouter:
    @pytest.fixture(autouse=True)
    def router(self):
        from agent.services.task_intent_router import TaskIntentRouter
        self.router = TaskIntentRouter()

    def test_list_files_tool_call(self):
        r = self.router.route({"tool_calls": [{"name": "list_files"}]})
        assert r.intent == "list_files"
        assert r.task_class == "deterministic"
        assert not r.llm_required

    def test_read_file_tool_call(self):
        r = self.router.route({"tool_calls": [{"name": "read_file"}]})
        assert r.intent == "read_file"
        assert r.task_class == "deterministic"

    def test_grep_search_tool_call(self):
        r = self.router.route({"tool_calls": [{"name": "grep_search"}]})
        assert r.intent == "grep_search"
        assert not r.llm_required

    def test_git_status_command(self):
        r = self.router.route({"command": "git status --short"})
        assert r.intent == "git_status"
        assert r.task_class == "deterministic"

    def test_git_diff_command(self):
        r = self.router.route({"command": "git diff HEAD"})
        assert r.intent == "git_diff"
        assert not r.llm_required

    def test_grep_command(self):
        r = self.router.route({"command": "grep -rn foo ."})
        assert r.intent == "grep_search"

    def test_ls_command(self):
        r = self.router.route({"command": "ls -la src/"})
        assert r.intent == "list_files"

    def test_pytest_command(self):
        r = self.router.route({"command": "pytest tests/"})
        assert r.intent == "run_tests"
        assert r.task_class == "hybrid"
        assert not r.llm_required

    def test_unknown_defaults_to_llm(self):
        r = self.router.route({"command": "some_exotic_tool --flag"})
        assert r.task_class == "llm_required"
        assert r.llm_required

    def test_task_kind_list_files(self):
        r = self.router.route({"task_kind": "list_files"})
        assert r.intent == "list_files"

    def test_task_kind_run_tests(self):
        r = self.router.route({"task_kind": "run_tests"})
        assert r.intent == "run_tests"
        assert r.task_class == "hybrid"

    def test_task_kind_audio_transcribe_is_hybrid(self):
        r = self.router.route({"task_kind": "audio_transcribe"})
        assert r.intent == "audio_transcribe"
        assert r.task_class == "hybrid"

    def test_task_kind_audio_transcribe_with_postprocess_is_hybrid(self):
        r = self.router.route({"task_kind": "audio_transcribe_with_postprocess"})
        assert r.intent == "audio_transcribe_with_postprocess"
        assert r.task_class == "hybrid"

    def test_deterministic_handler_id_set(self):
        r = self.router.route({"tool_calls": [{"name": "git_status"}]})
        assert r.deterministic_handler_id == "git_status"

    def test_llm_required_no_handler_id(self):
        r = self.router.route({})
        assert r.deterministic_handler_id is None


# ── TaskClassResolver ─────────────────────────────────────────────────────────

class TestTaskClassResolver:
    @pytest.fixture(autouse=True)
    def resolver(self):
        from agent.services.task_class_resolver import TaskClassResolver
        self.resolver = TaskClassResolver()

    def test_kind_override_list_files(self):
        r = self.resolver.resolve({"task_kind": "list_files"})
        assert r.task_class == "deterministic"
        assert not r.llm_required

    def test_kind_override_run_tests(self):
        r = self.resolver.resolve({"task_kind": "run_tests"})
        assert r.task_class == "hybrid"

    def test_kind_override_voice_command(self):
        r = self.resolver.resolve({"task_kind": "voice_command"})
        assert r.task_class == "hybrid"
        assert not r.llm_required

    def test_kind_override_llm_generate(self):
        r = self.resolver.resolve({"task_kind": "llm_generate"})
        assert r.task_class == "llm_required"
        assert r.llm_required

    def test_capability_forces_llm(self):
        r = self.resolver.resolve({
            "task_kind": "list_files",
            "required_capabilities": ["write_file"],
        })
        assert r.task_class == "llm_required"
        assert "write_file" in r.reason

    def test_shell_exec_forces_llm(self):
        r = self.resolver.resolve({"required_capabilities": ["shell_exec"]})
        assert r.llm_required

    def test_code_review_kind(self):
        r = self.resolver.resolve({"task_kind": "code_review"})
        assert r.task_class == "llm_required"

    def test_intent_router_fallback(self):
        r = self.resolver.resolve({"command": "grep -rn TODO ."})
        assert r.task_class == "deterministic"
        assert r.intent == "grep_search"

    def test_reason_populated(self):
        r = self.resolver.resolve({"task_kind": "git_diff"})
        assert r.reason.startswith("kind_override:")

    def test_handler_id_set_for_deterministic(self):
        r = self.resolver.resolve({"task_kind": "json_validate"})
        assert r.deterministic_handler_id == "json_validate"

    def test_handler_id_none_for_llm(self):
        r = self.resolver.resolve({"task_kind": "goal_plan"})
        assert r.deterministic_handler_id is None


# ── Read-only handlers ────────────────────────────────────────────────────────

class TestListFilesHandler:
    def test_propose(self, tmp_path):
        from agent.services.readonly_handlers import ListFilesHandler
        h = ListFilesHandler()
        result = h.propose(task={"path": str(tmp_path)})
        assert result["tool_calls"][0]["name"] == "list_files"

    def test_execute_existing_dir(self, tmp_path):
        from agent.services.readonly_handlers import ListFilesHandler
        (tmp_path / "foo.txt").write_text("x")
        h = ListFilesHandler()
        r = h.execute(task={"path": str(tmp_path)})
        assert r["exit_code"] == 0
        assert "foo.txt" in r["output"]

    def test_execute_missing_dir(self, tmp_path):
        from agent.services.readonly_handlers import ListFilesHandler
        h = ListFilesHandler()
        r = h.execute(task={"path": str(tmp_path / "noexist")})
        assert r["exit_code"] != 0


class TestReadFileHandler:
    def test_execute_reads_file(self, tmp_path):
        from agent.services.readonly_handlers import ReadFileHandler
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        r = ReadFileHandler().execute(task={"path": str(f)})
        assert r["exit_code"] == 0
        assert "hello world" in r["output"]

    def test_execute_missing_file(self, tmp_path):
        from agent.services.readonly_handlers import ReadFileHandler
        r = ReadFileHandler().execute(task={"path": str(tmp_path / "ghost.txt")})
        assert r["exit_code"] != 0

    def test_execute_no_path(self):
        from agent.services.readonly_handlers import ReadFileHandler
        r = ReadFileHandler().execute(task={})
        assert r["exit_code"] != 0


class TestGrepSearchHandler:
    def test_finds_pattern(self, tmp_path):
        from agent.services.readonly_handlers import GrepSearchHandler
        f = tmp_path / "code.py"
        f.write_text("def hello():\n    pass\n")
        r = GrepSearchHandler().execute(task={"pattern": "def hello", "path": str(tmp_path)})
        assert r["exit_code"] == 0
        assert "hello" in r["output"]

    def test_no_match_exit_1(self, tmp_path):
        from agent.services.readonly_handlers import GrepSearchHandler
        f = tmp_path / "code.py"
        f.write_text("nothing here")
        r = GrepSearchHandler().execute(task={"pattern": "xyz_not_here", "path": str(tmp_path)})
        assert r["exit_code"] != 0

    def test_missing_pattern(self, tmp_path):
        from agent.services.readonly_handlers import GrepSearchHandler
        r = GrepSearchHandler().execute(task={"path": str(tmp_path)})
        assert r["exit_code"] != 0


class TestJsonValidateHandler:
    def test_valid_json_string(self):
        from agent.services.readonly_handlers import JsonValidateHandler
        r = JsonValidateHandler().execute(task={"content": '{"key": "value"}'})
        assert r["exit_code"] == 0
        assert r.get("valid") is True

    def test_invalid_json(self):
        from agent.services.readonly_handlers import JsonValidateHandler
        r = JsonValidateHandler().execute(task={"content": '{bad json}'})
        assert r["exit_code"] != 0
        assert r.get("valid") is False

    def test_valid_json_file(self, tmp_path):
        from agent.services.readonly_handlers import JsonValidateHandler
        f = tmp_path / "data.json"
        f.write_text('{"ok": true}')
        r = JsonValidateHandler().execute(task={"path": str(f)})
        assert r["exit_code"] == 0

    def test_no_input(self):
        from agent.services.readonly_handlers import JsonValidateHandler
        r = JsonValidateHandler().execute(task={})
        assert r["exit_code"] != 0


class TestGitHandlers:
    def test_git_status_propose(self):
        from agent.services.readonly_handlers import GitStatusHandler
        p = GitStatusHandler().propose(task={"cwd": "/tmp"})
        assert p["tool_calls"][0]["name"] == "git_status"
        assert p["safety_flags"]["read_only"] is True

    def test_git_diff_propose(self):
        from agent.services.readonly_handlers import GitDiffHandler
        p = GitDiffHandler().propose(task={"cwd": "/tmp", "ref": "HEAD"})
        assert p["tool_calls"][0]["name"] == "git_diff"


# ── TaskHandlerRegistry: resolve_by_handler_id (te-005) ──────────────────────

class TestHandlerRegistryExtension:
    def test_resolve_by_handler_id(self):
        from agent.services.task_handler_registry import TaskHandlerRegistry
        from agent.services.readonly_handlers import ReadFileHandler

        reg = TaskHandlerRegistry()
        handler = ReadFileHandler()
        reg.register("read_file", handler, capabilities=["read_only"])
        assert reg.resolve_by_handler_id("read_file") is handler

    def test_resolve_by_handler_id_unknown(self):
        from agent.services.task_handler_registry import TaskHandlerRegistry
        reg = TaskHandlerRegistry()
        assert reg.resolve_by_handler_id("nonexistent") is None


# ── TaskRoutingContract TE fields (te-001) ────────────────────────────────────

class TestTaskRoutingContractTeFields:
    def test_fields_present(self):
        from agent.models import TaskRoutingContract
        c = TaskRoutingContract(
            task_class="deterministic",
            intent="list_files",
            llm_required=False,
            deterministic_handler_id="list_files",
        )
        assert c.task_class == "deterministic"
        assert c.intent == "list_files"
        assert c.llm_required is False
        assert c.deterministic_handler_id == "list_files"

    def test_fields_default_none(self):
        from agent.models import TaskRoutingContract
        c = TaskRoutingContract()
        assert c.task_class is None
        assert c.intent is None
        assert c.llm_required is None
        assert c.deterministic_handler_id is None


# ── Config te-004 ─────────────────────────────────────────────────────────────

class TestTaskEngineConfig:
    def test_defaults(self):
        from agent.config import Settings
        s = Settings()
        assert s.task_engine_enabled is True
        assert s.task_engine_deterministic_bypass_enabled is True
        assert s.task_engine_strict_unknown_tool_policy is False


# ── RunTestsHandler (te-007) ──────────────────────────────────────────────────

class TestRunTestsHandler:
    def test_propose_default_profile(self):
        from agent.services.run_tests_handler import RunTestsHandler
        p = RunTestsHandler().propose(task={})
        assert p["command"] == "pytest"
        assert p["safety_flags"]["read_only"] is False

    def test_propose_node_profile(self):
        from agent.services.run_tests_handler import RunTestsHandler
        p = RunTestsHandler().propose(task={"test_runner_profile": "node"})
        assert "npm test" in p["command"]

    def test_propose_uses_canvas_det_command_metadata(self):
        from agent.services.run_tests_handler import RunTestsHandler
        p = RunTestsHandler().propose(task={"metadata": {"det_command": "pytest tests/test_task_engine.py"}})
        assert p["command"] == "pytest tests/test_task_engine.py"
        assert p["tool_calls"][0]["name"] == "run_tests"

    def test_blocked_unknown_command(self):
        from agent.services.run_tests_handler import RunTestsHandler
        r = RunTestsHandler().execute(task={"command": "rm -rf /", "test_runner_profile": "default"})
        assert r["exit_code"] == 1
        assert r.get("blocked") is True
        assert r.get("policy_violation") == "run_tests_profile_gate"

    def test_allowed_pytest_command(self):
        from agent.services.run_tests_handler import RunTestsHandler
        r = RunTestsHandler().execute(task={"command": "pytest --version"})
        assert r["exit_code"] == 0 or "pytest" in r.get("output", "")

    def test_profile_gate_node_rejects_pytest(self):
        from agent.services.run_tests_handler import RunTestsHandler
        r = RunTestsHandler().execute(task={"command": "pytest tests/", "test_runner_profile": "node"})
        assert r.get("blocked") is True

    def test_profile_gate_rust(self):
        from agent.services.run_tests_handler import RunTestsHandler, _command_allowed, _resolve_profile
        profile = _resolve_profile("rust")
        assert _command_allowed("cargo test --release", profile)
        assert not _command_allowed("pytest", profile)

    def test_unknown_runner_fallback(self):
        from agent.services.run_tests_handler import RunTestsHandler
        r = RunTestsHandler().execute(task={"command": "pytest nonexistent_test_xyz123.py"})
        assert r["exit_code"] != 0  # exits non-zero when no tests collected

    def test_create_app_registers_run_tests_handler(self):
        from agent.ai_agent import create_app
        from agent.services.task_handler_registry import get_task_handler_registry

        app = create_app(testing=True)
        with app.app_context():
            assert get_task_handler_registry().resolve("run_tests") is not None


# ── TaskEnginePipelineTrace (te-008) ──────────────────────────────────────────

class TestTaskEnginePipelineTrace:
    def test_stamp_adds_stages(self):
        from agent.services.task_engine_trace import stamp_te_pipeline
        from agent.services.task_intent_router import TaskIntentRouter
        from agent.services.task_class_resolver import TaskClassResolver

        ir = TaskIntentRouter().route({"task_kind": "list_files"})
        cr = TaskClassResolver().resolve({"task_kind": "list_files"})

        pipeline = stamp_te_pipeline(None, intent_result=ir, class_result=cr, handler_id="list_files", bypassed_llm=True)
        stages = pipeline["stages"]
        stage_names = [s["stage"] for s in stages]
        assert "task_intent_router" in stage_names
        assert "task_class_resolver" in stage_names
        assert "deterministic_handler_dispatch" in stage_names

    def test_stamp_preserves_existing(self):
        from agent.services.task_engine_trace import stamp_te_pipeline
        existing = {"stages": [{"stage": "existing_stage"}], "custom": 42}
        pipeline = stamp_te_pipeline(existing)
        assert pipeline["custom"] == 42
        assert pipeline["stages"][0]["stage"] == "existing_stage"

    def test_extract_summary(self):
        from agent.services.task_engine_trace import stamp_te_pipeline, extract_te_summary
        from agent.services.task_intent_router import TaskIntentRouter
        from agent.services.task_class_resolver import TaskClassResolver

        ir = TaskIntentRouter().route({"task_kind": "grep_search"})
        cr = TaskClassResolver().resolve({"task_kind": "grep_search"})
        pipeline = stamp_te_pipeline(None, intent_result=ir, class_result=cr, handler_id="grep_search", bypassed_llm=True)
        summary = extract_te_summary(pipeline)
        assert summary["intent"] == "grep_search"
        assert summary["task_class"] == "deterministic"
        assert summary["bypassed_llm"] is True
        assert summary["handler_id"] == "grep_search"

    def test_task_engine_active_flag(self):
        from agent.services.task_engine_trace import stamp_te_pipeline
        p = stamp_te_pipeline(None)
        assert p["task_engine_active"] is True


# ── TaskEnginePolicyGate (te-009 + te-010) ────────────────────────────────────

class TestTaskEnginePolicyGate:
    def test_disabled_passes_all_to_llm(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(enabled=False)
        d = gate.evaluate({"task_kind": "list_files"})
        assert d.allow
        assert not d.bypass_llm
        assert d.reason == "task_engine_disabled"

    def test_bypass_disabled_routes_to_llm(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(deterministic_bypass_enabled=False)
        d = gate.evaluate({"task_kind": "read_file"})
        assert d.allow
        assert not d.bypass_llm
        assert "bypass_disabled" in d.reason

    def test_deterministic_kind_bypasses_llm(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate()
        d = gate.evaluate({"task_kind": "git_status"})
        assert d.allow
        assert d.bypass_llm
        assert d.handler_id == "git_status"
        assert not d.llm_required

    def test_hybrid_bypasses_llm(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate()
        d = gate.evaluate({"task_kind": "run_tests"})
        assert d.bypass_llm
        assert d.task_class == "hybrid"

    def test_llm_required_task(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate()
        d = gate.evaluate({"task_kind": "code_review"})
        assert not d.bypass_llm
        assert d.llm_required

    def test_strict_unknown_tool_blocks(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(strict_unknown_tool_policy=True)
        d = gate.evaluate({"tool_calls": [{"name": "some_exotic_tool_xyz"}]})
        assert d.blocked
        assert not d.allow
        assert "some_exotic_tool_xyz" in d.unknown_tools
        assert "strict_unknown_tool_policy" in d.reason

    def test_strict_known_tools_pass(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(strict_unknown_tool_policy=True)
        d = gate.evaluate({"tool_calls": [{"name": "read_file"}]})
        assert not d.blocked
        assert d.allow

    def test_non_strict_unknown_tool_allowed(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(strict_unknown_tool_policy=False)
        d = gate.evaluate({"tool_calls": [{"name": "some_exotic_tool_xyz"}]})
        assert not d.blocked
        assert d.allow

    def test_capability_forced_llm_not_bypassed(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate()
        d = gate.evaluate({"task_kind": "list_files", "required_capabilities": ["write_file"]})
        assert not d.bypass_llm
        assert d.llm_required


# ── ToolScopeContract (te-011) ────────────────────────────────────────────────

class TestToolScopeContract:
    def test_open_policy_allows_all(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract.open()
        assert scope.is_allowed("read_file")
        assert scope.is_allowed("exotic_tool_xyz")

    def test_closed_policy_denies_unlisted(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract(allowed_tools=["read_file", "grep_search"], policy="closed")
        assert scope.is_allowed("read_file")
        assert not scope.is_allowed("list_files")

    def test_forbidden_overrides_allowed(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract(
            allowed_tools=["read_file", "grep_search"],
            forbidden_tools=["grep_search"],
            policy="closed",
        )
        assert not scope.is_allowed("grep_search")

    def test_from_task_dict(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract.from_task({"allowed_tools": ["read_file", "git_status"]})
        assert scope.policy == "closed"
        assert scope.is_allowed("read_file")
        assert not scope.is_allowed("grep_search")

    def test_from_task_empty_is_open(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract.from_task({})
        assert scope.policy == "open"

    def test_unknown_tools_in(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract.open()
        unknown = scope.unknown_tools_in([{"name": "some_exotic_xyz"}, {"name": "read_file"}])
        assert "some_exotic_xyz" in unknown
        assert "read_file" not in unknown

    def test_merge_two_closed(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        a = ToolScopeContract(allowed_tools=["read_file", "grep_search"], policy="closed")
        b = ToolScopeContract(allowed_tools=["grep_search", "git_status"], policy="closed")
        merged = a.merge(b)
        assert merged.policy == "closed"
        assert "grep_search" in merged.allowed_tools
        assert "read_file" not in merged.allowed_tools

    def test_as_dict(self):
        from agent.services.tool_scope_contract import ToolScopeContract
        scope = ToolScopeContract(allowed_tools=["read_file"], policy="closed")
        d = scope.as_dict()
        assert d["policy"] == "closed"
        assert "read_file" in d["allowed_tools"]


# ── TaskEngineStatusService (te-012 / te-013) ─────────────────────────────────

class TestTaskEngineStatusService:
    def test_initial_inactive(self):
        from agent.services.task_engine_status_service import TaskEngineStatusService
        svc = TaskEngineStatusService()
        s = svc.get()
        assert not s.active

    def test_update_from_gate_decision(self):
        from agent.services.task_engine_status_service import TaskEngineStatusService
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate

        svc = TaskEngineStatusService()
        gate = TaskEnginePolicyGate()
        decision = gate.evaluate({"task_kind": "list_files"})
        svc.update(decision, task_id="task-123")
        s = svc.get()
        assert s.active
        assert s.intent == "list_files"
        assert s.task_class == "deterministic"
        assert s.bypassed_llm is True
        assert s.task_id == "task-123"

    def test_clear_resets(self):
        from agent.services.task_engine_status_service import TaskEngineStatusService
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate

        svc = TaskEngineStatusService()
        gate = TaskEnginePolicyGate()
        svc.update(gate.evaluate({"task_kind": "git_status"}))
        svc.clear()
        assert not svc.get().active

    def test_as_dict(self):
        from agent.services.task_engine_status_service import TaskEngineStatusService
        svc = TaskEngineStatusService()
        d = svc.as_dict()
        assert "active" in d
        assert "intent" in d

    def test_singleton(self):
        from agent.services.task_engine_status_service import get_task_engine_status_service
        a = get_task_engine_status_service()
        b = get_task_engine_status_service()
        assert a is b


# ── /api/task-engine route (te-013) ──────────────────────────────────────────

class TestTaskEngineRoute:
    @pytest.fixture
    def client(self):
        from flask import Flask
        from agent.routes.task_engine import task_engine_bp
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(task_engine_bp)
        return app.test_client()

    def test_status_endpoint(self, client):
        resp = client.get("/api/task-engine/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "active" in data

    def test_classify_deterministic(self, client):
        resp = client.post("/api/task-engine/classify", json={"task_kind": "read_file"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["task_class"] == "deterministic"
        assert data["bypass_llm"] is True
        assert data["handler_id"] == "read_file"

    def test_classify_llm_required(self, client):
        resp = client.post("/api/task-engine/classify", json={"task_kind": "code_review"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["task_class"] == "llm_required"
        assert data["bypass_llm"] is False

    def test_classify_includes_tool_scope(self, client):
        resp = client.post("/api/task-engine/classify", json={
            "task_kind": "grep_search",
            "allowed_tools": ["grep_search"],
        })
        data = resp.get_json()
        assert "tool_scope" in data
        assert data["tool_scope"]["policy"] == "closed"


# ── Backwards compat (te-015) ─────────────────────────────────────────────────

class TestBackwardsCompat:
    def test_disabled_engine_passes_llm_required(self):
        """Old flows are unaffected when task_engine_enabled=False."""
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        gate = TaskEnginePolicyGate(enabled=False)
        for task in [
            {"task_kind": "list_files"},
            {"task_kind": "code_review"},
            {"command": "grep -rn TODO ."},
            {},
        ]:
            d = gate.evaluate(task)
            assert d.allow, f"should allow: {task}"
            assert not d.bypass_llm, f"should not bypass LLM: {task}"
            assert d.llm_required, f"should be llm_required: {task}"

    def test_empty_task_does_not_crash(self):
        from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
        from agent.services.task_class_resolver import TaskClassResolver
        from agent.services.task_intent_router import TaskIntentRouter

        gate = TaskEnginePolicyGate()
        d = gate.evaluate({})
        assert d.allow  # graceful default

        cr = TaskClassResolver().resolve({})
        assert cr.task_class == "llm_required"

        ir = TaskIntentRouter().route({})
        assert ir.llm_required

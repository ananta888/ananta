"""FA-T018: E2E test — LLM-assisted project evolution via normalization.

Proves that project evolution (adding features, patching files) flows through
the correct normalization paths, and that all executable paths require
artifact collection and verification.
"""
from __future__ import annotations

import hashlib
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch

from worker.core.propose import ExecutableProposal, STATUS_EXECUTABLE, STATUS_ADVISORY
from worker.core.propose import FileProposalArtifact, PatchProposalArtifact
from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose_orchestrator import ProposeStrategyOrchestrator
from worker.core.deterministic_handler_strategy import DeterministicHandlerStrategy
from worker.core.template_propose_handler import TemplateProposeHandler
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.task_handler_registry import TaskHandlerRegistry
from agent.services.propose_policy import ProposePolicy
from agent.services.planning_utils import parse_followup_analysis
from worker.core.artifact_manifest import build_artifact_manifest, write_manifest
from agent.services.worker_output_collector_service import get_worker_output_collector_service
from agent.services.task_completion_policy_service import get_task_completion_policy_service


@pytest.fixture
def normalizer():
    return LLMResponseNormalizer()


@pytest.fixture
def evolution_context():
    return ProposeContext(
        goal_id="goal-evo-001",
        task_id="task-evo-001",
        task={"task_kind": "coding", "title": "Add auth to Fibonacci API", "goal_id": "goal-evo-001"},
        base_prompt="Add JWT authentication to the existing Fibonacci API",
    )


class TestProjectEvolutionNormalizationPaths:
    """Project evolution (add features, patch) flows through correct paths."""

    def test_patch_evolution_becomes_patch_proposal(self, normalizer, evolution_context):
        """Adding a feature via unified diff → PatchProposalArtifact (advisory, needs approval)."""
        raw = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,8 @@
 from flask import Flask
+from flask_jwt_extended import JWTManager
 app = Flask(__name__)
+app.config['JWT_SECRET_KEY'] = 'secret'
+jwt = JWTManager(app)
"""
        result = normalizer.normalize(raw, evolution_context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, PatchProposalArtifact)

    def test_new_file_evolution_becomes_file_proposal(self, normalizer, evolution_context):
        """Adding a new file → FileProposalArtifact (advisory, needs apply)."""
        raw = "```auth.py\nfrom flask_jwt_extended import create_access_token\n```"
        result = normalizer.normalize(raw, evolution_context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, FileProposalArtifact)
        assert result.proposal.files[0]["path"] == "auth.py"

    def test_tool_call_evolution_is_executable(self, normalizer, evolution_context):
        """Tool call for evolution is executable."""
        raw = '{"tool_calls": [{"name": "write_file", "args": {"path": "auth.py", "content": "# auth"}}]}'
        result = normalizer.normalize(raw, evolution_context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)


class TestEvolutionExecutableRequiresArtifacts:
    """All executable paths after evolution must produce collectible artifacts."""

    def test_executable_command_produces_collectible_artifacts(self, tmp_path: Path):
        """After execute, worker writes files and manifest is collected."""
        ws = tmp_path / "fibonacci-api"
        ws.mkdir()
        # Simulate worker output: adds auth.py
        (ws / "auth.py").write_text("# auth module\n")
        (ws / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        (ws / "requirements.txt").write_text("flask>=2.0\nflask-jwt-extended\n")

        artifacts = []
        for f in ["app.py", "requirements.txt", "auth.py"]:
            content = (ws / f).read_text()
            artifacts.append({
                "artifact_id": f"art-{uuid.uuid4().hex[:8]}",
                "kind": "generated_file",
                "relative_path": f,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "size_bytes": len(content.encode()),
                "classification": "internal",
                "operation": "created",
                "required": True,
                "verification_status": "pending",
                "metadata": {},
            })

        manifest = build_artifact_manifest(
            goal_id="goal-evo-001",
            task_id="task-evo-001",
            execution_id="exec-evo-001",
            trace_id="tr-evo-001",
            workspace_root=ws,
            worker_id="test-worker",
            artifacts=artifacts,
        )
        manifest_dir = ws / ".ananta" / "handoff" / "exec-evo-001"
        manifest_dir.mkdir(parents=True)
        write_manifest(manifest, manifest_dir / "artifact_manifest.v1.json")

        collector = get_worker_output_collector_service()
        collection = collector.collect(
            task_id="task-evo-001",
            goal_id="goal-evo-001",
            execution_id="exec-evo-001",
            trace_id="tr-evo-001",
            workspace_root=ws,
            manifest_relative_path=".ananta/handoff/exec-evo-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=False,
        )
        assert collection["manifest_valid"]
        artifact_paths = {a["relative_path"] for a in collection["artifacts"]}
        assert "auth.py" in artifact_paths

    def test_completion_uses_artifacts_not_model_text(self, tmp_path: Path):
        """Even when model says 'done', completion is decided by manifest."""
        ws = tmp_path / "fibonacci-api"
        ws.mkdir()
        for f, content in [("app.py", "# app"), ("auth.py", "# auth")]:
            (ws / f).write_text(content)

        artifacts = [
            {
                "artifact_id": f"art-{i}",
                "kind": "generated_file",
                "relative_path": f,
                "content_hash": hashlib.sha256(c.encode()).hexdigest(),
                "size_bytes": len(c.encode()),
                "classification": "internal",
                "operation": "created",
                "required": True,
                "verification_status": "pending",
                "metadata": {},
            }
            for i, (f, c) in enumerate([("app.py", "# app"), ("auth.py", "# auth")])
        ]
        manifest = build_artifact_manifest(
            goal_id="g", task_id="t", execution_id="e", trace_id="tr",
            workspace_root=ws, worker_id="w", artifacts=artifacts,
        )
        manifest_dir = ws / ".ananta" / "handoff" / "e"
        manifest_dir.mkdir(parents=True)
        write_manifest(manifest, manifest_dir / "artifact_manifest.v1.json")

        # Model claims task is done but with garbled text
        advisory = parse_followup_analysis("Evolution complete! Auth added. Please check the files.")
        assert advisory["advisory"] is True

        collector = get_worker_output_collector_service()
        collection = collector.collect(
            task_id="t", goal_id="g", execution_id="e", trace_id="tr",
            workspace_root=ws,
            manifest_relative_path=".ananta/handoff/e/artifact_manifest.v1.json",
            allow_synthesized_fallback=False,
        )

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="t",
            collection_result=collection,
            advisory_parse_result=advisory,
            exit_code=0,
            retry_count=0,
            expected_paths=["app.py", "auth.py"],
        )
        assert decision.decision in ("completed", "needs_review"), f"Unexpected: {decision.decision}"


class TestEvolutionOrchestratorFallback:
    """Orchestrator fallback to needs_review when no strategy handles evolution."""

    def test_unsupported_evolution_kind_becomes_needs_review(self):
        empty_registry = TaskHandlerRegistry()
        policy = ProposePolicy(
            strategy_order=["deterministic_handler"],
            on_all_strategies_declined="needs_review",
        )
        strategies = {"deterministic_handler": DeterministicHandlerStrategy()}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id="g",
            task_id="t",
            task={"task_kind": "unsupported_evolution_task"},
            base_prompt="add auth",
        )
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=empty_registry):
            result = orch.run(context)

        assert result.status == "needs_review"
        assert not result.is_executable

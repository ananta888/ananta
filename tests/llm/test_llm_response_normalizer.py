"""Tests for LLMResponseNormalizer — FA-T010."""

import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    PatchProposalArtifact,
    FileProposalArtifact,
    PlannerProposalArtifact,
    STATUS_EXECUTABLE,
    STATUS_ADVISORY,
)


class TestLLMResponseNormalizer:
    @pytest.fixture
    def context(self):
        return Mock(spec=ProposeContext, goal_id="test-goal", task_id="test-task")

    @pytest.fixture
    def normalizer(self):
        return LLMResponseNormalizer()

    def test_tool_calls(self, normalizer, context):
        raw = r'{"tool_calls": [{"name": "write_file", "args": {"path": "main.py", "content": "def fib():"}}]}'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)
        assert len(result.proposal.tool_calls) == 1
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        assert "openai_tool_calls" in result.metadata["source_format"]

    def test_fenced_json_tool_calls(self, normalizer, context):
        raw = """```json
{"tool_calls": [{"name": "git", "args": {}}]}
```"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)
        assert result.metadata["source_format"] == "fenced_json"

    def test_fenced_json_command(self, normalizer, context):
        raw = """```json
{"command": "mkdir src"}
```"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "mkdir src"

    def test_fenced_shell_advisory_by_default(self, normalizer, context):
        raw = """```bash
pip install fastapi uvicorn
```"""
        result = normalizer.normalize(raw, context)
        # shell blocks are advisory when allow_shell_execution=False (default)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None

    def test_fenced_shell_executable_when_allowed(self, normalizer, context):
        raw = """```bash
pip install fastapi uvicorn
```"""
        result = normalizer.normalize(raw, context, allow_shell_execution=True)
        assert result.status == STATUS_EXECUTABLE
        assert "pip install fastapi uvicorn" in result.proposal.command

    def test_unified_diff(self, normalizer, context):
        raw = """--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Old
+New Fibonacci API
"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, PatchProposalArtifact)
        assert "unified_diff" in result.metadata["source_format"]
        assert len(result.proposal.patches) == 1

    def test_file_block(self, normalizer, context):
        raw = """```main.py
from fastapi import FastAPI
app = FastAPI()
```
"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, FileProposalArtifact)
        assert len(result.proposal.files) == 1
        assert result.proposal.files[0]["path"] == "main.py"
        assert "file_blocks" in result.metadata["source_format"]

    def test_planner_text(self, normalizer, context):
        raw = "Sub-tasks: 1. Create app.py 2. Add tests"
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, PlannerProposalArtifact)
        assert len(result.proposal.sub_tasks) == 1
        assert "planner_text" in result.metadata["source_format"]

    def test_free_text_advisory_no_proposal(self, normalizer, context):
        raw = "Propose a simple Fibonacci API with FastAPI."
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None
        assert "free_text_normalized_to_advisory" == result.reason

    def test_invalid_json_fallback(self, normalizer, context):
        raw = "```json\n{ invalid json\n```"
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None  # fallback to prose advisory

    def test_long_text_truncated(self, normalizer, context):
        raw = "A" * 6000
        result = normalizer.normalize(raw, context)
        assert len(result.advisory_text) <= LLMResponseNormalizer.MAX_TEXT_LENGTH

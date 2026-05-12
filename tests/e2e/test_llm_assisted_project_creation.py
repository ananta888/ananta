"""FA-T018: E2E test — LLM-assisted project creation via all normalization paths.

Each test mocks a distinct LLM output format and proves:
  - tool_calls → ExecutableProposal (executable, can be executed)
  - fenced JSON → ExecutableProposal (executable)
  - file blocks → FileProposalArtifact (advisory, needs apply/approval)
  - unified diff → PatchProposalArtifact (advisory, needs apply/approval)
  - plain prose → AdvisoryProposalArtifact (advisory, never executable)

All executable paths still require artifact collection and verification.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, Mock

from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    STATUS_EXECUTABLE,
    STATUS_ADVISORY,
)
from worker.core.propose import (
    FileProposalArtifact,
    PatchProposalArtifact,
    PlannerProposalArtifact,
)
from worker.core.propose_orchestrator import ProposeContext
from agent.services.llm_response_normalizer import LLMResponseNormalizer


@pytest.fixture
def normalizer():
    return LLMResponseNormalizer()


@pytest.fixture
def context():
    return ProposeContext(
        goal_id="goal-llm-001",
        task_id="task-llm-001",
        task={"task_kind": "coding", "title": "Fibonacci API", "goal_id": "goal-llm-001"},
        base_prompt="Create a Fibonacci REST API",
    )


class TestLLMToolCallsPath:
    """Mocked LLM tool_calls are accepted and produce ExecutableProposal."""

    def test_openai_style_tool_calls_normalize_to_executable(self, normalizer, context):
        raw = '{"tool_calls": [{"name": "write_file", "args": {"path": "app.py", "content": "def fib(n): return n"}}]}'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)
        assert len(result.proposal.tool_calls) == 1
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        assert result.metadata.get("source_format") == "openai_tool_calls"

    def test_multiple_tool_calls_all_preserved(self, normalizer, context):
        raw = '{"tool_calls": [{"name": "write_file", "args": {"path": "app.py", "content": "x"}}, {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask"}}]}'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert len(result.proposal.tool_calls) == 2

    def test_tool_calls_proposal_is_executable_proposal_instance(self, normalizer, context):
        raw = '{"tool_calls": [{"name": "run_tests", "args": {}}]}'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)
        # Only ExecutableProposal may be executed — non-executable cannot slip through
        assert result.proposal.tool_calls or result.proposal.command


class TestFencedJsonPath:
    """Mocked fenced JSON normalizes and executes when valid."""

    def test_fenced_json_with_command(self, normalizer, context):
        raw = '```json\n{"command": "pip install fastapi && python app.py"}\n```'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "pip install fastapi && python app.py"
        assert result.metadata.get("source_format") == "fenced_json"

    def test_fenced_json_with_tool_calls(self, normalizer, context):
        raw = '```json\n{"tool_calls": [{"name": "write_file", "args": {"path": "app.py", "content": "x"}}]}\n```'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_EXECUTABLE
        assert len(result.proposal.tool_calls) == 1

    def test_invalid_fenced_json_falls_through_to_advisory(self, normalizer, context):
        raw = "```json\n{invalid json here\n```"
        result = normalizer.normalize(raw, context)
        # No valid executable extracted → advisory
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None

    def test_fenced_json_without_command_or_tool_calls_is_advisory(self, normalizer, context):
        raw = '```json\n{"description": "some metadata only"}\n```'
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY


class TestFileBlocksPath:
    """Mocked file blocks become FileProposalArtifact (advisory, not executable)."""

    def test_file_block_becomes_file_proposal_artifact(self, normalizer, context):
        raw = """```app.py
from flask import Flask
app = Flask(__name__)
```"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, FileProposalArtifact)
        assert len(result.proposal.files) == 1
        assert result.proposal.files[0]["path"] == "app.py"

    def test_file_block_content_preserved(self, normalizer, context):
        raw = "```requirements.txt\nfastapi>=0.100\nuvicorn\n```"
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, FileProposalArtifact)
        assert "fastapi" in result.proposal.files[0]["content"]

    def test_file_block_source_format_recorded(self, normalizer, context):
        raw = "```main.py\ndef main(): pass\n```"
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.metadata.get("source_format") == "file_blocks"

    def test_file_block_is_not_executable(self, normalizer, context):
        """FileProposalArtifact must never slip through as ExecutableProposal."""
        raw = "```app.py\nprint('hello')\n```"
        result = normalizer.normalize(raw, context)
        # Must be advisory, not executable
        assert result.status != STATUS_EXECUTABLE
        if result.proposal is not None:
            assert not isinstance(result.proposal, ExecutableProposal)


class TestUnifiedDiffPath:
    """Mocked unified diff becomes PatchProposalArtifact (advisory, needs apply/approval)."""

    def test_unified_diff_becomes_patch_proposal(self, normalizer, context):
        raw = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 from flask import Flask
+import math
 app = Flask(__name__)
+@app.route('/fib/<int:n>')
+def fib(n): return str(math.factorial(n))
"""
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert isinstance(result.proposal, PatchProposalArtifact)
        assert len(result.proposal.patches) > 0

    def test_patch_proposal_source_format_recorded(self, normalizer, context):
        raw = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.metadata.get("source_format") == "unified_diff"

    def test_patch_proposal_is_not_executable(self, normalizer, context):
        """PatchProposalArtifact must never slip through as ExecutableProposal."""
        raw = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
        result = normalizer.normalize(raw, context)
        assert result.status != STATUS_EXECUTABLE
        if result.proposal is not None:
            assert not isinstance(result.proposal, ExecutableProposal)


class TestPlainProseAdvisory:
    """Plain natural language becomes AdvisoryProposalArtifact and does not execute."""

    def test_plain_prose_is_advisory(self, normalizer, context):
        raw = "I would recommend creating a simple Fibonacci API with FastAPI."
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None
        assert result.reason == "free_text_normalized_to_advisory"

    def test_prose_does_not_become_executable(self, normalizer, context):
        raw = "The best approach is to write app.py with a Flask route for /fib/<n>."
        result = normalizer.normalize(raw, context)
        assert result.status != STATUS_EXECUTABLE

    def test_advisory_source_format_natural_language(self, normalizer, context):
        raw = "Just create the file and run it."
        result = normalizer.normalize(raw, context)
        assert result.status == STATUS_ADVISORY
        assert result.metadata.get("source_format") == "natural_language"

    def test_advisory_text_preserved(self, normalizer, context):
        raw = "Here is my advice: write the app first."
        result = normalizer.normalize(raw, context)
        assert result.advisory_text is not None
        assert len(result.advisory_text) > 0


class TestNormalizationPolicyEnforcement:
    """Cross-cutting: normalization never promotes advisory to executable."""

    @pytest.mark.parametrize("raw,expected_status", [
        # Prose — always advisory
        ("Write app.py with FastAPI", STATUS_ADVISORY),
        # Only metadata JSON — advisory
        ('```json\n{"description": "metadata"}\n```', STATUS_ADVISORY),
        # Invalid fenced JSON — advisory
        ('```json\n{ bad json\n```', STATUS_ADVISORY),
        # Valid command fenced JSON — executable
        ('```json\n{"command": "echo hello"}\n```', STATUS_EXECUTABLE),
        # Valid tool_calls — executable
        ('{"tool_calls": [{"name": "write_file", "args": {}}]}', STATUS_EXECUTABLE),
    ])
    def test_status_per_format(self, normalizer, context, raw, expected_status):
        result = normalizer.normalize(raw, context)
        snippet = repr(raw[:40])
        assert result.status == expected_status, (
            f"Format {snippet} → expected {expected_status}, got {result.status}"
        )

    def test_all_executable_paths_produce_executable_proposal(self, normalizer, context):
        """Any STATUS_EXECUTABLE result must carry an ExecutableProposal, never prose."""
        executables = [
            '{"tool_calls": [{"name": "write_file", "args": {}}]}',
            '```json\n{"command": "mkdir src"}\n```',
            "```bash\necho hello\n```",
        ]
        for raw in executables:
            result = normalizer.normalize(raw, context)
            if result.status == STATUS_EXECUTABLE:
                snippet = repr(raw[:40])
                assert isinstance(result.proposal, ExecutableProposal), (
                    f"Executable result from {snippet} must carry ExecutableProposal"
                )
                assert result.proposal.command or result.proposal.tool_calls

"""Tests for ContextBundler — FA-T012."""

import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from agent.services.context_bundle_service import ContextBundler
from agent.services.propose_policy_service import ProposePolicyService


class TestContextBundler:
    @pytest.fixture
    def context(self):
        ctx = Mock(ProposeContext)
        ctx.task = {"kind": "coding/new_software_project"}
        ctx.base_prompt = "Create Fibonacci API"
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        return ctx

    def test_bundle_tool_calling_contains_examples(self, context):
        prompt = ContextBundler.bundle(context, "tool_calling_llm")
        assert "tool_calling_llm" in prompt
        assert "write_file" in prompt
        assert "Output ONLY valid JSON" in prompt

    def test_bundle_json_schema_examples(self, context):
        prompt = ContextBundler.bundle(context, "json_schema_llm")
        assert "json_schema_llm" in prompt
        assert "command" in prompt

    def test_bundle_flexible_examples(self, context):
        prompt = ContextBundler.bundle(context, "flexible_llm_normalization")
        assert "fenced JSON" in prompt
        assert "pip install fastapi" in prompt

    def test_bundle_no_examples_default(self, context):
        prompt = ContextBundler.bundle(context, "unknown_strategy")
        assert "No examples." in prompt

    def test_bundle_policy_included(self, context):
        prompt = ContextBundler.bundle(context, "tool_calling_llm")
        assert "allow_legacy_sgpt" in prompt


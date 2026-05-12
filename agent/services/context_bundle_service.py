"""ContextBundler — FA-T012 rich LLM prompts for strategies."""

from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext
from agent.services.propose_policy_service import ProposePolicyService


class ContextBundler:
    """Bundles rich context for LLM strategies."""

    @staticmethod
    def bundle(context: ProposeContext, strategy_id: str) -> str:
        policy = dict(strategy_order=["deterministic_handler", "worker_strategy"], allow_legacy_sgpt=False)
        tools = context.tool_definitions_resolver() or []
        schema_str = """
ExecutableProposal schema:
{
  "proposal_id": "string",
  "command": "string or null",
  "tool_calls": array of {"name": "string", "args": object}
}
"""
        examples = ContextBundler._get_examples(strategy_id)
        goal_prompt = context.base_prompt

        prompt = f"""Task: {context.task}

Policy: {policy}

Schema: {schema_str}

Tools: {tools}

Examples:
{examples}

Propose for goal: {goal_prompt}

Output ONLY valid JSON matching schema."""

        return prompt

    @staticmethod
    def _get_examples(strategy_id: str) -> str:
        examples = {
            "tool_calling_llm": """
Example valid:
{{"tool_calls": [{{"name": "write_file", "args": {{"path": "main.py", "content": "code"}}}]}}
""",
            "json_schema_llm": """
Example valid:
{{"command": "mkdir src"}}
""",
            "flexible_llm_normalization": """
Example fenced JSON:
```json
{{"tool_calls": [...]}}
```
Example shell:
```bash
pip install fastapi
```
""",
        }
        return examples.get(strategy_id, "No examples.")


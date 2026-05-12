"""ContextBundler — FA-T012 rich LLM prompts for strategies."""

from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext
from agent.services.propose_policy_service import ProposePolicyService


_CONTEXT_BUNDLE_DEFAULTS: dict = {
    "mode": "standard",
    "window_profile": "standard_32k",
    "compact_max_chunks": 5,
    "standard_max_chunks": 12,
    "compact_budget_tokens": 4096,
    "standard_budget_tokens": 12288,
    "full_budget_tokens": 32768,
    "include_context_text": True,
}


def normalize_context_bundle_policy_config(cfg: dict | None) -> dict:
    """Merge caller-provided overrides with defaults and return a clean policy dict."""
    raw = dict(cfg) if isinstance(cfg, dict) else {}
    result = dict(_CONTEXT_BUNDLE_DEFAULTS)
    for key in _CONTEXT_BUNDLE_DEFAULTS:
        if key in raw and raw[key] is not None:
            result[key] = raw[key]
    return result


def resolve_context_bundle_policy(cfg: dict | None) -> dict:
    """Resolve a normalized context bundle policy to a runtime-ready effective config."""
    policy = normalize_context_bundle_policy_config(cfg)
    mode = str(policy.get("mode") or "standard")
    if mode == "compact":
        max_chunks = policy["compact_max_chunks"]
        total_budget_tokens = policy["compact_budget_tokens"]
    elif mode == "full":
        max_chunks = None
        total_budget_tokens = policy["full_budget_tokens"]
    else:
        max_chunks = policy["standard_max_chunks"]
        total_budget_tokens = policy["standard_budget_tokens"]
    return {
        "mode": mode,
        "window_profile": policy["window_profile"],
        "max_chunks": max_chunks,
        "total_budget_tokens": total_budget_tokens,
        "include_context_text": bool(policy.get("include_context_text", True)),
        "budget_tokens_by_mode": {
            "compact": policy["compact_budget_tokens"],
            "standard": policy["standard_budget_tokens"],
            "full": policy["full_budget_tokens"],
        },
    }


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
tool_calling_llm examples:
Example valid:
{{"tool_calls": [{{"name": "write_file", "args": {{"path": "main.py", "content": "code"}}}]}}
""",
            "json_schema_llm": """
json_schema_llm examples:
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

    @staticmethod
    def resolve_context_bundle_policy(cfg: dict | None) -> dict:
        return resolve_context_bundle_policy(cfg)


_context_bundle_service = ContextBundler()


def get_context_bundle_service() -> ContextBundler:
    return _context_bundle_service

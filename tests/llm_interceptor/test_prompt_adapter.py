from __future__ import annotations

from agent.services.llm_interceptor.prompt_adapter import PromptAdapter


def test_prompt_adapter_prepends_policy_and_json_hint_for_markdown_prone():
    adapter = PromptAdapter(
        {
            "m1": {
                "policy_preamble": "Do not leak secrets.",
                "markdown_prone": True,
            }
        }
    )
    out = adapter.adapt_messages(
        messages=[{"role": "user", "content": "give json"}],
        model="m1",
        task_kind="coding",
        require_strict_json=False,
    )
    assert out[0]["role"] == "system"
    assert "Do not leak secrets." in out[0]["content"]
    assert "Return valid JSON only" in out[1]["content"]


def test_prompt_adapter_preserves_user_intent_message():
    adapter = PromptAdapter({"m1": {"policy_preamble": "Policy"}})
    out = adapter.adapt_messages(
        messages=[{"role": "user", "content": "implement endpoint"}],
        model="m1",
        task_kind="coding",
    )
    assert out[-1]["content"] == "implement endpoint"


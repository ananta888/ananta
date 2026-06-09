"""Tests for the live chain runner (LCG-007 v0.8 closure).

The runner abstraction lets the LangChain adapter actually call an
LLM. The contract:

- SimplexRunner: always works, no framework. Calls generate_text().
- LangChainRunnableRunner: only used when langchain is importable.
  Wraps the same generate_text() call in a RunnableLambda so the
  chain shape is preserved.
- The adapter selects the runner at call time, not at import.
- generate_text is monkey-patched in tests so we never hit a real
  LLM endpoint; the test asserts the runner calls it with the
  right prompt and uses the right model.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from worker.adapters.chain_runners import (
    LangChainRunnableRunner,
    SimplexRunner,
    _parse_model_ref,
)
from worker.adapters.workflow_adapter_base import WorkerError
from worker.adapters.workflow_budget import WorkflowBudgetGuard


# ── _parse_model_ref helper ────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("local.default", "default"),
    ("ollama.llama3.1", "llama3.1"),
    ("", None),
    (None, None),
    ("single", "single"),
])
def test_parse_model_ref(raw, expected):
    assert _parse_model_ref(raw) == expected


# ── SimplexRunner ──────────────────────────────────────────────────────


def test_simplex_runner_returns_string_response():
    """generate_text that returns a string flows through unchanged."""
    runner = SimplexRunner()
    with patch("agent.llm_integration.generate_text", return_value="hello"):
        out = runner.run(
            prompt="p", payload={}, budget=_budget(), model_provider_ref="local.x",
        )
    assert out == "hello"


def test_simplex_runner_extracts_text_from_dict():
    """generate_text that returns a dict with 'text' is normalised to str."""
    runner = SimplexRunner()
    with patch("agent.llm_integration.generate_text",
               return_value={"text": "from-dict"}):
        out = runner.run(
            prompt="p", payload={}, budget=_budget(), model_provider_ref="local.x",
        )
    assert out == "from-dict"


def test_simplex_runner_strips_local_prefix_from_model_ref():
    """`local.default` -> `default` so generate_text can resolve it."""
    runner = SimplexRunner()
    with patch("agent.llm_integration.generate_text", return_value="ok") as m:
        runner.run(
            prompt="p", payload={}, budget=_budget(),
            model_provider_ref="local.llama3.1",
        )
    # The third positional/keyword arg of generate_text is model=None;
    # we passed prompt, model, timeout. Verify model='llama3.1' was used.
    _args, kwargs = m.call_args
    assert kwargs.get("model") == "llama3.1" or (
        len(_args) >= 2 and _args[1] == "llama3.1"
    )


def test_simplex_runner_raises_worker_error_on_failure():
    runner = SimplexRunner()
    with patch("agent.llm_integration.generate_text",
               side_effect=RuntimeError("llm offline")):
        with pytest.raises(WorkerError) as exc:
            runner.run(
                prompt="p", payload={}, budget=_budget(),
                model_provider_ref="local.x",
            )
    assert exc.value.reason_code == "llm_call_failed"
    assert "llm offline" in str(exc.value)


def test_simplex_runner_enforces_budget():
    """The runner records a step; exceeding the budget raises WorkerError."""
    runner = SimplexRunner()
    budget = WorkflowBudgetGuard(max_steps=1, timeout_seconds=60)
    budget.record_step("first")  # uses the only allowed step
    with patch("agent.llm_integration.generate_text", return_value="ok"):
        with pytest.raises(WorkerError) as exc:
            runner.run(
                prompt="p", payload={}, budget=budget,
                model_provider_ref="local.x",
            )
    assert exc.value.reason_code == "budget_steps_exceeded"


# ── LangChainRunnableRunner ────────────────────────────────────────────

# These tests require langchain-core to be importable. The v0.7
# default install does not include it; pip install ananta[langchain]
# pulls it in. The skip marker is applied per-test so the Simplex
# tests can still run when the framework is missing.


def test_langchain_runnable_runner_calls_invoke(monkeypatch):
    """RunnableLambda.invoke is called with the prompt+payload dict."""
    pytest.importorskip("langchain_core.runnables",
                        reason="langchain-core not installed (pip install ananta[langchain])")
    import langchain_core.runnables as rc  # type: ignore

    captured: dict[str, Any] = {}

    class FakeRunnable:
        def invoke(self, x: dict[str, Any]) -> str:
            captured.update(x)
            return "from-runnable"

    def fake_runnable_lambda(fn):
        return FakeRunnable()

    monkeypatch.setattr(rc, "RunnableLambda", fake_runnable_lambda)

    runner = LangChainRunnableRunner()
    with patch("agent.llm_integration.generate_text", return_value="x"):
        out = runner.run(
            prompt="my-prompt", payload={"k": "v"},
            budget=_budget(), model_provider_ref="local.m",
        )
    assert out == "from-runnable"
    assert captured["prompt"] == "my-prompt"
    assert captured["payload"] == {"k": "v"}


def test_langchain_runnable_runner_wraps_generate_text(monkeypatch):
    """The inner generate_text call still goes through with the model."""
    pytest.importorskip("langchain_core.runnables",
                        reason="langchain-core not installed (pip install ananta[langchain])")
    import langchain_core.runnables as rc  # type: ignore

    def fake_runnable_lambda(fn):
        return type("R", (), {"invoke": staticmethod(lambda x: fn(x))})()

    monkeypatch.setattr(rc, "RunnableLambda", fake_runnable_lambda)

    runner = LangChainRunnableRunner()
    with patch("agent.llm_integration.generate_text", return_value="ok") as m:
        out = runner.run(
            prompt="p", payload={}, budget=_budget(),
            model_provider_ref="ollama.llama3.1",
        )
    assert out == "ok"
    _args, kwargs = m.call_args
    model_arg = kwargs.get("model") or (_args[1] if len(_args) >= 2 else None)
    assert model_arg == "llama3.1"


def test_langchain_runnable_runner_raises_on_runnable_failure(monkeypatch):
    """An exception inside the runnable is caught and re-raised as WorkerError."""
    pytest.importorskip("langchain_core.runnables",
                        reason="langchain-core not installed (pip install ananta[langchain])")
    import langchain_core.runnables as rc  # type: ignore

    def fake_runnable_lambda(fn):
        return type("R", (), {"invoke": staticmethod(
            lambda x: (_ for _ in ()).throw(RuntimeError("kaboom"))
        )})()

    monkeypatch.setattr(rc, "RunnableLambda", fake_runnable_lambda)

    runner = LangChainRunnableRunner()
    with pytest.raises(WorkerError) as exc:
        runner.run(
            prompt="p", payload={}, budget=_budget(),
            model_provider_ref="local.x",
        )
    assert exc.value.reason_code == "langchain_runnable_failed"
    assert "kaboom" in str(exc.value)


# ── Adapter-level integration (skeleton still default-off) ────────────


def test_lc_adapter_live_path_uses_simplex_when_framework_missing(monkeypatch):
    """Without langchain installed, execute() must use SimplexRunner.

    We monkey-patch generate_text and verify the runner_label is
    'simplex' in the resulting artifact.
    """
    from agent.providers.lc_lg import LangChainProviderConfig
    from worker.adapters.langchain_adapter import LangChainAdapter

    cfg = LangChainProviderConfig(enabled=True, mode="local_live",
                                  model_provider_ref="local.x")
    a = LangChainAdapter(cfg)

    # Pretend langchain is NOT importable.
    monkeypatch.setattr(
        "worker.adapters.langchain_adapter.LangChainAdapter._langchain_available",
        staticmethod(lambda: False),
    )

    with patch("agent.llm_integration.generate_text", return_value="ok"):
        r = a.execute(task_id="t1", task_type="rag_query",
                      payload={"query": "hi"})
    assert r.status == "success"
    assert r.artifacts
    assert r.artifacts[0]["runner"] == "simplex"


def test_lc_adapter_live_path_uses_langchain_runnable_when_framework_available(monkeypatch):
    """With langchain installed, execute() must use LangChainRunnableRunner.

    We monkey-patch both the availability check and the framework's
    RunnableLambda so the test never opens a real LLM connection.
    """
    pytest.importorskip("langchain_core.runnables",
                        reason="langchain-core not installed (pip install ananta[langchain])")
    from agent.providers.lc_lg import LangChainProviderConfig
    from worker.adapters.langchain_adapter import LangChainAdapter
    import langchain_core.runnables as rc  # type: ignore

    monkeypatch.setattr(
        "worker.adapters.langchain_adapter.LangChainAdapter._langchain_available",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(rc, "RunnableLambda",
                        staticmethod(lambda fn: type("R", (), {
                            "invoke": staticmethod(lambda x: fn(x))
                        })()))

    cfg = LangChainProviderConfig(enabled=True, mode="local_live",
                                  model_provider_ref="local.x")
    a = LangChainAdapter(cfg)

    with patch("agent.llm_integration.generate_text", return_value="ok"):
        r = a.execute(task_id="t1", task_type="rag_query",
                      payload={"query": "hi"})
    assert r.status == "success"
    assert r.artifacts[0]["runner"] == "langchain_runnable"


def test_lc_adapter_live_path_budget_exceeded_returns_failed(monkeypatch):
    """A pre-saturated budget causes execute() to fail cleanly.

    We construct a budget with max_steps=1, pre-record one step
    (the codecompass_query that dry_run() does internally), and
    then let the simplex runner hit its own step. SimplexRunner's
    record_step raises budget_steps_exceeded; the adapter catches
    it and returns a failed result.
    """
    from agent.providers.lc_lg import LangChainProviderConfig
    from worker.adapters.langchain_adapter import LangChainAdapter

    cfg = LangChainProviderConfig(enabled=True, mode="local_live",
                                  model_provider_ref="local.x",
                                  max_steps=1)
    a = LangChainAdapter(cfg)
    monkeypatch.setattr(
        "worker.adapters.langchain_adapter.LangChainAdapter._langchain_available",
        staticmethod(lambda: False),
    )

    # Force the budget to be saturated BEFORE the adapter uses it:
    # we substitute the budget constructor so it starts with steps=1
    # (i.e. the next record_step raises).
    from worker.adapters import workflow_budget as wb_mod
    original_init = wb_mod.WorkflowBudgetGuard.__init__

    def saturated_init(self, **kwargs):
        original_init(self, **kwargs)
        # Pre-consume all allowed steps. Next record_step raises.
        self._steps = self._max_steps

    monkeypatch.setattr(wb_mod.WorkflowBudgetGuard, "__init__", saturated_init)

    with patch("agent.llm_integration.generate_text", return_value="ok"):
        r = a.execute(task_id="t1", task_type="rag_query",
                      payload={"query": "hi"})
    assert r.status == "failed"
    assert r.reason_code == "budget_steps_exceeded"


# ── Helpers ────────────────────────────────────────────────────────────


def _budget() -> WorkflowBudgetGuard:
    return WorkflowBudgetGuard(max_steps=10, timeout_seconds=60)

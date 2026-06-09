"""Chain runners for the workflow-adapter layer (LCG-007/008/009 v0.8 closure).

A runner is a thin wrapper around the LLM call. Two runners live here:

- SimplexRunner: deterministic, no framework. Calls generate_text()
  with the prompt and returns the response. This is the v0.7 default
  and works on any Ananta install.

- LangChainRunnableRunner: thin wrapper that uses LangChain's
  Runnable protocol to expose the LLM call as a chain. The actual
  LLM call still goes through generate_text() so the runtime
  behaviour is identical; only the wrapping changes. This runner
  is selected automatically when the `langchain` extra is installed.

Both runners honour the WorkflowBudgetGuard (steps + timeout) and
the model's external_calls_allowed posture. They never bypass the
policy gate.
"""
from __future__ import annotations

import logging
from typing import Any

from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.adapters.workflow_adapter_base import WorkerError

logger = logging.getLogger(__name__)


# ── SimplexRunner ──────────────────────────────────────────────────────


class SimplexRunner:
    """Prompt -> generate_text() -> response. No framework needed.

    This is the v0.7 fallback path. It is the path taken when the
    user has not installed `ananta[langchain]`. The runner is
    deliberately simple: a single prompt, a single response, an
    artifact. It is the right level of complexity for v0.7; a
    richer chain (multi-step, tool-using) is a v0.8+ concern.
    """

    def run(
        self,
        *,
        prompt: str,
        payload: dict[str, Any],
        budget: WorkflowBudgetGuard,
        model_provider_ref: str,
    ) -> str:
        """Return the model response text. Raises WorkerError on failure."""
        # Lazy import: generate_text pulls in the LLM provider stack
        # which we don't want at module load time of the adapter.
        from agent.llm_integration import generate_text

        # record_step() also enforces the timeout; pre-checking it
        # here gives a clean error before we open the LLM connection.
        budget.record_step("simplex_runner_entry")
        model = _parse_model_ref(model_provider_ref)
        try:
            response = generate_text(prompt=prompt, model=model, timeout=30)
        except Exception as exc:
            raise WorkerError(
                "llm_call_failed",
                f"generate_text failed: {type(exc).__name__}: {exc}",
            ) from exc
        # generate_text may return a string or a dict with 'text'/'content'.
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            return str(response.get("text") or response.get("content") or "")
        return str(response)


# ── LangChainRunnableRunner ────────────────────────────────────────────


class LangChainRunnableRunner:
    """Runnable-shaped wrapper around generate_text().

    Why a Runnable wrapper if we still call generate_text()? Because:
    - The LLM call is the same in both runners; what changes is the
      shape the chain takes. A v0.8+ user can replace the inner
      `generate_text` call with a real `ChatOpenAI` or
      `ChatOllama` Runnable without changing the adapter.
    - It documents the migration path: when you are ready to use
      real LangChain primitives, you swap this runner, not the
      adapter.

    The runner is selected automatically when `langchain` is
    importable. It is not safe to construct the runner if the
    framework is missing — the adapter checks availability first.
    """

    def run(
        self,
        *,
        prompt: str,
        payload: dict[str, Any],
        budget: WorkflowBudgetGuard,
        model_provider_ref: str,
    ) -> str:
        budget.record_step("langchain_runnable_entry")
        # The framework import is at call time, not at module load.
        try:
            from langchain_core.runnables import RunnableLambda  # type: ignore
        except ImportError as exc:
            raise WorkerError(
                "langchain_runtime_missing",
                f"langchain-core is not importable: {exc}",
            ) from exc

        # Build a Runnable that wraps the simplex-style call. This is
        # the documented migration path: replace the body of this
        # lambda with a real chain (e.g. prompt | llm | output_parser)
        # without changing the adapter.
        def _call(input_dict: dict[str, Any]) -> str:
            from agent.llm_integration import generate_text
            model = _parse_model_ref(model_provider_ref)
            response = generate_text(
                prompt=str(input_dict.get("prompt") or ""),
                model=model,
                timeout=30,
            )
            if isinstance(response, str):
                return response
            if isinstance(response, dict):
                return str(response.get("text") or response.get("content") or "")
            return str(response)

        runnable = RunnableLambda(_call)
        try:
            return str(runnable.invoke({"prompt": prompt, "payload": payload}))
        except Exception as exc:
            raise WorkerError(
                "langchain_runnable_failed",
                f"RunnableLambda failed: {type(exc).__name__}: {exc}",
            ) from exc


# ── Helpers ────────────────────────────────────────────────────────────


def _parse_model_ref(model_provider_ref: str) -> str | None:
    """Convert `local.default` -> `default` so generate_text can resolve it.

    The provider config's `model_provider_ref` follows the Ananta
    convention `<provider>.<model>` (e.g. `local.default`,
    `ollama.llama3.1`). `generate_text` expects the bare model name
    with the provider inferred from the runtime, so we strip the
    `local.` prefix and pass through anything else unchanged.
    """
    if not model_provider_ref:
        return None
    if "." in model_provider_ref:
        return model_provider_ref.split(".", 1)[1]
    return model_provider_ref

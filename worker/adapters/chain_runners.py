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
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.structured_output import StructuredOutputService
from worker.adapters.workflow_adapter_base import WorkerError
from worker.adapters.workflow_budget import WorkflowBudgetGuard

logger = logging.getLogger(__name__)


class TextGenerationPort(Protocol):
    def generate_text(self, **values: Any) -> Any: ...


class _UnavailableTextGeneration:
    def generate_text(self, **values: Any) -> Any:
        del values
        raise RuntimeError("worker_text_generation_not_composed")


_TEXT_GENERATION: TextGenerationPort = _UnavailableTextGeneration()


def configure_text_generation(port: TextGenerationPort) -> None:
    """Inject the Worker provider port from the role-guarded process root."""

    global _TEXT_GENERATION
    if port is None or not callable(getattr(port, "generate_text", None)):
        raise ValueError("worker_text_generation_port_invalid")
    _TEXT_GENERATION = port


@dataclass(frozen=True)
class ValidatedChainOutput:
    value: Any
    structured: bool
    audit_events: tuple[dict[str, Any], ...] = ()


def validate_chain_output(
    output: str,
    *,
    payload: dict[str, Any],
    node: dict[str, Any] | None = None,
) -> ValidatedChainOutput:
    """Apply the provider-neutral structured-output gate for every runner.

    Framework adapters call this only after the shared provider middleware and
    before publishing artifacts or advancing to a potentially writing node.
    """

    node_payload = dict(node or {})
    raw_schema = node_payload.get("output_schema", payload.get("output_schema"))
    output_format = str(
        node_payload.get("output_format") or payload.get("output_format") or ""
    ).lower()
    if raw_schema is None and output_format != "json":
        return ValidatedChainOutput(value=output, structured=False)
    schema = raw_schema if isinstance(raw_schema, dict) else {"type": "object"}
    result = StructuredOutputService(
        max_repair_attempts=1 if bool(payload.get("allow_format_repair", False)) else 0
    ).validate_json(
        output,
        schema,
        allow_format_repair=bool(payload.get("allow_format_repair", False)),
    )
    if not result.valid:
        reasons = ",".join(sorted({issue.reason_code for issue in result.issues}))
        raise WorkerError(
            "structured_output_validation_failed",
            f"Structured output failed the shared schema gate: {reasons}",
        )
    return ValidatedChainOutput(
        value=result.value,
        structured=True,
        audit_events=tuple(dict(event) for event in result.audit_events),
    )


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
        # record_step() also enforces the timeout; pre-checking it
        # here gives a clean error before we open the LLM connection.
        budget.record_step("simplex_runner_entry")
        provider, model = _bound_provider_model(
            payload=payload,
            model_provider_ref=model_provider_ref,
        )
        try:
            response = _TEXT_GENERATION.generate_text(
                prompt=prompt,
                provider=provider,
                model=model,
                timeout=30,
                provider_context=payload.get("provider_context"),
            )
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
            provider, model = _bound_provider_model(
                payload=(
                    input_dict.get("payload", {})
                    if isinstance(input_dict.get("payload"), dict)
                    else {}
                ),
                model_provider_ref=model_provider_ref,
            )
            response = _TEXT_GENERATION.generate_text(
                prompt=str(input_dict.get("prompt") or ""),
                provider=provider,
                model=model,
                timeout=30,
                provider_context=(
                    input_dict.get("payload", {}).get("provider_context")
                    if isinstance(input_dict.get("payload"), dict)
                    else None
                ),
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


def _bound_provider_model(
    *, payload: dict[str, Any], model_provider_ref: str
) -> tuple[str | None, str | None]:
    raw = payload.get("provider_context")
    context = dict(raw) if isinstance(raw, dict) else {}
    provider = str(context.get("selected_provider_id") or "").strip() or None
    model = str(context.get("selected_model_id") or "").strip() or None
    if bool(provider) != bool(model):
        raise WorkerError(
            "provider_selection_binding_incomplete",
            "Hub provider/model selection is incomplete.",
        )
    if bool(context.get("require_hub_provider_budget", False)) and not provider:
        raise WorkerError(
            "provider_selection_binding_required",
            "Hub provider/model selection is required for this invocation.",
        )
    if provider is not None and model is not None:
        return provider, model
    return None, _parse_model_ref(model_provider_ref)

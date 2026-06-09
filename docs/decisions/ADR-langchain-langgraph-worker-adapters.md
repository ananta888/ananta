# ADR: LangChain and LangGraph as optional Worker Adapters

Date: 2026-06-09
Status: accepted
Supersedes: none
Related: ADR-workflow-automation-adapters.md (parent ADR for all external workflow engines)

## Context

The Ananta Hub already integrates with external workflow engines
through a provider-neutral interface (n8n, generic webhooks, mock).
The ADR-workflow-automation-adapters established the boundary:
external engines are optional, the Hub stays the source of truth for
policy, approval, audit, task state, and artifact verification.

Two new engine candidates have appeared:

- **LangChain** — a popular Python framework for composing LLM calls,
  retrieval, and tools into declarative *chains*. Often used for RAG,
  summarization, and tool-using agents.
- **LangGraph** — a stateful graph runtime built on top of LangChain
  primitives, designed for multi-step agent workflows with explicit
  state, checkpoints, and human-in-the-loop nodes.

Both frameworks are widely adopted. Adopting them risks crossing the
boundary the parent ADR drew: chains/graphs could leak their own
retrieval index, call external LLM endpoints without Hub policy
control, or hold task state that contradicts the Hub's plan. The
question is **whether and how** to integrate them.

## Decision

LangChain and LangGraph are integrated as **optional worker adapters**
under the existing `WorkflowAdapter` interface. They follow the same
rules as n8n, generic webhooks, and mock providers:

1. **Default OFF.** Both providers ship with `enabled=False`,
   `mode=dry_run`. The Hub runs without them. No core dependency is
   added; both frameworks are installable via the optional
   `ananta[langchain]` and `ananta[langgraph]` extras.
2. **CodeCompass is the only allowed retriever.** The
   `CodeCompassRetriever` wraps the existing
   `HybridRetrievalService`. LangChain's own vector stores and
   loaders are not used as a project source of truth. If a chain
   needs more context, it asks CodeCompass.
3. **Hub owns policy, approval, audit, and state.** All tool calls
   flow through `WorkflowPolicyGate`. All risky actions (write,
   delete, network, shell, patch, push) require explicit Hub approval
   via `dry.approval_required` → blocked result. The audit log is
   per-task, attached to `WorkflowArtifactResult.execution_trace`, and
   never accumulates across tasks.
4. **Artifact-first results.** Adapters return
   `WorkflowArtifactResult` (summary + artifacts + sources +
   diagnostics + policy_decisions + execution_trace). They do not
   stream raw LLM tokens to the user.
5. **Budgets are enforced.** `max_steps`, `max_iterations`,
   `timeout_seconds`, and an optional `max_tokens` are checked at
   every step. A budget overrun raises `WorkerError` and ends the
   task with a partial artifact.
6. **Default-DENY for tools.** An empty `allowed_tools` set on
   `WorkflowPolicyGate` blocks every tool. Tools must be explicitly
   allowlisted. The hard-deny list (`exec_shell`, `write_file`,
   `delete_file`, `http_request`, etc.) is unconditional.
7. **State is the Hub's.** LangGraph's checkpoint store is
   `local_ephemeral` or `local_ephemeral_or_hub_owned` by default.
   No state is allowed to overwrite Hub task state.
8. **Local first, cloud only by explicit opt-in.** Default
   `model_provider_ref` is `local.default`. The `cloud_gated` mode
   requires `external_calls_allowed=True` and is validated by the
   provider config model.

## Architecture

```
                 ┌──────────────────────────────────────┐
                 │            Ananta Hub                │
                 │  (policy, approval, audit, state)    │
                 └────────────┬─────────────────────────┘
                              │  WorkflowAdapter (dry_run / execute)
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
   ┌────▼─────┐         ┌─────▼──────┐         ┌─────▼─────┐
   │ n8n /    │         │ LangChain  │         │ LangGraph │
   │ webhook  │         │  Adapter   │         │  Adapter  │
   │ (parent) │         │  (LCG-007) │         │  (LCG-008)│
   └────┬─────┘         └─────┬──────┘         └─────┬─────┘
        │                     │                      │
        │                     │  CodeCompassRetriever│
        │                     └──────────┬───────────┘
        │                                │
        │                     ┌──────────▼──────────┐
        │                     │  CodeCompass        │
        │                     │  (HybridRetrieval)  │
        │                     └─────────────────────┘
        │
        ▼
   external HTTP
```

- LangChain/LangGraph are **siblings** of n8n, not replacements.
- CodeCompass sits **below** them as a shared retriever layer.
- The Hub sits **above** them as the policy/audit gate.

## Forbidden usage (mirrors parent ADR, applied to LCG)

- Auto-merge / push without Hub approval.
- Unrestricted shell, network, or filesystem writes.
- Credential exfiltration in prompts, logs, traces, or artifacts.
- LangGraph checkpoint state overwriting Hub task state.
- LangChain's own retriever/vector store used as a *primary* project
  index — it may only wrap CodeCompass results.
- LangSmith, OpenAI, Anthropic, or any external service called without
  `external_calls_allowed=True` and Hub policy approval.

## Consequences

- Ananta continues to work without LangChain/LangGraph installed.
- The two extras are additive; the core `pip install ananta` does not
  pull them in.
- Adding real LLM execution to the adapters is a follow-up commit;
  the current skeleton proves the contract end-to-end and produces
  artifact-first results.
- Provider config and descriptor schemas are validated without the
  framework being installed (JSON schema + pydantic).
- The `WorkflowAdapter` interface is now load-bearing for four
  provider families; future engines (e.g. Temporal, Prefect) plug into
  the same interface.

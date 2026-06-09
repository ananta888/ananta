# LangChain and LangGraph Adapters — Architecture Detail

This document complements
[ADR-langchain-langgraph-worker-adapters.md](../decisions/ADR-langchain-langgraph-worker-adapters.md)
with the concrete code layout, control flow, and integration seams
for the two new optional worker adapters. It is the engineering
reference; the ADR is the decision record.

## Module layout

```
worker/adapters/
├── workflow_adapter_base.py        # WorkflowAdapter protocol, DryRunResult,
│                                   # WorkflowArtifactResult, WorkerError
├── workflow_adapter_registry.py    # Lazy registry; degrades on missing optional deps
├── workflow_policy_gate.py         # Default-deny tool/network gate
├── workflow_audit.py               # Per-task audit log with atomic snapshot()
├── workflow_budget.py              # Step/token/timeout enforcement
├── langchain_adapter.py            # LangChainAdapter (LCG-007)
└── langgraph_adapter.py            # LangGraphAdapter (LCG-008)

worker/retrieval/
└── codecompass_retriever.py        # CodeCompassRetriever (LCG-009)

agent/providers/lc_lg/
├── __init__.py                     # Re-exports
├── langchain_provider_config.py    # LangChainProviderConfig (LCG-003)
└── langgraph_provider_config.py    # LangGraphProviderConfig (LCG-004)

docs/contracts/
├── langchain-chain-descriptor.schema.json   # (LCG-014)
└── langgraph-graph-descriptor.schema.json   # (LCG-015)
```

## Control flow

### Dry-run (no LLM call, no side effects)

```
caller
  └─→ adapter.dry_run(task_id, task_type, payload)
        ├─→ audit.snapshot()            # discard prior task's events
        ├─→ audit.log("dry_run_start")
        ├─→ policy.check_tool(...) for each requested tool
        ├─→ block if retriever != "codecompass"
        ├─→ block if external_url without external_calls_allowed
        ├─→ build plan_steps
        ├─→ audit.log("dry_run_complete", blocked, approval_required)
        └─→ return DryRunResult
              {
                plan_steps, required_tools, required_context_sources,
                policy_decisions, approval_required, approval_reasons,
                risk_level, blocked, block_reason, metadata: {
                  dry_run_audit_trace: <events for THIS task>
                }
              }
```

### Live execute (requires explicit opt-in)

```
caller
  └─→ adapter.execute(task_id, task_type, payload)
        ├─→ audit.snapshot()            # clean slate
        ├─→ audit.log("execute_start")
        ├─→ if not config.is_live(): blocked_result("live_execution_requires_live_mode")
        ├─→ dry = dry_run(...)
        ├─→ if dry.blocked: blocked_result(dry.block_reason)
        ├─→ if dry.approval_required: blocked_result("approval_required")
        ├─→ budget = WorkflowBudgetGuard(max_steps, timeout_seconds, max_tokens)
        ├─→ _run_chain / _run_graph(...)
        │     ├─→ policy gate on every tool call
        │     ├─→ retriever.query(...) — CodeCompass only
        │     ├─→ build artifact
        │     └─→ audit.log(...)
        └─→ return WorkflowArtifactResult
              {
                status, summary, artifacts, sources, diagnostics,
                policy_decisions,
                execution_trace: audit.snapshot()  # THIS task only
              }
```

The audit log is per-task because `audit.snapshot()` returns and
clears atomically. The next task starts fresh; no leakage.

## Why the WorkflowAdapter interface (DIP)

`WorkflowAdapter` is a `Protocol` in `workflow_adapter_base.py`:

```python
class WorkflowAdapter(Protocol):
    def descriptor(self) -> WorkflowAdapterDescriptor: ...
    def dry_run(self, *, task_id, task_type, payload) -> DryRunResult: ...
    def execute(self, *, task_id, task_type, payload) -> WorkflowArtifactResult: ...
```

The Hub depends on this protocol, never on concrete adapters. Adding
LangChain or LangGraph required no Hub code changes. The
`workflow_adapter_registry` returns the registry; an adapter is
absent (degraded) when its package is not installed.

## CodeCompass as the only retriever

`CodeCompassRetriever.query(query, max_results)` wraps
`HybridRetrievalService.retrieve(...)`. Failures are swallowed
(returning `sources=[]`) so a cold CodeCompass index does not break
dry-runs. Adapters that try to set `retriever_ref` to anything other
than `codecompass` or `none` are blocked in the dry-run.

The LCG-010 follow-up will route the embedding scope through
`embedding_provider_config_service` so the dimension/model/scope
appear in provider diagnostics. Until then, the retriever uses the
Hub's default embedding configuration.

## Policy gate semantics

Three layers, evaluated in order:

1. **Hard-deny list** — `exec_shell`, `write_file`, `delete_file`,
   `read_file_arbitrary`, `http_request`, `network_scan`,
   `spawn_process`. Always blocked.
2. **Empty allowlist** — `allowed_tools=set()` blocks every tool
   (default-deny). Tools must be explicitly listed.
3. **Allowlist** — otherwise the tool is in the allowlist.

Network resources use `external_calls_allowed` (a separate flag)
rather than the tool allowlist. This lets chains run entirely on
local models with no HTTP at all.

## Human approval

`dry.approval_required` is set when:

- The task type is `tool_chain` (LangChain) or any kind with
  high-risk tool nodes (LangGraph).
- The descriptor references a `human_gate` node (LangGraph).
- Any tool node's `tool_ref` is in
  `LangGraphProviderConfig.human_in_loop_required_for` (defaults:
  `write, delete, network, shell, patch, push`).

When set, `execute()` returns a blocked result with
`reason_code="approval_required"`. The Hub's approval service is
expected to either grant approval (resuming the task) or reject it.

## Budget enforcement

`WorkflowBudgetGuard` is created at the top of `execute()` and passed
to `_run_chain` / `_run_graph`. Every significant step calls
`budget.record_step(label)`. Three checks:

- `steps > max_steps` → `WorkerError("budget_steps_exceeded")`
- `elapsed > timeout_seconds` → `WorkerError("budget_timeout")`
- `tokens > max_tokens` → `WorkerError("budget_tokens_exceeded")`

A `WorkerError` becomes a `WorkflowArtifactResult` with
`status="failed"` and a stable `reason_code`. The Hub can decide
whether to retry.

## Descriptor schemas

Both schemas are pure JSON Schema 2020-12. They are validated
without LangChain/LangGraph being installed, so a CI gate can
validate `examples/langchain/*.json` and `examples/langgraph/*.json`
even in the core install.

- `langchain-chain-descriptor.schema.json` declares
  `id, purpose, inputs, outputs, model_ref, retriever_ref, tools,
   policies, artifact_outputs`. `retriever_ref` is an enum of
  `codecompass` or null.
- `langgraph-graph-descriptor.schema.json` declares
  `graph_id, nodes, edges, entrypoint, stop_conditions,
   checkpoint_policy, human_gates, artifact_outputs, policies`. Node
  `kind` is an enum: `llm, tool, human_gate, router,
  artifact_writer, retriever, end`.

## Future work (follow-up commits)

- **LCG-010** — Route `CodeCompassRetriever` through
  `embedding_provider_config_service` for visible scope/dimension
  diagnostics.
- **LCG-021** — TUI/Angular surface for the registry, dry-run, and
  approval gate.
- **LCG-028** — Profile updates (`local-only`, `cloud-free-first`,
  `local-rtx3080-freecloud-minimax`).
- **Real chain runner** — Replace the placeholder text in
  `langchain_adapter._run_chain` with an actual LLM call once a real
  executor and provider wiring are in place. The current skeleton
  proves the contract end-to-end.

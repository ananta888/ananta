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
├── workflow_adapter_registry.py    # Lazy registry; profile auto-loading from AGENT_CONFIG
├── workflow_policy_gate.py         # Default-deny tool/network gate
├── workflow_audit.py               # Per-task audit log, dict-based redaction, snapshot()
├── workflow_budget.py              # Step/token/timeout enforcement
├── langchain_adapter.py            # LangChainAdapter (LCG-007); LCEL chain path; stream()
├── langgraph_adapter.py            # LangGraphAdapter (LCG-008); compile path; resume; stream()
├── lc_chat_model_factory.py        # build_lc_chat_model() — prefix → ChatModel mapping
└── lc_tool_registry.py             # get_tools_for_chain() — allowlist + hard-deny filter

worker/retrieval/
├── codecompass_retriever.py        # CodeCompassRetriever (LCG-009)
└── lc_baseretriever_adapter.py     # LangChainCodeCompassRetriever (BaseRetriever wrapper)

agent/providers/lc_lg/
├── __init__.py                     # Re-exports
├── langchain_provider_config.py    # LangChainProviderConfig (LCG-003, LCG-033)
└── langgraph_provider_config.py    # LangGraphProviderConfig (LCG-004, LCG-031, LCG-032)

agent/routes/
└── workflow_adapters.py            # Blueprint: /api/workflow_adapters/ (LCG-034, LCG-053)

client_surfaces/operator_tui/
└── workflow_adapter_panel.py       # TUI status panel + dry_run plan view (LCG-056, LCG-057)

docs/contracts/
├── langchain-chain-descriptor.schema.json    # v1.0 (LCG-014)
├── langgraph-graph-descriptor.schema.json    # v1.0 (LCG-015)
├── langchain-chain-descriptor.v1.1.json      # v1.1 (LCG-066) — additive, backward-compat
└── langgraph-graph-descriptor.v1.1.json      # v1.1 (LCG-066)
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

The embedding scope is configured via `LangGraphProviderConfig.embedding_provider_scope`
(default: `"codecompass_vector"`). It is passed to `CodeCompassRetriever(scope=...)`
on adapter init and reflected in `provider_diagnostics` from `descriptor()`.

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

- `langchain-chain-descriptor.schema.json` (v1.0) declares
  `id, purpose, inputs, outputs, model_ref, retriever_ref, tools,
   policies, artifact_outputs`. `retriever_ref` is an enum of
  `codecompass` or null.
- `langgraph-graph-descriptor.schema.json` (v1.0) declares
  `graph_id, nodes, edges, entrypoint, stop_conditions,
   checkpoint_policy, human_gates, artifact_outputs, policies`. Node
  `kind` is an enum: `llm, tool, human_gate, router,
  artifact_writer, retriever, end`.
- v1.1 variants (`*.v1.1.json`) are additive: they add
  `prompt_template`, `output_format`, `condition` on edges, and
  `subgraph` node kind. All v1.0 examples validate against v1.1 —
  no breaking changes.

## Security and redaction

`WorkflowAuditLog.log()` applies `agent.common.redaction.redact()` to
the entire kwargs dict (dict-based path) before storing. This means:

- Any kwarg whose **key** matches a known sensitive key (e.g., `token`,
  `api_key`, `secret`, `password`) is replaced with
  `***REDACTED_TOKEN***` (or similar) at the key level.
- Any kwarg whose **value** contains an `sk-[A-Za-z0-9_-]{20,}` or
  AWS key pattern is replaced at the string level.
- Non-string values pass through unchanged.
- `_build_prompt()` and `_build_node_prompt()` apply `redact()` to
  query strings and prior LLM responses before embedding them in prompts.
- `_serialize_state()` (resume_token) applies `redact()` to
  `llm_responses` before JSON-serializing.

## Streaming (stream())

Both adapters expose a `stream(*, task_id, task_type, payload)` method
that yields `dict` events. Policy gate (`dry_run`) is checked before
the generator body — a blocked dry-run yields a single `stream_blocked`
event and stops immediately.

- **LangGraphAdapter**: when compiled graph is available → yields
  `node_complete` events per node, then `stream_end`. Without compiled
  graph → single `stream_end` (batch fallback).
- **LangChainAdapter**: when LCEL chain is available → yields `token`
  events per chunk, then `stream_end`. Without model → batch fallback.

The Hub SSE endpoint (`GET /api/workflow_adapters/<kind>/stream`) wraps
this generator in a `text/event-stream` response.

## Hub API

`agent/routes/workflow_adapters.py` (Blueprint `workflow_adapters_bp`,
prefix `/api/workflow_adapters`) provides:

| Method | Path                          | Purpose                        |
|--------|-------------------------------|--------------------------------|
| GET    | `/`                           | List all adapter descriptors   |
| GET    | `/<kind>/`                    | Single adapter descriptor      |
| POST   | `/<kind>/dry_run`             | Run dry-run, get plan          |
| POST   | `/<kind>/execute`             | Execute (resume_token support) |
| GET    | `/<kind>/stream`              | SSE streaming                  |

All routes require `@check_auth`. The registry auto-loads provider
config from `AGENT_CONFIG['providers']` when the Flask app context
is active (profile auto-loading, LCG-035).

# LangChain and LangGraph — Optional Setup

Date: 2026-06-09
Related: [ADR-langchain-langgraph-worker-adapters.md](../decisions/ADR-langchain-langgraph-worker-adapters.md)

LangChain and LangGraph are **optional** worker adapters. The core
`pip install ananta` does NOT pull them in. This document shows the
opt-in path, the default-off behaviour, and the smoke check.

## TL;DR

```bash
# 1. Core install (no LCG frameworks)
pip install ananta

# 2a. Add LangChain only
pip install 'ananta[langchain]'

# 2b. Add LangGraph only (also pulls langchain-core)
pip install 'ananta[langgraph]'

# 2c. Add both
pip install 'ananta[lc-lg]'
```

After install, the adapters are still disabled by default. Enable
them in a profile (see [Profile update](#profile-update) below).

## What the extras pull in

| Extra        | Pulled in                          | Notes                                |
|--------------|------------------------------------|--------------------------------------|
| `langchain`       | `langchain-core>=0.3,<0.4`         | Core chain runtime only.              |
| `langgraph`       | `langgraph>=0.2,<0.3`, `langchain-core>=0.3,<0.4` | Graph runtime + matching core. |
| `lc-lg`           | both of the above                  | Convenience meta-extra.               |
| `lc-lg-ollama`    | `langchain-ollama>=0.3,<0.4`       | ChatOllama for local models.          |
| `lc-lg-openai`    | `langchain-openai>=0.3,<0.4`       | ChatOpenAI for cloud.openai.*         |
| `lc-lg-anthropic` | `langchain-anthropic>=0.3,<0.4`    | ChatAnthropic for cloud.anthropic.*   |
| `lc-lg-all`       | `lc-lg` + all three above          | Full install incl. all providers.     |

The `lc-lg-*` provider extras are optional. They unlock the **ChatModel path**
via `build_lc_chat_model(model_provider_ref)` in `lc_chat_model_factory.py`.
When a provider extra is missing, the adapter falls back to `SimplexRunner`
(which uses `agent.llm_integration.generate_text` via the hub's LLM routing).

The Ananta default is local models; add a provider package explicitly
when you need it. `langchain-community` is never pulled in automatically.

## Why optional, not required

Per the ADR, both frameworks are opt-in because:

- The core Ananta install must not grow by ~50 MB or risk a
  version-conflict with the existing `llama-index` dependency.
- LangChain's release cadence is fast and breaking — pinning it in
  the core would create a maintenance burden for users who do not
  use it.
- The adapter skeletons validate descriptors and produce
  dry-run results without the framework installed. The contract is
  the testable boundary; the framework is an implementation detail
  of the live path.

## Smoke check

```python
# After pip install 'ananta[lc-lg]'
from worker.adapters.workflow_adapter_registry import list_adapters_as_dicts
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.langgraph_adapter import LangGraphAdapter

# Descriptor reports the framework availability.
print(LangChainAdapter().descriptor().status)
print(LangGraphAdapter().descriptor().status)
# 'disabled adapter_disabled_by_config' by default,
# 'degraded framework_not_installed' if you skipped the extras.

# Registry lists all four (or however many) providers.
for a in list_adapters_as_dicts():
    print(a["adapter_id"], a["status"])
```

Expected output for a core install:

```
disabled adapter_disabled_by_config
degraded framework_not_installed
adapter.langchain disabled ...
adapter.langgraph degraded ...
adapter.n8n       disabled ...
```

Expected output after `pip install 'ananta[lc-lg]'`:

```
disabled adapter_disabled_by_config
disabled adapter_disabled_by_config
adapter.langchain disabled ...
adapter.langgraph disabled ...
adapter.n8n       disabled ...
```

Note: even with the extras installed, the adapters stay
`adapter_disabled_by_config` until you flip the relevant provider
config to `enabled=True` in a profile.

## Profile update

LCG-028 in the todo. The minimal change for a profile that wants
LangChain enabled is to add a provider block:

```json
{
  "providers": {
    "langchain": {
      "enabled": true,
      "mode": "dry_run",
      "model_provider_ref": "local.default",
      "external_calls_allowed": false,
      "allowed_tools": ["summarize_doc", "search_code"],
      "human_in_loop_required_for": [],
      "max_steps": 8,
      "timeout_seconds": 60
    },
    "langgraph": {
      "enabled": false,
      "mode": "dry_run",
      "checkpoint_policy": "local_ephemeral",
      "human_in_loop_required_for": ["shell", "patch", "push", "delete"],
      "max_iterations": 10
    }
  }
}
```

Default values in the provider config:

- `enabled=False`
- `mode=dry_run`
- `model_provider_ref=local.default`
- `external_calls_allowed=False`
- `allowed_tools=set()` — empty allowlist means default-deny
- `human_in_loop_required_for={"shell", "patch", "push", "delete",
  "network", "write"}` for LangGraph
- `max_steps=8`
- `timeout_seconds=60`
- `max_tokens=None` (unlimited unless profile sets it)
- `checkpoint_policy=local_ephemeral`

## What still works without the extras

- The four descriptor schemas validate against example JSON
  descriptors in `examples/langchain/` and `examples/langgraph/`.
- The two adapters run `dry_run` end-to-end and produce
  `DryRunResult` with `plan_steps`, `policy_decisions`, and
  `dry_run_audit_trace` — all from the Ananta code path, no
  framework needed.
- `execute()` returns a blocked result with
  `reason_code="live_execution_requires_live_mode"` if the framework
  is not installed or the provider is not `enabled=True`.

## When something goes wrong

- `framework_not_installed` descriptor status — install the matching
  extra (`ananta[langchain]` or `ananta[langgraph]`).
- `validation_error: cloud_gated requires external_calls_allowed` —
  the provider config has `mode=cloud_gated` but
  `external_calls_allowed=False`. Either set
  `external_calls_allowed=True` (Hub approval required) or change
  `mode` to `local_only`.
- `validation_error: retriever_ref must be 'codecompass' or null` —
  your chain descriptor references a non-CodeCompass retriever. The
  ADR requires CodeCompass as the only retriever.
- `blocked: external_calls_blocked` — the chain tried an HTTP
  resource but `external_calls_allowed=False`. Either allow external
  calls (with Hub approval) or remove the network tool from the
  descriptor.

## ChatModel path (provider extras)

When a provider extra is installed, `build_lc_chat_model(model_provider_ref)` returns
a real `BaseChatModel` and the LCEL chain `(prompt | model | parser)` is used instead
of `SimplexRunner`. The prefix in `model_provider_ref` determines the provider:

| Prefix             | Provider extra       | ChatModel class     |
|--------------------|----------------------|---------------------|
| `ollama.*`         | `lc-lg-ollama`       | `ChatOllama`        |
| `local.*`          | `lc-lg-ollama`       | `ChatOllama`        |
| `openai.*`         | `lc-lg-openai`       | `ChatOpenAI`        |
| `cloud.openai.*`   | `lc-lg-openai`       | `ChatOpenAI`        |
| `anthropic.*`      | `lc-lg-anthropic`    | `ChatAnthropic`     |
| `cloud.anthropic.*`| `lc-lg-anthropic`    | `ChatAnthropic`     |

If the matching provider extra is absent, the LCEL chain is skipped and
`SimplexRunner` is used as a transparent fallback. No config change needed.

## StateGraph compile path (LangGraph live mode)

When `langgraph` is installed and `mode=local_live`, `LangGraphAdapter._run_graph()`
first attempts `_run_compiled_graph()`, which builds a real `StateGraph`, registers
each node as a Python function, compiles it with `checkpointer=_get_checkpointer()`,
and invokes it. Policy-Gate and Budget-Guard run inside each node function — identical
to the `_walk_nodes()` path.

If compilation fails (e.g., unsupported node kinds, StateGraph API change), the error
is caught, logged as `compiled_graph_failed_fallback` in the audit trace, and
`_walk_nodes()` is used as the fallback. No silent data loss.

`_get_checkpointer()` returns based on `checkpoint_policy`:

| Policy                         | Checkpointer                          |
|--------------------------------|---------------------------------------|
| `none`                         | `None` — no checkpointing             |
| `local_ephemeral`              | `MemorySaver()` (in-process only)     |
| `local_ephemeral_or_hub_owned` | `MemorySaver()` (hub store: LCG-049)  |
| `hub_owned`                    | `None` for now (not yet wired)        |

See `docs/architecture/langchain-langgraph-adapters.md` for the
control flow, `docs/architecture/codecompass-vs-langchain.md` for
the boundary rules, and `tests/test_workflow_lc_lg_smoke.py` for
end-to-end tests that do NOT require the framework installed.

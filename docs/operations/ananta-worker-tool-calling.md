# Ananta Worker Tool Calling

## Single source of truth

`AnantaToolRegistryService` (`agent/services/ananta_tool_registry_service.py`) is the
single canonical registry of all tools the ananta-worker may request. All policy
enforcement, prompt serialisation, and OpenAI-schema generation derive from this one
source. Do not add tools to `agent/tools.py` for worker use — register them in the
registry instead.

## Two serialisation formats

The registry supports two output formats for the same tool specs:

| Format | Method | Used by |
|--------|--------|---------|
| **prompt_json_protocol** | `describe_for_prompt()` | `run_ananta_worker_tool_loop()` — LLM receives tool list as text; emits `ananta_worker_tool_loop.v1` JSON |
| **native_openai_tools** | `describe_for_openai_tools()` | SGPT handler when backend supports OpenAI tools API |

`ToolCallingModeService` (`agent/services/tool_calling_mode_service.py`) resolves
which format to use at runtime based on provider, backend, and config.

`ToolSchemaAdapterService` (`agent/services/tool_schema_adapter_service.py`) is a thin
adapter over the registry; prefer it from call-sites that do not need direct registry
access.

## LLM never executes tools itself

The hub is always the executor. The LLM requests a tool call; the hub validates it
through `AnantaToolPolicyService`, then executes it via `execute_ananta_tool()` or
`UnifiedToolExecutionService`. The result is fed back as evidence in the next round.

## CodeCompass tools vs rag-helper

**CodeCompass tools** (`codecompass.*`) are agent-facing: the worker LLM requests them
to retrieve context, search symbols, expand the dependency graph, or read file excerpts.
They are part of the public tool contract and appear in both `describe_for_prompt()` and
`describe_for_openai_tools()`.

**rag-helper** is an internal backend service that CodeCompass tools call on the hub
side. It is not exposed to the worker LLM directly and does not appear in the tool
registry.

## Config keys

```yaml
ananta_worker_tool_calling:
  mode: auto               # auto | native_openai_tools | prompt_json_protocol | disabled
  native_backend_allowlist: [openai, lmstudio, ollama, litellm, openrouter, ananta-worker]
  native_backend_denylist: []
  fallback_mode: prompt_json_protocol

ananta_worker_tool_loop:
  enabled: false
  max_iterations: 6
  max_tool_calls: 12
  allowed_tools: []        # empty = all non-blocked tools

sgpt_native_tools:
  source: ""               # "" = legacy | "ananta_tool_registry_service"
  legacy_agent_tools_enabled: true
```

# ADR: AgentFeatureProvider Interface

**Status:** Proposed  
**Date:** 2026-06-22  
**Author:** Ananta Architecture  
**Relates to:** `docs/architecture/transformer-feature-provider.md`, `docs/architecture/codecompass-vector-encoding.md`

---

## Context

Ananta coordinates multiple AI agents — local CLI tools (OpenCode, Claude-CLI), external coding assistants (Codex, Copilot adapters), and local inference models — as workers that execute tasks. Workers have broad execution authority: they can read files, run code, produce patches.

For vector retrieval and ranking, a different mode is needed: agents that contribute structured feature signals without having execution authority. An agent that analyzes code and produces a relevance score is useful. An agent that produces a free-text answer that silently overrides domain scope is dangerous.

`AgentFeatureProvider` is the interface that separates these two modes. In FeatureProvider mode, agents deliver structured signals. Ananta decides everything else.

---

## Decision

Define `AgentFeatureProvider` as a first-class interface for agents operating in structured-output, read-only mode. Distinguish it clearly from the `WorkerExecution` path used for task execution.

---

## Core Principle

> Agents deliver signals. Ananta decides.

This is not a slogan — it is an architectural constraint enforced at every interface boundary.

---

## Interface Definition

```python
@dataclass(frozen=True)
class AgentFeatureOutput:
    feature_text: str                          # short structured summary, plain text
    feature_vector: list[float] | None         # optional embedding of feature_text
    confidence: float                          # 0.0–1.0, agent self-reported
    evidence_refs: list[str]                   # file paths or record IDs supporting the output
    provider_id: str                           # stable identifier for this provider instance
    model_or_agent_version: str               # exact model name + version
    policy_decision: str                       # "allowed" | "blocked:<reason>"
    diagnostics: dict[str, Any]
```

Every provider **must** produce this schema. Free-text answers, conversational turns, and JSON blobs that do not conform are rejected and logged as `malformed_output`, never passed downstream.

---

## PolicyScope — Required Parameters

Every `AgentFeatureProvider` instance must accept and enforce:

```python
@dataclass(frozen=True)
class AgentFeaturePolicy:
    policy_scope: str                          # e.g. "repo:ananta" or "path:/worker/**"
    allowed_paths: frozenset[str]              # glob patterns; provider only sees these
    max_input_chars: int                       # hard cap before dispatch
    no_write_mode: bool = True                 # provider must not write, patch, or execute
    external_calls_allowed: bool = False       # provider must not call external URLs
    allowed_provider_ids: frozenset[str] = frozenset()  # which adapters may be used
```

**Enforcement:**

- Context packages passed to providers are pre-filtered to `allowed_paths` before dispatch.
- Provider outputs are validated against `allowed_paths` after return (no invented paths).
- `no_write_mode=True` (default): any tool call or file write in provider output causes `PolicyViolationError`.
- `external_calls_allowed=False` (default): HTTP calls outside localhost are blocked.
- A provider cannot grant itself new paths or permissions through its output.

---

## Structured Outputs Only

Providers receive a `ContextPackage` (a pre-assembled, policy-filtered snapshot of repo context) and must return `AgentFeatureOutput`. They do not receive:

- Full repo directory access.
- Shell execution rights.
- Access to secrets, credentials, or auth tokens.
- A free-form conversation history.

If a provider returns a conversational answer rather than the schema, the output is discarded. The provider is marked `degraded` in diagnostics.

---

## Adapters

### OpenCode Adapter (read-only)

```python
class OpenCodeFeatureAdapter(AgentFeatureProvider):
    mode = "analyze-only"
    tool_access = ["read_file", "grep_search"]   # no write tools
    output_validation = AgentFeatureOutput
```

- Receives only files from `allowed_paths`.
- Tool calls are intercepted: write/execute calls raise `PolicyViolationError`.
- Returns structured analysis: relevance assessment, code smell detection, dependency signals.

### Codex Adapter (read-only)

```python
class CodexFeatureAdapter(AgentFeatureProvider):
    mode = "analyze-only"
    requires = {"external_calls_allowed": True, "allowed_provider_ids": {"codex-v1"}}
```

- Requires explicit `external_calls_allowed=True` in policy.
- Input is truncated to `max_input_chars` before dispatch.
- API key is provided via environment; never included in `ContextPackage` or diagnostics.

### Claude-CLI Adapter (read-only)

```python
class ClaudeCliFeatureAdapter(AgentFeatureProvider):
    mode = "analyze-only"
    invocation = "claude --no-write --output-format json"
```

- Invoked via subprocess with `--no-write` flag.
- Stdout is parsed as JSON against `AgentFeatureOutput` schema.
- Non-conforming output is logged and discarded.

---

## AgentFeatureTrace

Every provider invocation produces an `AgentFeatureTrace` record stored in diagnostics:

```python
@dataclass(frozen=True)
class AgentFeatureTrace:
    trace_id: str
    provider_id: str
    model_or_agent_version: str
    input_hash: str             # SHA-256 of ContextPackage content
    output_hash: str            # SHA-256 of raw provider output before validation
    elapsed_ms: float
    policy_decision: str        # "allowed" | "blocked:<reason>"
    validation_result: str      # "valid" | "malformed" | "schema_mismatch"
    experimental: bool
```

The trace is kept separate from the feature content. Consumers can inspect what the agent returned (`output_hash`) and whether Ananta accepted it (`policy_decision`, `validation_result`) independently.

---

## Connection to VectorEncoding and CodeCompass

`AgentFeatureProvider` outputs can generate additional embedding records in `CodeCompassVectorStore`:

1. Provider produces `AgentFeatureOutput` with `feature_text`.
2. `EmbeddingProvider` embeds `feature_text` → raw float vector.
3. `VectorEncoder` encodes the vector via the active `VectorEncodingProfile`.
4. Record is stored with `source_scope=agent_feature` and `provider_id` in metadata.
5. `HybridOrchestrator` treats `agent_feature` records as secondary/fuzzy signals.

This means agent-derived knowledge enriches the vector index without granting the agent authority over ranking or retrieval decisions.

```json
{
  "record_id": "...",
  "source_scope": "agent_feature",
  "provider_id": "opencode-feature-v1",
  "model_or_agent_version": "opencode-0.1.202",
  "embedding_text": "This module handles payment retry logic with exponential backoff.",
  "encoded_vector": { ... }
}
```

---

## Approval and Policy Gates

| Gate | Behavior |
|---|---|
| `external_calls_allowed=False` (default) | Provider cannot reach external hosts; local-only mode |
| `allowed_provider_ids` empty | No external provider may be instantiated |
| `no_write_mode=True` (default) | Any write tool call raises `PolicyViolationError` |
| `allowed_paths` filter | Applied before dispatch AND after response |
| Schema validation | Outputs not matching `AgentFeatureOutput` are discarded |
| Approval gate for new providers | New `allowed_provider_ids` require explicit operator configuration |

---

## FeatureProvider Mode vs. Worker Execution Mode

| Aspect | FeatureProvider Mode | Worker Execution Mode |
|---|---|---|
| Writes files | Never | Yes, with approval |
| Executes code | Never | Yes, with approval |
| Output schema | Fixed (`AgentFeatureOutput`) | Task-dependent |
| Auth/secrets | Not exposed | Scoped to task |
| Ranking authority | None | N/A |
| Failure mode | Degraded signal, not blocking | Task failure |
| Trace | `AgentFeatureTrace` | Worker execution trace |

---

## What This Is Not

- Not a way to give an agent unrestricted repo access under a "read-only" label.
- Not a replacement for domain scope filtering in `CodeCompassVectorStore`.
- Not a chat interface to an agent.
- Not a way for agent output to override `HybridOrchestrator` ranking decisions.

---

## Related

- `worker/retrieval/vector_encoding.py` — encoding applied to agent feature vectors
- `worker/retrieval/codecompass_vector_store.py` — stores records with `source_scope=agent_feature`
- `docs/architecture/transformer-feature-provider.md` — specialized case for transformer models
- `architektur/uml/codecompass-vector-agent-feature-pipeline.mmd` — full pipeline diagram

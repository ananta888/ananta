# ADR: ContextCompressionAdapter Contract

**Status:** Accepted  
**Date:** 2026-06-22  
**Track:** HCCA-001 — context_compression_adapter_contract  
**Scope:** `agent/services/context_compression/`

---

## Context

Agent context windows in Ananta fill up quickly. Tool outputs repeat the same JSON structure dozens of times per session. RAG retrievals include full file excerpts when only a function signature was needed. Log blocks contain hundreds of DEBUG lines surrounding three relevant ERROR lines. When the LLM receives 12 000 tokens of noise to find 400 tokens of signal, plan quality degrades and latency increases.

The ContextManager already enforces a token budget model (see `docs/context-manager-target-model.md`) and priority tiers. But budget enforcement only drops low-priority blocks — it does not reduce the size of high-priority, compressible blocks that have already been admitted. A separate compression layer is needed that operates on individual content pieces *before* they are finalized in the context window.

---

## Decision

Define a **CompressionAdapter contract** that sits between context assembly and the final LLM call. Every piece of content that enters the context window passes through the adapter. The adapter decides — based on content type, task intent, and the active mode — whether to pass the content unchanged, compress it, or block it with a logged reason. The original content is always preserved in the local CCR Store so it can be retrieved if the LLM or downstream tooling needs it.

The feature is entirely opt-in at runtime. Disabling it or switching to `passthrough_with_metrics` mode requires no restart.

---

## Integration Modes

The adapter supports four operating modes, controlled by the `mode` field in the compression config:

| Mode | Behavior | Default |
|---|---|---|
| `off` | Adapter is fully disabled. Every `compress()` call returns the original content without measurement. | No |
| `passthrough_with_metrics` | Content is never modified. Token counts and compression eligibility are measured and emitted as tracking events, but nothing is stored or changed. This is the safe rollout default. | **Yes** |
| `compress` | Eligible content is compressed using the configured adapter. QualityGuard validates the result; fallback to passthrough if quality is insufficient. Original is stored in CCR Store with TTL. | No |
| `compress_aggressive` | Like `compress`, but with a higher target reduction percentage (default 55% vs 35%) and a lower minimum quality score (0.6 vs 0.7). Suitable for very large log or search-result blocks where lossy reduction is acceptable. | No |

---

## Contract Types

### `CompressionRequest`

Passed by the caller to `adapter.compress()`. All fields are read by the PolicyEngine before any compression is attempted.

| Field | Type | Semantics |
|---|---|---|
| `content` | `str` | The raw text content to be considered for compression. Must be the complete block; partial slices produce unreliable quality checks. |
| `content_type` | `str` | Semantic label of the content: `"tool_output"`, `"json"`, `"log"`, `"search_results"`, `"rag_results"`, `"codecompass_symbol_list"`, `"old_chat_summary"`, `"current_user_message"`, `"active_patch"`, `"credential"`, `"secret"`, `"approval_prompt"`. Unknown types default to the generic compressor strategy. |
| `task_intent` | `str` | The intent of the active task, e.g. `"coding"`, `"debug"`, `"review"`, `"security_audit"`, `"general"`. Used by the PolicyEngine to protect sensitive task types from compression side-effects. |
| `budget_tokens` | `int` | Optional token budget hint. If `> 0` and the content already fits within the budget, the content is not eligible (below-threshold gate). Set to `0` to disable threshold gating. |
| `content_id` | `str` | Optional stable identifier for this content piece (e.g. task ID + block index). Used by the ABRouter for deterministic routing. Defaults to a hash of the content. |
| `sensitivity_label` | `str` | Pre-computed sensitivity classification from the ContextAccessPolicy layer: `"safe"`, `"sensitive"`, `"secret"`, `"unknown"`. Content labelled `"secret"` is never compressed or stored. |
| `metadata` | `dict` | Arbitrary caller metadata passed through unchanged to the `CompressionResult.diagnostics` field. |

### `CompressionResult`

Returned by every `adapter.compress()` call, including passthrough.

| Field | Type | Semantics |
|---|---|---|
| `content` | `str` | The content to use in the LLM context window. Identical to `CompressionRequest.content` when decision is `"passthrough"`. |
| `decision` | `str` | What happened: `"passthrough"` (no change), `"compressed"` (compression accepted), `"blocked"` (policy gate fired), `"truncated"` (budget enforcement only). |
| `reason_code` | `str` | Machine-readable reason for the decision. Examples: `"disabled"`, `"content_type_blocked"`, `"task_intent_protected"`, `"sensitivity_blocked"`, `"below_token_threshold"`, `"mode_passthrough_only"`, `"quality_ok"`, `"fallback_on_risk"`. |
| `ccr_ref` | `str` | Reference key for the original content in the CCR Store. Empty string when nothing was stored (passthrough or blocked decisions). Use this ref with `CCRRetrievalTool` to fetch the original. |
| `token_before` | `int` | Estimated token count of the input content (chars / 4). Always populated, even on passthrough. |
| `token_after` | `int` | Estimated token count of the output `content`. Equal to `token_before` on passthrough. |
| `quality_score` | `float` | QualityGuard score (0.0–1.0). `0.0` when not applicable (passthrough, blocked). Values below `min_quality_score` (default 0.7) cause fallback to passthrough. |
| `adapter_used` | `str` | Which adapter implementation handled the request: `"passthrough"`, `"ananta_smart_compressor"`, `"external_headroom"`. |
| `elapsed_ms` | `float` | Wall-clock time for the full compression pipeline in milliseconds. Useful for detecting compression overhead in latency-sensitive paths. |
| `diagnostics` | `dict` | Merged caller metadata plus internal diagnostics: strategy used by SmartCompressor, QualityGuard check breakdown, ABRouter lane. |

---

## Policy Gates

The `CompressionPolicyEngine` evaluates each `CompressionRequest` through a strict gate sequence. Gates are evaluated in order; the first block terminates evaluation.

### NEVER compressed — hard block

These content types are blocked regardless of mode, token count, or task intent:

| Content type | Reason |
|---|---|
| `current_user_message` | User message must reach the LLM verbatim. Any compression would silently change the user's instructions. |
| `active_patch` / `diff` | Patches contain semantically significant whitespace and line numbers. Compression corrupts them. |
| `credential` | Credential values must not be compressed, stored, or transformed. |
| `secret` | Same as credential. SecretRedactor label `"secret"` also triggers this gate. |
| `approval_prompt` | Approval prompts must be auditable and unmodified. |

### PROTECTED by task intent

When `task_intent` matches one of the protected intents, content of *any* compressible type is blocked from compression for that request. This prevents compression from subtly altering the context that a debugger or reviewer relies on.

| Task intent | Reason |
|---|---|
| `debug` | Tool output and logs are diagnostic evidence. Compression must not remove lines. |
| `fix` | Patch context must be exact. |
| `review` | Reviewer context must be complete. |
| `security_audit` | No lossy transformation in security-relevant analysis flows. |
| `test_failure` | Stack traces and test output must survive intact. |

### Compressible — eligible for compression

Content types on the allow-list that are not blocked by other gates:

`tool_output`, `json`, `log`, `search_results`, `rag_results`, `old_chat_summary`, `codecompass_symbol_list`

Content types not on this list and not on the NEVER list pass through with reason code `content_type_not_compressible`.

### Token threshold gate

Content with an estimated token count below `max_input_tokens_before_considering` (default: 1200) is not compressed. Compressing small blocks adds overhead without meaningful savings.

### Mode gate

In `passthrough_with_metrics` mode, all content passes through after measurement. Metrics are emitted; no storage occurs.

---

## CCR Store

The **Compressed Content Reference Store** (HCCA-005) is a local, content-addressable store that preserves the original content whenever compression is applied.

**Why keep the original:** Compression is lossy by design for some content types. When the LLM needs the full tool output — for example, to cite a specific line number or verify a file path — the compressed version in context contains a reference (`ccr_ref`) that the agent can pass to `CCRRetrievalTool` to fetch the full original.

**How refs work:** The store computes SHA-256 over the UTF-8 content and uses the first 24 hex characters as the ref. Refs are stable across sessions for identical content (content-addressed). A lightweight `.index.json` tracks metadata (content type, TTL, byte size, redaction flag) without reading the data files.

**TTL:** Default 72 hours. Configurable via `ccr_store.ttl_hours`. The `CCRStore.expire_old()` method cleans up expired entries; it should be called periodically (e.g. on agent startup or via a background job).

**Size limit:** Each entry is capped at 5 MiB (`max_bytes_per_item`). Content exceeding this limit raises `ValueError` at store time; the adapter catches this and falls back to passthrough.

**Retrieval path:** `CCRRetrievalTool.retrieve(CCRRetrievalRequest(ref=..., requester_id=...))` returns a `CCRRetrievalResult`. The tool supports an `allowed_content_types` filter for callers that want to restrict what they can retrieve.

**SecretRedactor integration:** Before content is written to the CCR Store, the SecretRedactor runs. If secrets are detected, they are redacted in the stored copy and `CCREntry.redacted=True` is set. The LLM context receives the redacted compressed form; the CCR Store also stores the redacted form — never the raw secret.

---

## Adapter Implementations

Three implementations of the adapter contract are available:

### `passthrough`

The simplest implementation. Returns the original content unchanged. Token counts are measured and emitted. No compression is attempted, no CCR storage occurs. Used in `off` and `passthrough_with_metrics` modes.

### `ananta_smart_compressor`

The primary production adapter (HCCA-007). Pure Python, no LLM calls, no external dependencies. Routes each request to a content-type-specific strategy:

- **`json` strategy:** Parses the JSON, prunes null/empty values, truncates deep nested objects at depth 4, truncates string values over 200 characters, removes duplicate list items, then re-serializes with compact separators.
- **`log` strategy:** Drops DEBUG/TRACE/VERBOSE lines, deduplicates consecutive identical lines, always preserves lines matching ERROR / EXCEPTION / TRACEBACK / CRITICAL / WARNING patterns. Falls back to head (60%) + tail (20%) with omission marker if still exceeding target.
- **`search_results` / `rag_results` / `codecompass_symbol_list` strategy:** Deduplicates path/symbol lines, truncates lines over 300 characters, applies top-N budget if target not reached.
- **`generic` (fallback):** Collapses consecutive blank lines (max 1), deduplicates repeated 3-line paragraphs, applies head (40%) + tail (20%) with omission marker.

After compression, `QualityGuard` validates the result. If the score is below `min_quality_score`, the adapter falls back to passthrough (unless `fallback_on_quality_risk=False`).

### `external_headroom`

An optional adapter (HCCA-012) that delegates compression to an external Headroom CLI or HTTP service. **Disabled by default.** Requires explicit `external_headroom.enabled=true` in config. Supports three transports: `cli` (subprocess via stdin/stdout JSON), `http` (HTTP POST to `base_url`), and `mcp` (not yet implemented). If the external service is unavailable or returns an error, the adapter falls back to passthrough transparently.

---

## Consequences

**Token savings.** In `compress` mode with the `ananta_smart_compressor`, typical savings are 25–45% on log and search-result content. JSON pruning saves 15–30% on dense tool outputs. Small content blocks (below the token threshold) and protected content types are never touched.

**Audit trail.** Every compression attempt emits a structured `CompressionEvent` to the `ananta.compression` logger and the in-memory ring buffer. The `CompressionTracker.summary()` method returns aggregate statistics: `total_requests`, `total_compressed`, `total_blocked`, `total_token_savings`, `avg_quality_score`.

**Safety invariants.** The policy gate sequence is deterministic and side-effect-free. A gate block returns immediately without touching the content. The QualityGuard fallback ensures that compression never silently degrades output quality below the configured threshold. The CCR Store never holds raw secrets (SecretRedactor runs before storage).

**Operational simplicity.** The adapter can be disabled at runtime by setting `enabled=false` or `mode=passthrough_with_metrics`. No restart required when config is loaded dynamically. No data is lost on disable — the CCR Store retains entries until TTL expiry.

---

## Python Usage

```python
from agent.services.context_compression import build_compression_adapter, CompressionRequest

adapter = build_compression_adapter(config)

result = adapter.compress(CompressionRequest(
    content=rag_output,
    content_type="rag_results",
    task_intent="general",
    budget_tokens=800,
))

context_text = result.content  # may be original if passthrough
ccr_ref = result.ccr_ref       # empty string if nothing stored
```

To retrieve the original later:

```python
from agent.services.context_compression import CCRRetrievalTool, CCRRetrievalRequest

retrieval_tool = CCRRetrievalTool(ccr_store=adapter.ccr_store)
retrieval_result = retrieval_tool.retrieve(CCRRetrievalRequest(
    ref=ccr_ref,
    requester_id="worker-abc123",
))
if retrieval_result.found:
    full_content = retrieval_result.content
```

---

## Full Configuration Example

```python
compression_config = {
    # Master switch — set to False to disable entirely
    "enabled": True,

    # Operating mode: "off" | "passthrough_with_metrics" | "compress" | "compress_aggressive"
    "mode": "passthrough_with_metrics",

    # Which adapter to use when mode requires compression
    # "passthrough" | "ananta_smart_compressor" | "external_headroom"
    "adapter": "ananta_smart_compressor",

    # Policy engine settings
    "policy": {
        "never_compress_types": [
            "current_user_message",
            "active_patch",
            "credential",
            "secret",
            "approval_prompt",
        ],
        "protect_task_intents": [
            "debug", "fix", "review", "security_audit", "test_failure"
        ],
        "compressible_types": [
            "tool_output", "json", "log", "search_results",
            "rag_results", "old_chat_summary", "codecompass_symbol_list",
        ],
        # Minimum estimated tokens before compression is even considered
        "max_input_tokens_before_considering": 1200,
        # Target character reduction percentage passed to SmartCompressor
        "target_reduction_percent": 35.0,
        # Fall back to passthrough when QualityGuard score is too low
        "fallback_on_quality_risk": True,
        # QualityGuard minimum score (0.0–1.0)
        "min_quality_score": 0.7,
    },

    # CCR Store settings
    "ccr_store": {
        # Local filesystem path for the store (required when compression is active)
        "path": "/var/ananta/ccr_store",
        # Hours before an entry expires and becomes unresolvable
        "ttl_hours": 72,
        # Maximum size per stored item (bytes)
        "max_bytes_per_item": 5242880,
        # Run SecretRedactor before storing originals
        "redact_secrets": True,
    },

    # A/B router for phased rollout experiments
    "ab_router": {
        "enabled": False,
        "strategy_a": "passthrough",
        "strategy_b": "ananta_smart_compressor",
        # Percentage of requests routed to strategy B (0–100)
        "rollout_percent_b": 0.0,
        # Seed for deterministic routing hash
        "seed": 42,
    },

    # External Headroom adapter — disabled by default
    "external_headroom": {
        "enabled": False,
        "transport": "cli",         # "cli" | "http"
        "command": ["headroom"],    # CLI transport: subprocess command
        "base_url": "",             # HTTP transport: endpoint URL
        "timeout_seconds": 20.0,
    },

    # CompressionTracker settings
    "tracking": {
        "emit_to_log": True,
        "include_in_tracking_viewer": True,
    },
}
```

---

## Security Invariants

1. `current_user_message` is never compressed or stored — enforced by the NEVER list in `CompressionPolicyEngine`.
2. `active_patch` and `diff` content types are never compressed in `debug`, `fix`, or `review` task intents — enforced by policy gate 4.
3. `SecretRedactor` runs before any content is written to the CCR Store — enforced in the adapter pipeline before `CCRStore.store()` is called.
4. The CCR Store is local filesystem only — `CCRStore` uses `pathlib.Path`, no network access.
5. `external_headroom.enabled` defaults to `False` — must be explicitly opted into.
6. `QualityGuard` blocks acceptance of any compressed output that scores below `min_quality_score` — falls back to passthrough, never silently accepts degraded output.
7. Every compression step emits a `CompressionEvent` to the `ananta.compression` logger — no silent compressions.
8. Compression is always optional — setting `enabled=false` or `mode=passthrough_with_metrics` requires no restart and causes no data loss.

---

## Related

- `agent/services/context_compression/policy_engine.py` — HCCA-004
- `agent/services/context_compression/token_estimator.py` — HCCA-003
- `agent/services/context_compression/smart_compressor.py` — HCCA-007
- `agent/services/context_compression/quality_guard.py` — HCCA-008
- `agent/services/context_compression/ccr_store.py` — HCCA-005
- `agent/services/context_compression/ccr_retrieval_tool.py` — HCCA-016
- `agent/services/context_compression/secret_redactor.py` — HCCA-006
- `agent/services/context_compression/ab_router.py` — HCCA-014
- `agent/services/context_compression/external_headroom_adapter.py` — HCCA-012
- `agent/services/context_compression/tracking.py` — HCCA-015
- `docs/context-manager-target-model.md` — upstream token budget model
- `docs/context_access_policy_backend.md` — upstream sensitivity labelling
- `docs/architecture/context-compression-security.md` — HCCA-017 security part
- `docs/release/context-compression-rollout.md` — HCCA-017 rollout plan

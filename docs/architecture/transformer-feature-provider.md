# ADR: TransformerFeatureProvider

**Status:** Proposed  
**Date:** 2026-06-22  
**Author:** Ananta Architecture  
**Relates to:** `docs/architecture/codecompass-vector-encoding.md`, `docs/architecture/agent-feature-provider.md`

---

## Context

CodeCompass currently produces embeddings via `EmbeddingProvider`, which wraps an external or local embedding API call. This works well for text similarity retrieval but does not capture intermediate transformer representations (hidden states, attention patterns, layer-wise activations) that could serve as richer feature signals for ranking, clustering, or anomaly detection.

A naive approach would be to route transformer inference through the same LLM chat-completion path. This is wrong: it conflates free-text generation with structured feature extraction, removes auditability, and introduces unpredictable output shapes.

The `TransformerFeatureProvider` abstraction solves this by defining a strict interface: transformers are used only as feature extractors, never as decision makers.

---

## Decision

Introduce `TransformerFeatureProvider` as a separate abstraction from `EmbeddingProvider` and from all chat/generation paths.

---

## Separation from ChatCompletion

| Concern | ChatCompletion / LLM Generation | TransformerFeatureProvider |
|---|---|---|
| Output type | Free-text answer | Structured numeric features / scores |
| Downstream use | Presented to user | Consumed by deterministic rules |
| Replaces policy? | Must not | Must not |
| Replaces approval gates? | Must not | Must not |
| Auditable output shape | No (free text) | Yes (fixed schema) |
| Trace shows model output? | Yes (plain text) | Yes (feature vector + metadata) |

The provider **must never** return a free-text answer that is used to bypass policy, allowed paths, or approval gates.

---

## Policy Gates

Every `TransformerFeatureProvider` instance is constructed with a `PolicyScope` that enforces hard limits before any model call is made:

```python
@dataclass(frozen=True)
class TransformerFeaturePolicy:
    local_only: bool = True
    allowed_base_urls: frozenset[str] = frozenset()
    model_name: str = ""
    layer_selector: str = "last"        # last | pooled | all | layer:<n>
    max_input_tokens: int = 512
    no_write_mode: bool = True
    external_calls_allowed: bool = False
```

**Enforcement rules:**

- `local_only=True` (default): no HTTP calls to external hosts are made; only local inference endpoints (localhost, 127.0.0.1, unix sockets) are permitted.
- `allowed_base_urls`: if `local_only=False`, only URLs in this set may be called. Empty set with `local_only=False` blocks all calls.
- `model_name`: required; empty string is rejected at construction time.
- `layer_selector`: selects which transformer layer outputs are extracted. Validated against known selectors; arbitrary strings are rejected.
- `max_input_tokens`: input is truncated or rejected before dispatch; never silently overflows.
- `no_write_mode=True` (default): the provider must not write files, emit tool calls, or modify repo state.
- `external_calls_allowed=False` (default): any URL not matching `allowed_base_urls` causes `PolicyViolationError`, not a silent skip.

Policy violations are raised as `PolicyViolationError` with a machine-readable `reason` field. They are never silently swallowed.

---

## Reproducible Metadata

Every feature extraction result includes a `TransformerFeatureMetadata` record:

```python
@dataclass(frozen=True)
class TransformerFeatureMetadata:
    provider_id: str             # stable identifier for this provider instance
    model_version: str           # exact model name + version string
    layer: str                   # which layer was extracted
    pooling: str                 # mean | cls | max | none
    dimensions: int              # output dimensionality
    input_hash: str              # SHA-256 of normalized input text
    config_hash: str             # SHA-256 of policy + model config
    elapsed_ms: float
    experimental: bool
```

This metadata is stored alongside every encoded vector in `CodeCompassVectorStore`. On index rebuild, `config_hash` change triggers a full re-extraction.

The `input_hash` allows verifying that two feature extractions used the same input, independent of when or where they ran.

---

## Output Contract

`TransformerFeatureProvider.extract(text: str) -> TransformerFeatureResult`:

```python
@dataclass(frozen=True)
class TransformerFeatureResult:
    feature_vector: list[float]         # the extracted embedding/hidden state
    feature_scores: dict[str, float]    # optional named scalar scores
    metadata: TransformerFeatureMetadata
    diagnostics: dict[str, Any]         # compression ratio, max_abs_error after encoding
    policy_decision: str                # "allowed" | "blocked:<reason>"
```

If the policy blocks the call, `feature_vector` is empty, `policy_decision` is `"blocked:<reason>"`, and no network call is made.

---

## Disabled Mode

`TransformerFeatureProvider` can be completely disabled by setting `enabled=False` in configuration:

```python
CODECOMPASS_TRANSFORMER_FEATURE_PROVIDER_ENABLED=false
```

When disabled:
- No model calls are made.
- `CodeCompassVectorStore` uses `EmbeddingProvider` vectors only.
- All existing CodeCompass retrieval paths continue to work unchanged.
- No import errors or startup failures occur.

The disable path is the default. The provider is opt-in.

---

## Deterministic Downstream Processing

```
TransformerFeatureProvider
  → structured feature_vector + feature_scores
  → VectorEncodingProfile (same encoding pipeline as EmbeddingProvider)
  → VectorEncoder
  → CodeCompassVectorStore (stored as source_scope=transformer_feature)
  → VectorSearchEngine (cosine similarity, same as normal embeddings)
  → HybridOrchestrator (weights transformer features as secondary signal)
  → deterministic rerank rules
  → ContextTrace (shows feature, score, decision separately)
  → PromptContext
```

The transformer creates only features and scores. All routing, ranking, approval and policy decisions run via deterministic rules in `HybridOrchestrator`. The model never decides what context reaches the prompt.

---

## Trace Format

The `ContextTrace` entry for a transformer-feature-sourced result looks like:

```json
{
  "trace_id": "...",
  "source_scope": "transformer_feature",
  "provider_id": "transformer-feature-local-v1",
  "model_feature": {
    "dimensions": 768,
    "layer": "last",
    "input_hash": "a3b4c5...",
    "experimental": false
  },
  "deterministic_score": {
    "cosine_similarity": 0.87,
    "hybrid_rank": 3,
    "domain_scope_passed": true
  },
  "final_decision": {
    "included_in_context": true,
    "reason": "hybrid_rank_threshold_passed"
  }
}
```

Model feature, deterministic score, and final decision are always separate fields. Inspecting the trace shows exactly which part came from the model and which came from deterministic rules.

---

## What This Is Not

- Not a chat completion path.
- Not a replacement for `EmbeddingProvider` (it supplements it).
- Not a way for a model to write files or execute code.
- Not a way for model output to override domain scope boundaries.
- Not a prerequisite for any current CodeCompass retrieval path.

---

## Related

- `worker/retrieval/vector_encoding.py` — VectorEncodingProfile used by this provider
- `docs/architecture/agent-feature-provider.md` — generalization of this pattern to arbitrary agents
- `docs/architecture/codecompass-turboquant-scope.md` — scope boundaries
- `architektur/uml/codecompass-vector-encoding-transformer-pipeline.mmd` — pipeline diagram

# Release: Context Compression Rollout Plan

**Status:** Draft  
**Date:** 2026-06-22  
**Track:** HCCA-017 (rollout part)  
**Scope:** `agent/services/context_compression/`  
**Owner:** Ananta Release

---

## Rollout Philosophy

Context compression changes what the LLM sees. An undetected quality regression — where the LLM loses a critical line from a stack trace or receives corrupted JSON — is worse than no compression at all. Therefore:

1. Phase 1 is metrics-only by default. No content is ever modified until operators have seen real token counts from their workloads.
2. Every phase transition requires documented quality gate results. No promotion on assumption.
3. Rollback is a single config change. No schema migration, no data loss.
4. Aggressive modes are introduced only after conservative modes have been stable.

---

## Phase 1 — Metrics Only (Safe Default)

**Goal:** Ship the feature. Collect real token data. Zero behavioral change.

**Configuration:**

```python
compression_config = {
    "enabled": True,
    "mode": "passthrough_with_metrics",
    "adapter": "passthrough",
    "ccr_store": {
        "path": "/var/ananta/ccr_store",
        "ttl_hours": 72,
        "redact_secrets": True,
    },
    "ab_router": {"enabled": False},
    "external_headroom": {"enabled": False},
    "tracking": {
        "emit_to_log": True,
        "include_in_tracking_viewer": True,
    },
}
```

**What happens:** Every `CompressionRequest` passes through unchanged. The `TokenEstimator` measures token counts before and after (identical in passthrough), the `CompressionPolicyEngine` evaluates eligibility, and a `CompressionEvent` is emitted to the `ananta.compression` logger with `decision="passthrough"` and `reason_code="mode_passthrough_only"`. No content is modified, no CCR Store entries are written.

**What to observe:**

- `token_before` distribution across content types — identify which content types have the highest token counts.
- `reason_code` distribution — understand what fraction of requests would be eligible (`"mode_passthrough_only"`) vs blocked by policy (`"content_type_blocked"`, `"task_intent_protected"`, `"below_token_threshold"`).
- `passthrough_rate` — proportion of requests that would have been compressed if mode were `compress`.

Observe via:
```python
summary = tracker.summary()
# {
#   "total_requests": ...,
#   "total_compressed": 0,      # always 0 in phase 1
#   "total_blocked": ...,
#   "total_token_savings": 0,   # always 0 in phase 1
#   "avg_quality_score": 0.0,
# }
```

**Release criteria for Phase 1:**

- [ ] All HCCA unit tests green: `tests/test_context_compression_core.py`
- [ ] `mode=passthrough_with_metrics` produces no behavioral change in integration tests
- [ ] `CompressionTracker.summary()` returns correct totals
- [ ] `CCRStore` with `redact_secrets=True` stores no raw secrets in any test run
- [ ] Diagnostics contain no API keys, auth headers, or PEM blocks

---

## Phase 2 — Compress Mode, 5% A/B Rollout

**Goal:** Measure real token savings and quality scores on a small slice of traffic. Validate SmartCompressor performance on production content types.

**Trigger:** Phase 1 has been stable for at least 5 working days. `total_requests` has accumulated enough data to compute meaningful statistics (target: ≥ 500 compression-eligible requests observed).

**Configuration:**

```python
compression_config = {
    "enabled": True,
    "mode": "compress",
    "adapter": "ananta_smart_compressor",
    "policy": {
        "never_compress_types": [
            "current_user_message", "active_patch", "credential",
            "secret", "approval_prompt",
        ],
        "protect_task_intents": [
            "debug", "fix", "review", "security_audit", "test_failure",
        ],
        "compressible_types": [
            "tool_output", "json", "log", "search_results",
            "rag_results", "old_chat_summary", "codecompass_symbol_list",
        ],
        "max_input_tokens_before_considering": 1200,
        "target_reduction_percent": 35.0,
        "fallback_on_quality_risk": True,
        "min_quality_score": 0.7,
    },
    "ccr_store": {
        "path": "/var/ananta/ccr_store",
        "ttl_hours": 72,
        "redact_secrets": True,
    },
    "ab_router": {
        "enabled": True,
        "strategy_a": "passthrough",
        "strategy_b": "ananta_smart_compressor",
        "rollout_percent_b": 5.0,
        "seed": 42,
    },
    "tracking": {
        "emit_to_log": True,
        "include_in_tracking_viewer": True,
    },
}
```

**What happens:** 5% of eligible compression requests are routed to the `ananta_smart_compressor`. The other 95% remain on passthrough. Both lanes emit `CompressionEvent` records. The `adapter_used` field distinguishes them. This allows direct comparison of token savings and quality scores between lanes without affecting the majority of traffic.

**What to measure:**

| Metric | Source | Target threshold |
|---|---|---|
| `token_delta` (lane B) | `CompressionEvent.token_delta` | Mean > 0 (savings observed) |
| `avg_quality_score` (lane B) | `CompressionTracker.summary()` | ≥ 0.75 |
| `fallback_on_risk` rate | Events with `reason_code="fallback_on_risk"` | < 15% of lane B attempts |
| CodeCompass Recall@5 | Integration test benchmark | Not degraded vs baseline |
| LLM plan quality score | Integration test benchmark | Not degraded vs baseline |

**Quality gates before Phase 3:**

- [ ] Mean `token_delta` on lane B requests is positive (compression saves tokens)
- [ ] `avg_quality_score` ≥ 0.75 across all content types in lane B
- [ ] No integration test failures attributable to compression (check by disabling and re-running)
- [ ] CodeCompass Recall@5 for `rag_results` and `codecompass_symbol_list` content types not degraded beyond ±2% vs Phase 1 baseline
- [ ] `fallback_on_risk` rate ≤ 15% for each content type individually
- [ ] CCR Store expiry working: entries older than TTL are evicted on next `expire_old()` call
- [ ] No secrets detected in CCR Store data files (automated scan of store directory)

---

## Phase 3 — Full Rollout, Expand Content Types, Tune Thresholds

**Goal:** Enable compression for all eligible traffic. Expand the compressible types list based on Phase 2 data. Tune `target_reduction_percent` and `min_quality_score` per content type if the SmartCompressor supports per-type config.

**Trigger:** All Phase 2 quality gates met with documented results.

**Configuration changes from Phase 2:**

```python
# Disable A/B routing — route all eligible traffic to SmartCompressor
"ab_router": {
    "enabled": False,
},

# Optionally expand compressible_types if Phase 2 data shows safe candidates
# e.g. add "build_output" if it was observed in Phase 2 metrics
"policy": {
    ...
    "compressible_types": [
        "tool_output", "json", "log", "search_results",
        "rag_results", "old_chat_summary", "codecompass_symbol_list",
        # Add new types only after Phase 2 data supports it
    ],
},

# Mode stays "compress" — no change needed
"mode": "compress",
```

**Tuning guidance:**

- If `avg_quality_score` for `json` content is consistently ≥ 0.90, consider lowering `min_quality_score` for JSON-only paths to 0.65 to allow more aggressive pruning.
- If `fallback_on_risk` rate for `log` content is > 20%, the SmartCompressor's log strategy may be too aggressive for the workload. Reduce `target_reduction_percent` to 25% for `log` or increase `min_quality_score` to 0.80.
- Re-run CodeCompass Recall@5 benchmark after any threshold change.

**Quality gates before Phase 4 (if pursued):**

- [ ] Phase 3 stable for ≥ 10 working days with no quality regressions
- [ ] `avg_quality_score` ≥ 0.75 across all content types at full rollout volume
- [ ] No operator-reported LLM quality issues attributable to compression in that period
- [ ] CCR Store `total_live_bytes` stays within acceptable disk usage limits

---

## Phase 4 (Optional) — External Headroom Adapter

**Goal:** Optionally delegate compression to teams that have the Headroom CLI or service installed locally, enabling more sophisticated semantic compression algorithms beyond the SmartCompressor's deterministic strategies.

**Trigger:** Phase 3 stable. Team has Headroom installed locally and has tested the CLI/HTTP integration in a non-production environment.

**Configuration:**

```python
"external_headroom": {
    "enabled": True,
    "transport": "cli",          # start with CLI transport only
    "command": ["headroom"],     # or full path: ["/usr/local/bin/headroom"]
    "timeout_seconds": 20.0,
},
# Keep ananta_smart_compressor as the primary adapter
# ExternalHeadroomAdapter is used only when explicitly selected via ab_router or adapter config
```

**Safety requirements before enabling:**

- [ ] Headroom CLI confirmed local-only (no outbound network calls in its operation)
- [ ] `ExternalHeadroomAdapter.health_check()` returns `{"available": true}` in test environment
- [ ] Timeout set to ≤ 20 seconds to prevent blocking the context assembly path
- [ ] Fallback to passthrough confirmed working by simulating CLI unavailability (`command=["false"]`)
- [ ] No secrets or PII in content types sent to the external adapter (verify content type filter)

**This phase is not required.** Most workloads will be well-served by the `ananta_smart_compressor` in Phase 3. Phase 4 is for advanced users with specific semantic compression requirements.

---

## Rollback

Rollback is a single config change. No data migration, no data loss.

### Immediate rollback — disable all compression

```python
compression_config["enabled"] = False
# OR
compression_config["mode"] = "passthrough_with_metrics"
```

This takes effect on the next agent restart (or immediately if config is loaded dynamically). All subsequent `compress()` calls return passthrough. CCR Store entries are retained until their TTL expires — they are not deleted on rollback.

### Rollback from Phase 3 to Phase 2 (A/B routing)

```python
compression_config["ab_router"] = {
    "enabled": True,
    "strategy_a": "passthrough",
    "strategy_b": "ananta_smart_compressor",
    "rollout_percent_b": 5.0,
    "seed": 42,
}
```

### Rollback from Phase 2 to Phase 1 (passthrough only)

```python
compression_config["mode"] = "passthrough_with_metrics"
compression_config["ab_router"]["enabled"] = False
```

### Verify rollback succeeded

```python
summary = tracker.summary()
assert summary["total_compressed"] == 0
assert summary["total_token_savings"] == 0
```

---

## Monitoring

Track the following metrics via `CompressionTracker.summary()` and the `ananta.compression` logger:

| Metric | How to observe | Alerting threshold |
|---|---|---|
| `total_token_savings` | Cumulative token delta across compressed events | N/A (informational) |
| `avg_quality_score` | Mean quality score of compressed events | Alert if < 0.70 over any 1h window |
| `passthrough_rate` | `total_requests - total_compressed / total_requests` | Alert if > 95% after Phase 3 (compression not firing) |
| `fallback_on_risk` rate | Count of events with `reason_code="fallback_on_risk"` | Alert if > 20% of eligible requests |
| CCR Store disk usage | `CCRStore.diagnostics()["total_live_bytes"]` | Alert if > 500 MiB |
| `ccr_store.live_entries` | `CCRStore.diagnostics()["live_entries"]` | Alert if > 50 000 (index bloat) |

Log format: every `CompressionEvent` is emitted as a JSON line to logger `ananta.compression`. Key fields for dashboards:

```json
{
  "event_type": "compression_completed",
  "content_type": "log",
  "decision": "compressed",
  "reason_code": "quality_ok",
  "token_before": 3200,
  "token_after": 1920,
  "token_delta": 1280,
  "quality_score": 0.83,
  "adapter_used": "ananta_smart_compressor",
  "elapsed_ms": 12.4
}
```

---

## Known Risks

**Over-compression of dense JSON.** The `_compress_json` strategy prunes null/empty values and caps string length at 200 characters. JSON payloads where long string values are semantically significant (e.g. base64-encoded file content embedded in a tool response) may lose critical data. Mitigation: ensure such content types are added to `never_compress_types` or not included in `compressible_types`.

**Log deduplication edge cases.** The `_compress_log` strategy deduplicates consecutive identical lines. Structured logs where the same error fires repeatedly may be collapsed to a single line, losing the count of occurrences. The count is not preserved in the current implementation. Mitigation: if occurrence count matters, use `content_type="json"` with structured error objects instead of raw log text.

**JSON objects with ordering semantics.** The `_compress_json` strategy serialises with compact separators (`json.dumps(obj, separators=(",", ":"))`) which does not preserve key ordering in Python < 3.7. Python 3.7+ preserves dict insertion order. If the LLM is sensitive to JSON key order (e.g. prompt contracts that rely on field position), validate this does not apply to your content types.

**Token estimation accuracy.** `TokenEstimator` uses 4 characters per token as a conservative approximation. Actual token counts vary by model and tokeniser. The threshold `max_input_tokens_before_considering=1200` is a character-based estimate, not a true tiktoken count. For models with non-standard tokenisers (e.g. CJK-heavy content), actual token counts may be significantly different.

---

## Release Checklist

### Before Phase 1 deployment

- [ ] All HCCA unit tests pass: `pytest tests/test_context_compression_core.py -v`
- [ ] `SecretRedactor` unit tests pass for all 11 pattern types
- [ ] `CCRStore.expire_old()` called on agent startup (bootstrap hook confirmed)
- [ ] `docs/architecture/context-compression-adapter.md` reviewed and accurate
- [ ] `docs/architecture/context-compression-security.md` reviewed and security invariants verified
- [ ] `architektur/uml/context-compression-pipeline.mmd` diagram reviewed and matches implementation
- [ ] Config schema documentation matches `CompressionPolicy.from_config()` accepted keys

### Before Phase 2 deployment (A/B rollout)

- [ ] Phase 1 quality gates met (see above)
- [ ] Phase 2 config reviewed by second person
- [ ] CCR Store disk quota confirmed with ops team
- [ ] Log ingestion pipeline confirmed to handle `ananta.compression` JSON lines
- [ ] Integration test suite run with `mode=compress` and all tests pass

### Before Phase 3 deployment (full rollout)

- [ ] All Phase 2 quality gates met with documented benchmark results
- [ ] `fallback_on_risk` rate documented per content type
- [ ] CodeCompass Recall@5 benchmark results documented
- [ ] Rollback steps tested in staging environment
- [ ] CCR Store TTL eviction confirmed working under load

### Before Phase 4 deployment (external_headroom)

- [ ] Headroom CLI version pinned and checksummed
- [ ] `ExternalHeadroomAdapter.health_check()` returns `available=true` in production environment
- [ ] Data handling terms of Headroom service reviewed if HTTP transport used
- [ ] Timeout and fallback behaviour confirmed under simulated unavailability

---

## Related

- `agent/services/context_compression/` — full implementation
- `docs/architecture/context-compression-adapter.md` — adapter contract (HCCA-001)
- `docs/architecture/context-compression-security.md` — threat model and security invariants (HCCA-017 security)
- `architektur/uml/context-compression-pipeline.mmd` — pipeline diagram
- `tests/test_context_compression_core.py` — unit tests
- `docs/release/codecompass-vector-encoding-rollout.md` — reference rollout plan for a comparable feature

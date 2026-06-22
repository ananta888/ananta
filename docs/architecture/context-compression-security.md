# Context Compression — Security Checklist and Threat Model

**Status:** Accepted  
**Date:** 2026-06-22  
**Track:** HCCA-017 (security part)  
**Scope:** `agent/services/context_compression/`

---

## Scope

This document covers the security properties of the ContextCompressionAdapter pipeline. It does not cover general Ananta secret handling, the ContextAccessPolicy backend, or LLM provider key management. See `docs/context_access_policy_backend.md` and `docs/security/context-release-gates.md` for those.

---

## Threat Model

### Threat 1: Secrets in Compressed Context

**Risk:** A `CompressionRequest` whose content contains an API key, password, or PEM private key is compressed and the resulting `CompressionResult.content` — which goes directly into the LLM context window — still contains the raw secret. The LLM may repeat the secret in its output or include it in a tool call.

**Mitigation: SecretRedactor (HCCA-006)**

`SecretRedactor` runs in the adapter pipeline before any content is written to the CCR Store. It also runs as a check on the compressed output via QualityGuard's `no_new_secrets_introduced` check.

Patterns detected and redacted (replacements appear as `[REDACTED:<name>]`):

| Pattern name | What it matches |
|---|---|
| `OPENAI_API_KEY` | `sk-` followed by 32+ alphanumeric characters |
| `ANTHROPIC_API_KEY` | `sk-ant-` followed by 32+ alphanumeric/hyphen/underscore characters |
| `BEARER_TOKEN` | `Bearer ` followed by 20+ token characters |
| `PASSWORD_FIELD` | `password:` or `password=` followed by 6+ non-whitespace characters (case-insensitive) |
| `PEM_PRIVATE_KEY` | `-----BEGIN ... PRIVATE KEY-----` header |
| `BASIC_AUTH_URL` | URLs with embedded `user:password@host` credentials |
| `AWS_ACCESS_KEY` | `AKIA` followed by 16 uppercase alphanumeric characters |
| `HIGH_ENTROPY_VALUE` | `api_key`, `auth_token`, `secret`, `access_token`, `private_key` key names followed by 32+ chars (case-insensitive) |
| `GITHUB_TOKEN` | `ghp_` (classic) or `github_pat_` (fine-grained) personal access tokens |
| `SLACK_TOKEN` | `xox[baprs]-` Slack bot/app tokens |
| `GOOGLE_API_KEY` | `AIza` followed by 35 alphanumeric/hyphen/underscore characters |

**Scan result labels:**

- `SAFE` — no patterns matched; content may be stored and compressed normally.
- `SENSITIVE` — only `PASSWORD_FIELD`, `BEARER_TOKEN`, or `HIGH_ENTROPY_VALUE` matched; these are redacted but the content is not hard-blocked.
- `SECRET` — any higher-certainty pattern (API keys, PEM, AWS keys, GitHub tokens, Slack tokens, Google API keys) matched; content with `sensitivity_label="secret"` is blocked by PolicyEngine gate 3 and never compressed or stored.

**Policy gate interaction:** When the upstream `ContextAccessPolicy` layer has already labelled content with `sensitivity_label="secret"`, the PolicyEngine gate 3 (`sensitivity_blocked`) fires before SecretRedactor is invoked. SecretRedactor is an additional defence-in-depth layer for content that was not pre-labelled.

**Residual risk:** Regex-based detection cannot catch all secrets. Custom tokens, encoded secrets, or secrets split across compression boundaries may not be detected. Do not rely on SecretRedactor as the sole secret-protection mechanism. The ContextAccessPolicy `never_cloud` and `require_approval` rules upstream are the primary controls.

---

### Threat 2: Malicious Content in CCR Store

**Risk:** The CCR Store accumulates content from many agent runs. A compromised or malicious task injects content into the store that, when retrieved by a later task, causes prompt injection or leaks sensitive data across task boundaries.

**Mitigation: Local-only, TTL-evicted, content-addressed store**

The `CCRStore` implementation (`agent/services/context_compression/ccr_store.py`) is strictly local filesystem:

- All I/O uses `pathlib.Path` — no HTTP client, no socket, no subprocess.
- The store path is configured statically in `ccr_store.path`; there is no remote sync or replication.
- Entries expire automatically after `ttl_hours` (default 72). `CCRStore.expire_old()` must be called to evict expired entries from disk; this is called on agent startup and periodically.
- Content is keyed by SHA-256 hash — a given piece of content always resolves to the same ref, preventing accidental overwrite of different content under the same key.
- The store does not execute stored content. `CCRRetrievalTool.retrieve()` returns a plain string; the caller is responsible for how it uses the content.

**Cross-task isolation:** The CCR Store is scoped per agent instance by `store_path`. Worker isolation (separate working directories) ensures that workers on different tasks do not share a store unless explicitly configured to do so.

**Residual risk:** If the `store_path` directory is writable by an attacker, they can inject entries. Standard filesystem permissions on the store directory mitigate this. The TTL ensures injected content expires; it does not prevent a compromised entry from being retrieved before expiry.

---

### Threat 3: Compression Changes Semantics of Code or Patches

**Risk:** The SmartCompressor's JSON pruner, log noise removal, or generic head/tail truncation is applied to a code patch, test output, or structured config. The result looks similar but has different semantics — a line number shifts, an assertion changes, a field is pruned. The LLM acts on the corrupted version.

**Mitigation 1: Protected content types (PolicyEngine gate 2)**

`active_patch`, `diff`, and `credential` are on the `NEVER_COMPRESS_TYPES` list. The PolicyEngine gate 2 fires before SmartCompressor is invoked. These types can never be compressed regardless of mode, task intent, or token count.

**Mitigation 2: Protected task intents (PolicyEngine gate 4)**

When `task_intent` is `debug`, `fix`, `review`, `security_audit`, or `test_failure`, all compressible content types are blocked for that request. This covers the case where a `tool_output` or `json` block happens to contain a patch diff embedded in a larger JSON payload.

**Mitigation 3: QualityGuard checks (HCCA-008)**

After compression, QualityGuard validates the result with the following checks:

| Check | What it verifies | Penalty on failure |
|---|---|---|
| `not_empty` | Compressed output is not empty | Hard failure (score = 0.0) |
| `length_ratio` | Compressed is at least 1% shorter than original | 0.15 |
| `min_reduction` | At least 10% character reduction achieved | 0.10 |
| `error_lines_preserved` | Every `ERROR`, `EXCEPTION`, `TRACEBACK`, `CRITICAL` line present in the original is also present in compressed output | 0.30 |
| `json_valid` | If content_type is `json` and original was valid JSON, compressed must also be valid JSON | 0.25 |
| `no_new_secrets_introduced` | Compressed output must not contain secret-like strings that were absent from the original | 0.40 |

A score below `min_quality_score` (default 0.7) causes the adapter to fall back to passthrough. The fallback is logged with reason code `fallback_on_risk`.

**Mitigation 4: SmartCompressor preservation patterns**

The `_compress_log` strategy preserves any line matching `ERROR`, `EXCEPTION`, `TRACEBACK`, `CRITICAL`, or `WARNING` regardless of the noise-removal logic applied to surrounding lines. The `_compress_json` strategy's pruner does not prune top-level keys — only nested values beyond depth 4 and empty/null values are removed.

---

### Threat 4: External Headroom Adapter Leaks Data

**Risk:** When `external_headroom.enabled=true`, the adapter sends content to an external process or HTTP endpoint. If that process is compromised, has network access, or logs its inputs, sensitive content from the Ananta context escapes the local trust boundary.

**Mitigation 1: Disabled by default**

`HeadroomAdapterConfig.enabled` defaults to `False`. It must be explicitly set to `True` in config. There is no auto-discovery or activation path.

**Mitigation 2: CLI transport uses local subprocess only**

The `cli` transport (`transport="cli"`) launches a local subprocess via `subprocess.run()` with `capture_output=True`. It does not open any socket or outbound connection. The subprocess receives content via stdin and returns the result on stdout. The subprocess is the configured `command` (default: `["headroom"]`) which must be installed locally.

**Mitigation 3: HTTP transport is explicit and requires `base_url`**

The `http` transport requires a non-empty `base_url` to be configured. If `base_url` is empty, the adapter returns passthrough. Operators must explicitly specify the endpoint. No default endpoint exists.

**Mitigation 4: Passthrough fallback on any error**

Any exception from the external process — timeout, non-zero exit code, malformed JSON response, network error — causes a transparent passthrough. The original content is returned unchanged and a `reason_code="external_headroom_unavailable"` is logged.

**Residual risk:** HTTP transport sends content to a network endpoint. This is acceptable only in environments where the endpoint is local (e.g. `localhost:PORT`) or on a trusted private network. Do not configure an external `base_url` pointing to a third-party service without reviewing the data handling terms of that service.

---

### Threat 5: A/B Routing Skews Results or Leaks PII

**Risk:** The A/B router assigns some requests to a different compression strategy than expected. If the routing is non-deterministic or seeded with user-identifying data, the results may differ between users in ways that are not auditable.

**Mitigation 1: Deterministic routing**

`ABRouter.route(content_id)` computes SHA-256 over `"{seed}:{content_id}"`. The result is deterministic for a given seed and content ID. The same content in the same configuration always routes to the same lane (A or B).

**Mitigation 2: No PII in routing key**

`content_id` is a content hash or a task-scoped identifier (e.g. task ID + block index). It is never a user identifier, email, or IP address. The routing key contains no PII.

**Mitigation 3: Disabled by default**

`ABRouterConfig.enabled` defaults to `False`. All requests go to lane A (the primary adapter) when A/B routing is disabled.

**Mitigation 4: Full audit trail**

Every routing decision that results in a compression attempt is recorded in a `CompressionEvent` with `adapter_used`, `decision`, and `reason_code` fields. The A/B routing outcome is visible in `CompressionTracker.summary()` via `ABRouter.stats()`.

---

## Security Invariants Checklist

The following invariants must hold in any deployed configuration of the ContextCompressionAdapter:

- [ ] `current_user_message` is never compressed — enforced by `NEVER_COMPRESS_TYPES` in `CompressionPolicyEngine`
- [ ] `active_patch` / `diff` is never compressed in `debug`, `fix`, or `review` task intents — enforced by PolicyEngine gates 2 and 4
- [ ] `SecretRedactor` runs before any content is written to `CCRStore` — enforced in the adapter pipeline
- [ ] `CCRStore` is local filesystem only — no network I/O in `ccr_store.py`
- [ ] `external_headroom.enabled=false` by default — must be explicitly opted into
- [ ] `QualityGuard` blocks acceptance of compressed output below `min_quality_score` threshold — fallback to passthrough is logged
- [ ] Every compression step emits a traceable `CompressionEvent` to the `ananta.compression` logger
- [ ] Compression is always optional — `enabled=false` or `mode=passthrough_with_metrics` disables all compression without restart and without data loss

---

## Configuration Review Guidelines

When reviewing a deployment configuration for security, check the following:

**1. Is `external_headroom.enabled` set to `true`?**
If yes: verify that `transport` is `"cli"` (local subprocess only) or that `base_url` points to a trusted local endpoint. Document the data handling terms. Confirm that SecretRedactor is active (`ccr_store.redact_secrets=true`).

**2. Is `ccr_store.redact_secrets` set to `false`?**
This should never be `false` in production. If it is, secrets that pass policy gate filtering may be stored unredacted. Set to `true` unless there is an explicit, documented exception.

**3. Is `mode=compress_aggressive` configured?**
Aggressive compression lowers the minimum quality score to 0.6 and increases target reduction to 55%. Review the compressible content types list carefully. Ensure no code-adjacent types (`json` containing patch data, `tool_output` from diff tools) are on the list for workflows that use `compress_aggressive`.

**4. Is `ccr_store.path` on a world-writable directory?**
The CCR Store directory should be owned by the Ananta process user and not writable by others. Check with `ls -la` and apply `chmod 700` or equivalent.

**5. Is `max_input_tokens_before_considering` set to a very low value (e.g. 100)?**
A very low threshold causes compression to run on nearly all content, including small blocks that may contain structured data. The default of 1200 tokens (~4800 chars) is conservative and safe.

**6. Are `protect_task_intents` or `never_compress_types` customised to remove items from the defaults?**
Removing `debug`, `fix`, or `review` from `protect_task_intents`, or removing `active_patch` from `never_compress_types`, weakens the safety model. Any such customisation requires a documented exception with a security review.

---

## Related

- `agent/services/context_compression/policy_engine.py` — HCCA-004: gate sequence
- `agent/services/context_compression/secret_redactor.py` — HCCA-006: pattern registry
- `agent/services/context_compression/quality_guard.py` — HCCA-008: QualityResult checks
- `agent/services/context_compression/ccr_store.py` — HCCA-005: local store
- `agent/services/context_compression/external_headroom_adapter.py` — HCCA-012: opt-in external adapter
- `agent/services/context_compression/ab_router.py` — HCCA-014: deterministic routing
- `docs/context_access_policy_backend.md` — upstream sensitivity labelling and ContextAccessPolicy
- `docs/security/context-release-gates.md` — release gates for context exposure
- `docs/architecture/context-compression-adapter.md` — full adapter contract (HCCA-001)
- `docs/release/context-compression-rollout.md` — phased rollout plan (HCCA-017)

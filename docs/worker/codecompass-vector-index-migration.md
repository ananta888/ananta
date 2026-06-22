# CodeCompass Vector Index Migration Guide

**Status:** Reference  
**Date:** 2026-06-22  
**Author:** Ananta Architecture  
**Scope:** Index structure changes from v1 (raw `vector`) to v2 (`encoded_vector`)

---

## Overview

The CodeCompass vector index file changed structure when `VectorEncoding` was introduced. This document describes the differences, when migration is necessary, and how to safely rebuild or roll back.

---

## Old Index Structure (v1)

Each entry in `codecompass_vector_index.json` contained:

```json
{
  "record_id": "...",
  "kind": "function",
  "file": "worker/retrieval/vector_encoding.py",
  "vector": [0.0123, -0.4567, 0.8901, "..."],
  "embedding_text": "VectorEncoder.encode encodes a float32 vector ...",
  "importance_score": 0.8
}
```

- `vector`: raw `list[float]` of float32 values.
- No encoding metadata.
- No `algorithm_version` in state.
- No compression ratio or error diagnostics.

The state block was minimal:

```json
{
  "state": {
    "retrieval_cache_state": "...",
    "manifest_hash": "...",
    "embedding_provider_config_hash": "..."
  }
}
```

---

## New Index Structure (v2)

Entries now contain `encoded_vector` with full encoding metadata:

```json
{
  "record_id": "...",
  "kind": "function",
  "file": "worker/retrieval/vector_encoding.py",
  "encoded_vector": {
    "mode": "int8",
    "dimensions": 1536,
    "payload": "<base64>",
    "metadata": {
      "profile": { "mode": "int8", "target_bits": 8.0, "seed": 888, "block_size": 0, "store_original": false, "algorithm_version": "vector-encoding.v1", "experimental": false },
      "profile_hash": "a3b4c5d6...",
      "checksum": "sha256_first24",
      "scale": 0.00312,
      "zero_point": 0,
      "levels": 127
    },
    "diagnostics": {
      "bytes_original_float32": 6144,
      "bytes_encoded_payload": 1536,
      "compression_ratio_vs_float32": 4.0,
      "max_abs_error": 0.0016,
      "experimental": false
    }
  },
  "embedding_text": "VectorEncoder.encode encodes a float32 vector ...",
  "importance_score": 0.8,
  "source_scope": "repo"
}
```

When `store_original=true` (not the default), the raw `vector` field is also present alongside `encoded_vector`.

The state block in v2 includes encoding information:

```json
{
  "state": {
    "retrieval_cache_state": "...",
    "manifest_hash": "...",
    "embedding_provider_config_hash": "...",
    "vector_encoding_profile": { "mode": "int8", "seed": 888, "..." },
    "vector_encoding_config_hash": "a3b4c5...",
    "vector_encoding_compression_ratio": 4.0,
    "vector_encoding_max_abs_error": 0.0016,
    "algorithm_version": "codecompass_vector_index.v2"
  }
}
```

---

## When Rebuild Is Necessary

A full index rebuild is triggered automatically when:

1. **Encoding profile changes**: `config_hash` in state differs from the profile built from current settings. `refresh()` returns `reason=vector_encoding_changed`.
2. **Manifest hash changes**: repository content changed, new files indexed.
3. **Embedding provider changes**: `embedding_provider_config_hash` changed (different model, different endpoint).
4. **Manual trigger**: operator runs `rebuild` via CLI or service API.

A rebuild is **not** necessary when:
- Only the `store_original` setting changes (next rebuild will handle it).
- Querying a v1 index with `mode=off` (backward-compatible path, see below).
- Changing quality gate thresholds (gate is evaluated at rebuild time, not query time).

---

## How Users Disable Quantization and Rebuild

### Step 1: Set encoding mode to off

Via environment variable:

```bash
CODECOMPASS_VECTOR_ENCODING_MODE=off
```

Or via service configuration:

```json
{
  "vector_encoding": {
    "mode": "off"
  }
}
```

### Step 2: Trigger rebuild

Via the CodeCompass service API or CLI:

```bash
python scripts/setup_codecompass_index.py --rebuild
```

Or by deleting the index file and restarting the service (the store auto-rebuilds on missing index):

```bash
rm codecompass_vector_index.json
# restart service or run refresh
```

### Step 3: Verify

After rebuild, the state block should show:

```json
{ "vector_encoding_profile": { "mode": "off" } }
```

And entries will have `vector` (raw float32) instead of `encoded_vector`, or both if the index was rebuilt from a prior v2 state with `store_original=true`.

---

## algorithm_version and Format Migration

The `algorithm_version` field in `VectorEncodingProfile` defaults to `"vector-encoding.v1"`. It is stored in each entry's `encoded_vector.metadata.profile.algorithm_version` and in the state block.

If the encoding algorithm changes in a future version (e.g., new rotation matrix, different packing format), `algorithm_version` must be incremented (e.g., `"vector-encoding.v2"`). The store detects this mismatch on load and treats it as a `vector_encoding_changed` reason requiring rebuild.

**Current versions:**

| Field | Value |
|---|---|
| Index file schema | `codecompass_vector_index.v2` |
| Encoding algorithm | `vector-encoding.v1` |

---

## `store_original=false` Is the Safe Default

Setting `store_original=false` (the default) means the raw float32 vector is not stored alongside the encoded vector. This is intentional:

- Prevents storage growth (no double-storage of vectors).
- Reduces index file size.
- Is safe: the encoded vector can always be decoded back to an approximation of the original.
- Reconstruction fidelity is tracked via `max_abs_error` in diagnostics.

`store_original=true` is only needed if:
- You need to switch encoding modes without a full re-embed (the raw vector allows re-encoding without calling the embedding provider again).
- You are debugging quantization accuracy and need the exact original for comparison.

Setting `store_original=true` approximately doubles the index size for non-trivial compression ratios.

---

## Backward Compatibility: Old Index Without `encoded_vector`

A v1 index file (entries with only `vector`, no `encoded_vector`) remains fully readable:

- `CodeCompassVectorStore.load()` returns the entries as-is.
- `search()` checks for `encoded_vector` first; if absent, falls back to `vector`.
- No crash, no error, no data loss.

The v1 index is not automatically migrated to v2 on load. Migration happens only on the next `rebuild()` or `refresh()` call when `vector_encoding_changed` is detected.

If the state block has no `vector_encoding_profile` key (v1 state), the store treats this as `mode=off` with no config hash, which will trigger a rebuild on the next `refresh()` if a non-off encoding is configured.

---

## Summary

| Scenario | Action Required |
|---|---|
| Old v1 index, no encoding configured | Nothing; continues to work |
| Old v1 index, encoding now configured | Rebuild on next `refresh()` call |
| Encoding mode changed | Automatic rebuild on `refresh()` |
| Disable encoding (was int8, now off) | Set env var, trigger rebuild |
| Index file deleted | Auto-rebuild on service start |
| `algorithm_version` bumped in code | Rebuild required; detected automatically |

---

## Related

- `worker/retrieval/vector_encoding.py` — VectorEncodingProfile, VectorEncoder, EncodedVector
- `worker/retrieval/codecompass_vector_store.py` — store load/save/rebuild/refresh logic
- `docs/worker/codecompass-vector-quantization-metrics.md` — quality gates for encoded index
- `docs/release/codecompass-vector-encoding-rollout.md` — rollout and rollback steps

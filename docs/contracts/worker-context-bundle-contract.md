# Worker Context Bundle Contract

Schema: `schemas/worker/worker_context_bundle.v1.json`

## Purpose

Defines the worker-facing retrieval bundle payload with optional CodeCompass provenance metadata while preserving plain `context_text` compatibility.

## Required fields

- `schema=worker_context_bundle.v1`
- `bundle_type`
- `query`
- `chunk_count`
- `chunks`

## CodeCompass provenance fields

Each chunk must include `engine`, `source`, `content`, `score`, `metadata`.

`metadata` may include:

- `record_id`
- `record_kind`
- `file`
- `vector_score`
- `expanded_from`
- `relation_path`
- `source_manifest_hash`

Workers that only need plain context can consume `context_text` and ignore metadata.


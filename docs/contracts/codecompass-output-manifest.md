# CodeCompass Output Manifest Contract

Schema: `schemas/worker/codecompass_output_manifest.v1.json`

## Purpose

Defines which CodeCompass JSONL outputs are present for retrieval and includes hash/mtime metadata for cache invalidation and provenance.

## Required top-level fields

- `schema=codecompass_output_manifest.v1`
- `codecompass_version`
- `profile_name`
- `source_scope`
- `generated_at`
- `output_dir`
- `outputs`

## Output entries

`outputs` contains these keys (each key may be an object or `null`):

- `index`
- `details`
- `context`
- `embedding`
- `relations`
- `graph_nodes`
- `graph_edges`

Each non-null output entry contains:

- `path`
- `sha256`
- `mtime`
- `record_count`


# Incremental Artifact Layers

Stand: 2026-08-17 · Track: `codecompass-incremental-artifact-index-layer-sync`

CodeCompass indexes are an immutable **base** plus ordered **delta**
layers. Reads overlay newest-wins records and honor tombstones. The Hub
owns heads, generations and publish; workers only produce layer blobs.

```text
Snapshot A  -> Base layer
Snapshot B  -> Delta (upsert/tombstone)
Snapshot C  -> Delta
Compact     -> new Base, same effective view
```

Compatibility keys keep embedding/graph/FTS identities separate from
the human profile name. An embedding-model change rebases vectors
without silently mixing dimensions.

Operator surfaces:

- `GET /api/codecompass/layers/profiles`
- `GET /api/codecompass/layers/heads/<profile>`
- `POST /api/codecompass/layers/diff`
- `POST /api/codecompass/layers/update` (`dry_run` default true)
- `POST /api/codecompass/layers/compact`
- MCP `codecompass.layers_heads` / `codecompass.layers_plan`

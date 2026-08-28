# CodeCompass SIRA index lifecycle

SIRA is off by default. Offline enrichment accepts only Hub-approved,
scope-filtered CodeCompass documents. Generated terms are persisted as immutable
artifacts, never regenerated during an online query.

1. Normalize a complete bound document snapshot.
2. Compare `record_id` and `document_hash` with the active layer set.
3. Enrich only added/changed records and tombstone deletions.
4. Write the new base or delta layer to a temporary file, fsync and atomically
   replace its final path.
5. Build FTS rows and DF/CF statistics for the same binding.
6. Verify digests, then atomically publish the active pointer last.

`plan_incremental_enrichment` invalidates every current document when the
prompt/model/chunk/profile dependency digest changes. Otherwise it emits exact
unchanged, enrich and tombstone sets. `EnrichmentLayerStore` is idempotent by
content-derived layer ID. An interrupted write leaves the previous active
pointer readable.

Use `scripts/manage_sira_index.py build` to build a bound FTS snapshot,
`diagnostics` to compare the requested binding with the active snapshot, and
`compact --layer-root …` to merge the active base and deltas. Compaction writes
and verifies a new base before switching the pointer; old layer files are not
automatically deleted.

## Hub-queued production operations

Configure each SIRA-capable Worker with both
`CODECOMPASS_SIRA_SNAPSHOT_ROOT` and `CODECOMPASS_SIRA_LAYER_ROOT`. The first is
the Worker-local inbox populated by the governed CodeCompass synchronizer; the
second stores repository-isolated base/delta layers. If either setting is
missing, the Worker does not advertise the `sira_index` capability. No API
request can select either path.

The synchronizer publishes a complete, immutable
`codecompass.sira-sync-snapshot.v1` JSON artifact named
`<snapshot_artifact_id>.json`. It contains exactly `schema`, `binding`,
`documents`, and `enrichments`. The binding includes tenant, project,
repository, revision, manifest/index/statistics digests, profile version, and
profile digest. The Worker rejects scope mismatches, duplicate document IDs,
orphaned enrichments, content-hash mismatches and invalid artifact digests.

An authenticated project owner, maintainer, or automation identity can enqueue
a sync through the Hub without an interactive approval:

```http
POST /api/codecompass/sira/operations
Content-Type: application/json

{
  "operation": "sync",
  "repository_id": "repository-a",
  "snapshot_artifact_id": "snapshot-20260828-001",
  "idempotency_key": "repository-a-sync-20260828-001"
}
```

For compaction, use `"operation": "compact"` and omit
`snapshot_artifact_id`. Repeating the same idempotency key and payload returns
the original operation; reusing the key with a different payload is rejected.
Poll `GET /api/codecompass/sira/operations/<operation_id>` for the Hub-owned
task state. The Worker executes one operation and never creates child tasks or
contacts another Worker. Failures are terminal, bounded reason codes; no test or
production path waits for a person.

The Hub must never open Worker index paths. Worker status is reported through a
redacted read-model port. Queueing, retry, job ownership and activation remain
Hub decisions; the CLI performs only a directly requested Worker-local storage
operation.

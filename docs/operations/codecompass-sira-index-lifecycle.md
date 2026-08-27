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

The Hub must never open Worker index paths. Worker status is reported through a
redacted read-model port. Queueing, retry, job ownership and activation remain
Hub decisions; the CLI performs only a directly requested Worker-local storage
operation.

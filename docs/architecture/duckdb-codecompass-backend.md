# DuckDB CodeCompass Backend

Stand: 2026-08-17 · Track: `duckdb_codecompass_analytics_retrieval_integration`

DuckDB is an **optional local snapshot** for analytics, Parquet exchange
and exact vector search. It is not the system of record.

## Boundaries

| Concern | Owner |
|---|---|
| Tasks, policy, auth, queue | Hub |
| Productive remote vectors | Qdrant |
| Deterministic offline/test vectors | JSON VectorStore |
| Productive FTS | SQLite FTS5 |
| Canonical graph | CodeCompass GraphStore |
| Local joins, counts, exact ANN-free search | DuckDB snapshot |

Agents never receive free SQL, `ATTACH`, `INSTALL`, `LOAD`, or `COPY`
to arbitrary paths. They call named query templates with typed
parameters, a row limit and the same tenant/workspace/revision scope
used by hybrid retrieval.

## Publication

One immutable `.duckdb` file per
`workspace_id / repository_id / profile_name / compatibility_fingerprint`.
A worker writes a staging file, validates schema/counts/scope, then
swaps `active-snapshot.json`. Readers open the pointer path read-only.

VSS/HNSW stays off unless an explicit experimental profile enables it.
Exact cosine over scoped rows is the compatibility baseline.

## Composition

```text
Hub task
  -> CodeCompassDuckDBMaterializer
  -> DuckDBSnapshotManager (single writer)
  -> DuckDBVectorStore / Analytics templates / Parquet export
  -> CodeCompassAgenticRetrievalService (optional channel)
```

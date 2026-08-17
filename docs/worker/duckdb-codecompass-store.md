# DuckDB CodeCompass store

Optional extra: `pip install ananta[duckdb]`.

Provider `duckdb` writes versioned `.duckdb` snapshots under
`snapshot_root` and publishes `active-snapshot.json`. Search is exact
cosine over scope-filtered rows. JSON remains the default vector store.

See `docs/architecture/duckdb-codecompass-backend.md`.

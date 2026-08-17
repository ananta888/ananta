# DuckDB rollout

1. Keep `provider=json` as default.
2. Enable `ananta[duckdb]` on one worker.
3. Materialize a snapshot from a known CodeCompass manifest.
4. Compare exact search against JSON for the same scope.
5. Turn on analytics templates only after security tests pass.
6. VSS/HNSW stays off.

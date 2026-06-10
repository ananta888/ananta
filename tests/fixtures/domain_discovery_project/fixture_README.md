# Domain Discovery Test Fixture

A small, self-contained Python project used by the CCDD-016/017 integration
tests under `rag-helper/tests/test_codecompass_domain_discovery_integration.py`.

## Layout

```
identity/        users/, sessions/             -> 4 files
billing/         invoices/, payments/          -> 4 files
rag/             indexer/, retriever/          -> 4 files
orchestration/   workers/, queues/             -> 4 files
ui/web/          components/, api/             -> 4 files
shared/util/     date_utils.py                 -> 1 file (cross-coupled)
misc/            loose.py                      -> 1 file (isolated)
```

Total: 22 files, all with the same trivial class shape (one class per
file). The file content is irrelevant for the analysis; the test
asserts only on the structural shape (file count, path, cross-edges).

## Expected findings

When run through the CCDD analysis library:

- 5 main domain candidates surface: identity, billing, rag, orchestration, ui
- `misc/loose.py` is `unassigned_records` (single file, no root_path,
  no edges)
- `shared/util/date_utils.py` is `unassigned_records` (single file
  below the `min_files` threshold; cross-cluster edges surface as
  `external_domain_refs` on the three consumer clusters)
- identity and billing surface a `mutual_coupling` boundary warning
  (3 edges per direction, each with a distinct edge_type to defeat
  the graph dedup)

## Why these specific decisions

- The fixture must be small enough to run as a unit test (< 100 ms).
- Cross-cluster coupling must be visible without enlarging the
  fixture; the 3 distinct edge_types between identity and billing
  produce a mutual_coupling warning at the default threshold.
- `shared/util/date_utils.py` lives outside the 5 main domains
  intentionally; a future descriptor may rename it to its own
  cluster, but at the analysis level it is unassigned.
- `misc/loose.py` is the canonical example of an isolated record
  that should never be claimed as a domain.

## Adding files

If you add a new file, update `EXPECTED_FILES` in
`rag-helper/tests/test_codecompass_domain_discovery_integration.py`
and re-run the integration tests. The fixture is part of the
deterministic test output: any change here may shift the expected
domain-id set and the boundary_warnings list.

# DSPy optimization engine

DSPy is optional and default-off. Install only the isolated worker extra with
`pip install '.[dspy]'`; normal Hub and worker installations do not import it.
The admitted compatibility pin is `dspy==3.2.1`. Its immutable tag object is
`27a8e2a134b0b8dbd2d7433ea67ffe9be627d376` and resolves to commit
`29448ae12756abdd14bd8796c819247ebb83673c`. Artifact digests, dependency
versions, licenses and the support matrix live in
`config/licenses/dspy-optimization.v1.json`. The image installs the generated
hash-locked dependency set, never a floating transitive resolution.

Official upstream metadata for that commit declares Python 3.10–3.14, an MIT
license, and dependencies including OpenAI, LiteLLM, Pydantic, Diskcache,
Cloudpickle and GEPA. Ananta never loads program state through Cloudpickle.
The 3.3 family changes the BaseLM path, so it is a compatibility candidate
rather than the production pin.

`pip-audit 2.10.1` reports CVE-2025-69872/PYSEC-2026-2447 for the transitive
`diskcache==5.6.3` pin and no fixed version. DSPy's default disk cache can
deserialize pickle values, so Ananta unconditionally disables it immediately
at the optional adapter boundary. Only a bounded process-local memory cache is
allowed. The read-only, non-root worker remains disposable and does not share
this cache across containers or jobs.

Configuration lives in `config/dspy/optimization_defaults.v1.json`. To run a
local isolated worker set `ANANTA_DSPY_OPTIMIZATION_ENABLED=true` and
`ANANTA_DSPY_OPTIMIZATION_MODE=local`. `mock` is deterministic and network-free
for tests. Cloud mode still requires exact Hub provider bindings.

The API exposes a capability read model, dry-run, tenant-scoped job lifecycle,
evaluation, policy promotion and rollback. Dry-run performs no model call.
All terminal paths return a deterministic state; no test or production flow
waits for a human. Missing evidence or capabilities block release while keeping
the baseline active.

The fixed `dspy-local-baseline` profile automatically admits its immutable
repository bundle into the Hub Evidence Registry, reserves the `RUN_*` before
execution and verifies the task/revision/scope binding after the test run.
This proves the local source and contract baseline only. Production promotion
still requires a production-scoped run with real dataset, provider, quality,
cost, recovery and image-digest evidence.

The three closed program kinds are planning structured tasks, scoped RAG answer
and structured extraction. They cannot add tools, routes, providers or storage
backends. Optimized programs export to canonical JSON and can be executed by a
future native renderer without DSPy.

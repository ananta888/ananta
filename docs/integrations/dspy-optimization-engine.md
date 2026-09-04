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
backends. `DspyNativeProgramRuntime` executes promoted canonical JSON without a
DSPy import and falls back to the established baseline on any contract,
rendering or parsing failure.

## Headless operator flow

The API and `ananta optimization` CLI expose the same Hub services:

```text
ananta optimization capabilities
ananta optimization dry-run --spec optimization-spec.json
ananta optimization create --spec optimization-spec.json --idempotency-key experiment-001
ananta optimization runs --tenant-id tenant-1
ananta optimization promote --plan promotion-plan.json --evaluation evaluation.json
ananta optimization provenance --tenant-id tenant-1 --scope-id planning-en
ananta optimization rollback --tenant-id tenant-1 --scope-id planning-en --revision 2
```

Dry-run validates the immutable dataset digest, optimizer, exact Provider
Execution Bindings and all hard budgets without a model call. Create delegates
one authorized job to one isolated Worker. The Worker may return only a closed
program artifact; evaluation, promotion, canary assignment and rollback remain
Hub operations. CLI and UI are optional clients and no action waits for human
input. An enabled Hub policy may promote automatically after every deterministic
and attested gate passes.

The REST surface is the canonical API contract used by both clients:

| Method and path | Purpose | Required binding |
| --- | --- | --- |
| `GET /api/dspy-optimization/capabilities` | safe capability/limit projection | authenticated caller |
| `POST /api/dspy-optimization/dry-run` | validate a spec without model calls | admin, closed spec |
| `POST /api/dspy-optimization/runs` | create an idempotent run | admin, `Idempotency-Key` |
| `GET /api/dspy-optimization/runs` | bounded tenant run page | admin, `tenant_id` |
| `POST /api/dspy-optimization/evaluations` | compare bound run manifests | admin, baseline/candidate |
| `POST /api/dspy-optimization/promotion-plans` | apply all automatic gates atomically | admin, plan/attestation |
| `GET /api/dspy-optimization/provenance` | read immutable registry history | admin, tenant/scope |
| `POST /api/dspy-optimization/rollbacks` | activate the known previous digest | admin, expected revision |

All mutation failures are bounded JSON responses: conflicts use HTTP 409,
policy denials 403, invalid contracts 422 and unavailable capabilities 503.
The signed, digest-bound PromotionPlan is the explicit machine confirmation;
no interactive confirmation is required or accepted by the execution path.

## Program-kind recipes

Planning uses the fixed `goal, constraints -> tasks` signature. Scope binds
language, planning mode, model profile and output schema. The existing
`PlanningPromptOptimizerService` remains the baseline; malformed candidate
output automatically uses that baseline. Evaluation checks exact task fields,
unique IDs, dependency order, schema validity, policy and cost.

RAG uses `question, context -> answer, citations`. The context comes only from
the trusted CodeCompass retrieval port with tenant, workspace, repository,
profile and role scope. Each citation must be an admitted `SRC_*` reference and
content digest. JSONVectorStore and Qdrant remain infrastructure choices behind
that same port; prompt programs cannot select them.

Structured extraction uses `input -> result` and binds input/output schema IDs.
Strict JSON and the single deterministic repair (removing a complete JSON code
fence) are reported separately. Repair never fills or changes domain values;
missing, extra or invalid fields remain deterministic failures.

All recipes use a disjoint holdout set, digest-only prompt/output telemetry,
explicit call/token/cost/time limits and the same atomic rollback path.
The evaluation manifest also binds seed, prompt digest, DSPy version, hardware,
cache mode, sampling digest, repetitions and warmups. A 95-percent uncertainty
margin and minimum sample count must still leave the configured quality delta;
otherwise promotion fails closed. Provider-reported cost remains observational:
budget and promotion cost comes only from the Hub-supplied Ananta price profile.

## Compatibility and upgrade

| Ananta | DSPy | Python | Profile | Verified capability |
| --- | --- | --- | --- | --- |
| 0.7.x | 3.2.1 | 3.11, 3.12 | `dspy-3.2.1-ananta-v1` | BaseLM legacy call, typed bridge seam, LabeledFewShot state-only export |
| 0.7.x | 3.3.0 | isolated 3.12 lane | none | verified fail-closed rejection as an unadmitted candidate |

The DSPy workflow installs the exact hash lock on both supported Python
versions and runs the real adapter tests. A version mismatch reports
`dspy_version_incompatible`; it never loads or promotes an old artifact.
Additive PromptProgram upcasts retain the original digest and an explicit
migration record. Rollback uses the previous dependency lock and immutable
container reference; a new image cannot inherit promotion authority from the
old image.

Each job receives a context-local LM/cache/settings boundary and a private
temporary directory. Bounded checkpoints bind tenant, run and spec digest; a
mismatch is discarded, and completed jobs erase their checkpoint and temporary
state. Explicit retryable provider failures follow only the configured retry
budget, consume the normal call budget and retain one idempotent logical request
identity across attempts.

`scripts/build_dspy_worker_sbom.py` deterministically binds the 67-package lock,
base-image digest and Dockerfile digest. The actual built-image digest is
created by CI/release and must be registered with the production run; the local
SBOM deliberately leaves it null rather than inventing it.
The same workflow runs the pinned advisory scan and rejects every finding except
the specifically documented DiskCache advisory whose executable persistence
path is disabled. Metrics and alerts are defined in
`config/monitoring/dspy-optimization.v1.json`; labels are limited to kind and
outcome and never include tenants, runs, prompts, outputs or source code.

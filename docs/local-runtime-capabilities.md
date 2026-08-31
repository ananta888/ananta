# Local runtime capabilities

Ollama and LM Studio capability discovery is read-only and default-off. The
Hub owns refresh admission and dispatches refresh work through its task system;
Workers only execute the assigned provider probe and never create follow-up
tasks. A routing hot path reads the atomic snapshot cache and performs no
network request.

Each snapshot is bound to provider, model digest and runtime version. Positive
native routing requires a non-stale `runtime_reported`, `profile_declared`, or
unexpired `observed_success` claim. A non-expired `observed_failure` blocks a
positive declaration until its TTL ends; heuristics are diagnostic only. Embedding models
cannot become chat candidates, template conflicts disable native tools, and a
runtime/model digest change creates a distinct snapshot.

Raw templates are classified and hashed but never persisted or returned. API
projections contain no base URL, credentials, tool arguments, prompt text,
images, or raw provider responses. Redirects are rejected before a second
request; local/private endpoints require an exact configured origin.

## Deterministic examples

An Ollama `/api/tags` row is retained even if its bounded `/api/show` detail
request fails. A successful detail response such as
`{"capabilities":["completion","tools"]}` produces positive
`runtime_reported` chat and tools claims. LM Studio continues to start with
only `/v1/models`; native `/api/v1/models` metadata is optional. A native row
with `{"type":"embedding","capabilities":["embedding"]}` is routed only as
an embedding model and never inferred from the model name.

## Rollout and rollback

| Phase | Activation trigger | Metrics and stop conditions | Rollback | Responsible operator |
|---|---|---|---|---|
| 1: observe | Refresh task tests and cache validation are green. | Refresh success, stale age, rejected metadata and cache corruption; stop on secret exposure or repeated invalid snapshots. | Disable refresh admission and retain or remove the diagnostic cache. | Hub platform operator |
| 2: capability routing | Phase 1 has stable snapshots and the routing feature flag is explicitly enabled. | No-routable-model rate, capability conflicts, fallback reason codes and provider error rate; stop on a routing regression. | Disable capability-aware routing; legacy provider/profile selection remains authoritative. | Model-routing operator |
| 3: unified tools | Phase 2 is stable and the separate Hub tool-loop flag is explicitly enabled. | Tool policy denials, loop deadlines, call/iteration limits and completion rate; stop on an unauthorized call or orphaned execution. | Disable the tool-loop flag while retaining text generation and snapshots. | Hub execution operator |

No phase loads, unloads, downloads, or mutates a model. Rollback never needs a
runtime mutation and the legacy provider configuration remains valid.

## Compatibility and deprecation

Catalog-v1 readers remain supported; Catalog v2 plus the runtime-capability
endpoint is the replacement path. This track does not remove an API. Before a
future removal, the server must first emit a stable
`model_catalog_v1_deprecated` warning for at least two minor releases. Removal
is permitted only in a major release and no earlier than 2027-08-31. The
warning and this date are compatibility policy, not a claim that removal is
scheduled.

Operators can inspect a cache without contacting a runtime:

```bash
python scripts/local_runtime_capability_probe.py --cache data/local-runtime-capabilities.json
```

Exit code `0` means fresh snapshots, `1` means at least one stale snapshot,
and `2` means no usable snapshot. Live-provider, performance, GPU, `SRC_*`, and
`RUN_*` claims remain unverified unless those identifiers are actually supplied.

`POST /api/models/runtime-capabilities/v1/refresh` only admits and coalesces a
Hub task. A worker with `local_runtime_capability_discovery` performs the
read-only probe. The Hub validates the returned provider/model/digest bindings
before replacing its cache, so no container depends on shared filesystem state.

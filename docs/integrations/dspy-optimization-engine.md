# DSPy optimization engine

DSPy is optional and default-off. Install only the isolated worker extra with
`pip install '.[dspy]'`; normal Hub and worker installations do not import it.
The observed stable pin is `dspy==3.2.1`. Its tag resolves to annotated tag
object `27a8e2a...` and commit `29448ae...`; these navigation values are not
release evidence until a Hub source catalog supplies the exact allowed
`SRC_*` identifier.

Official upstream metadata for that commit declares Python 3.10–3.14, an MIT
license, and dependencies including OpenAI, LiteLLM, Pydantic, Diskcache,
Cloudpickle and GEPA. Cloudpickle is installed transitively but Ananta never
loads program state through it. The prerelease 3.3.0b1 introduces a new BaseLM
system, so it is a compatibility candidate rather than the production pin.

Configuration lives in `config/dspy/optimization_defaults.v1.json`. To run a
local isolated worker set `ANANTA_DSPY_OPTIMIZATION_ENABLED=true` and
`ANANTA_DSPY_OPTIMIZATION_MODE=local`. `mock` is deterministic and network-free
for tests. Cloud mode still requires exact Hub provider bindings.

The API exposes a capability read model, dry-run, tenant-scoped job lifecycle,
evaluation, policy promotion and rollback. Dry-run performs no model call.
All terminal paths return a deterministic state; no test or production flow
waits for a human. Missing evidence or capabilities block release while keeping
the baseline active.

The three closed program kinds are planning structured tasks, scoped RAG answer
and structured extraction. They cannot add tools, routes, providers or storage
backends. Optimized programs export to canonical JSON and can be executed by a
future native renderer without DSPy.

# Workflow Runtime Inventory and Baseline Audit

Snapshot: repository state 2026-07-13. Normative machine-readable inventory:
[`workflow-runtime-inventory.v1.json`](workflow-runtime-inventory.v1.json).

## Reading the classifications

- **live**: code has a real execution/read path, subject to configuration and
  the listed production gates;
- **simulated**: deterministic lifecycle/probe behavior with no claim that user
  work or side effects executed;
- **degraded**: a concrete implementation exists, but optional dependency,
  configuration, bridge or capability evidence prevents production use;
- **placeholder**: contract/component exists but the production call path is
  deliberately unavailable.

Classification is per path, not per filename. A runtime can have a live worker
implementation and a degraded/unavailable public bridge. Tests are evidence for
the tested contract only, not evidence of production deployment health.

## Selectors and runtime paths

The compatibility backend selector still accepts exactly `local` and
`temporal`; unknown values fail closed. Productive control no longer treats
that selector as the runtime catalog: one Hub-owned registry exposes Native,
LangGraph and Temporal to deterministic profile/capability selection before a
run is bound. The optional worker adapter catalog still contains `langchain`
and `langgraph`; descriptor discovery is sanitized and read-only. LangChain is
an integration layer, not a fourth runtime or control plane.

| Path | Class | Owner / container | Current call path and truth |
| --- | --- | --- | --- |
| Legacy local `WorkflowBackend` | simulated compatibility class | Hub / `ai-agent-hub` | retained for additive API compatibility and isolated tests; productive `local` selection is bound to the Native bridge and never executes through this in-memory class |
| Native graph | live, release-gated | Hub plus worker / Hub and agent containers | unified control service → Hub-owned graph/reconciler → Hub task queue → one delegated Native node → signed SQL checkpoint and canonical event projection |
| LangChain adapter | live optional integration, not a runtime | worker / agent container | a Hub-selected delegated node may use approved provider/tool/retriever adapters; LangChain cannot start runs, select workers or own checkpoints |
| LangGraph adapter | live optional runtime, release-gated | Hub plus worker / Hub and dedicated LangGraph worker | unified control service → Hub scheduler/checkpoint gateway → one Hub task per ready node → single-node LangGraph execution → canonical Hub merge/projection |
| Temporal client backend | live optional, explicitly degraded on failure | Hub / `ai-agent-hub` | authenticated route → SDK client → Temporal; missing SDK/server/binding/projection returns degraded/non-success |
| Temporal durable workflow | live optional, fail-closed gateway | Temporal worker + Hub + Ananta worker | Temporal workflow → Activity → authenticated Hub gateway → Hub task queue → worker; Activity never selects worker |
| Temporal probe | simulated | Temporal smoke/worker/server | side-effect-free workflow and Activity proving registration/connectivity only |
| Runtime operations UI | live unified read/control surface | Hub + Angular | Angular, CLI and TUI read the same authenticated Hub projections; approval/evidence-bound commands return through the single control service |

The exact owner, container list, entry point, code/test evidence, gap and follow-up
task for every row are stored in the JSON inventory.

## Production trust boundary

Workflow authorization uses separate Ed25519 keyrings. The Hub alone mounts
`workflow_runtime_auth_signing_keyring`; Native, LangGraph and Temporal workers
receive only `workflow_runtime_auth_verification_keyring`, containing public
keys and revocations. Verification loaders reject `private_keys`, legacy
`keys`, unsafe paths and unknown fields. Shared HMAC is unavailable in
production and can be enabled only by the explicit development-compatibility
flag `ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING=1`.

Internal Workflow Worker, Hub task and LangGraph checkpoint gateways accept
agent service credentials/service JWTs only. Ordinary user/admin JWTs are not
service identities and fail closed. This keeps both signing authority and
runtime mutation endpoints outside the Angular/browser trust boundary.

## Temporary Worker namespace compatibility facades

Some framework-neutral contracts predate the dedicated contracts package and
still live below the Hub-shaped `agent` namespace. Productive Worker runtime and
adapter code is therefore limited to an exact, security-tested compatibility
list: `agent.providers.lc_lg` plus
`agent.services.workflow_runtime.{components,condition_evaluator,execution_plan,`
`native_graph_contracts,native_graph_ports,parallel,ports,security}`. A broad
`agent.services.workflow_runtime` prefix is deliberately not allowed.

These modules are contracts/ports and may not depend on Flask, persistence,
repositories, approval implementations, task queues or concrete Hub services.
Their migration target is `ananta_contracts.workflow_runtime.*`; provider
configuration DTOs move to a neutral contracts/configuration package. After
Worker imports have migrated, the scanner allowance and the compatibility
facades are removed. Restricted-inference modules remain governed by their own
AIR boundary program and do not widen this workflow-runtime exception.

## Capability → evidence → gap → follow-up

The inventory does not turn a descriptor string into a production claim. Every
reported capability has executable evidence, the remaining semantic/deployment
gap and one active AIR follow-up.

### Native

- approval and side-effect guarding use signed, role-bound Hub commands and the
  shared ledger; release admission verifies the security invariants (`AIR-019`);
- bounded parallel branches are separate Hub tasks with deterministic merge,
  shared capacity/budget ownership and race coverage (`AIR-015`, `AIR-019`);
- checkpoint/resume use the signed SQL Hub store and separate Hub/worker restart
  tests plus migration/recovery drills (`AIR-031`, `AIR-055`);
- stream uses the authenticated canonical cursor/backpressure contract shared
  by Angular, CLI and TUI (`AIR-025`);
- versioned components compile to the same flattened plan for Native and
  LangGraph, including N-1 evidence (`AIR-034`).

### LangGraph

- dry-run, agent/multi-step/review and human-in-loop paths execute only after
  Hub selection and produce versioned node-result evidence (`AIR-013`,
  `AIR-015`, `AIR-019`);
- runtime-private state never overrides canonical Hub state; productive
  checkpointing uses the Hub-owned SQL gateway, while `MemorySaver` remains an
  explicit development-only mode (`AIR-031`);
- CodeCompass retrieval preserves supplied provenance and rejects unknown
  source identifiers; tenant/source allowlists are Hub-bound (`AIR-023`);
- compiled/manual fallback is explicit, semantic-classified and release-gated;
  no exception triggers a silent walker change (`AIR-036`).

### Temporal

- durability/replay: SDK time-skipping replay, N-1 history, real Compose probe,
  server restart and hard worker replacement are all executable gates; each
  deployment still supplies its own backup/restore evidence (`AIR-055`);
- Hub Activity gateway: authorization and ledger receipt tests; requires mounted
  keyrings/service identity and Hub availability; the production Compose wiring
  is source-verified while real secret provisioning remains deployment evidence
  (`AIR-042`, `AIR-055`);
- retry/timeout/cancel: finite shared retry budget, non-idempotent suppression,
  uncertainty and cancel-race tests feed Hub reconciliation (`AIR-043`,
  `AIR-049`);
- signals/queries/updates: typed duplicate/stale/unauthorized tests; only the Hub
  exposes public control (`AIR-045`);
- history projection: pagination/dedupe tests; unknown/stale/inconsistent history
  blocks promotion (`AIR-046`).

## Archived completion claims remain visible

Archive files are historical inputs and are intentionally not rewritten.
Their `done`/`completed` fields are not release evidence.

| Archive | Visible contradiction at baseline | Current treatment |
| --- | --- | --- |
| `todos/archiv/todo.temporal-workflow-backend-adapter.json` | claimed Activity gateway, executable workflow, retries/cancel, UI, Compose and crash tests complete while only a degrading client adapter existed | the current Temporal worker/gateway/Compose/failure lab now supplies independent evidence; the old claim remains historical rather than retroactive proof |
| `todos/archiv/todo.workflow-blueprint-bpmn-temporal-integration.json` | whole integration marked completed although local backend simulated state and Temporal did not prove Hub-delegated work/recovery/projection | compatibility is retained behind the unified service; neutral contracts, gateway, durable projection and release gates now provide current evidence |
| `todos/archiv/todo.langchain-langgraph-worker-adapters.json` | track marked done although its own LCG-020 was partial and worker adapter code did not imply a production Hub bridge/checkpoint authority | discovery remains read-only; productive LangGraph now uses the Hub bridge, task fan-out, checkpoint gateway and conformance/release evidence |
| `todos/archiv/langchain_langgraph_support.todo.json` | later audit explicitly says some earlier “done” claims meant skeleton/example and enumerates more gaps | inventory cites individual tests/gaps/follow-up tasks instead of inheriting archive status |

This reconciliation preserves the historical contradiction: current changes can
resolve a gap, but do not retroactively make an earlier completion claim valid.

## Deterministic inventory gate

Run:

```bash
python scripts/validate_workflow_runtime_docs.py
```

The validator checks that:

- all backend selector values and default LangChain/LangGraph adapter kinds found
  in code are documented;
- every path has classification, owner, container, call path, evidence, gap and
  valid AIR follow-up;
- every capability has an existing test node, gap and follow-up;
- every archived claim references an existing immutable archive file;
- links/example/security gate requirements remain complete.

Changes that add a runtime adapter or backend selector without inventory and
evidence therefore fail the mandatory documentation/security CI job.

See the accepted
[runtime boundary ADR](../decisions/ADR-workflow-runtime-control-state-boundaries.md),
[architecture](workflow-runtime.md),
[threat model](../security/workflow-runtime-threat-model.md) and
[rollout runbook](../operations/workflow-runtime-rollout.md).

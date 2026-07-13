# Runtime-neutral Workflow Example

## What this proves

The example uses one immutable
[`ExecutionPlan`](../../../examples/workflow-runtime/execution-plan.v1.json) for
Native, LangGraph and Temporal. Runtime selection changes mechanics only:

- Native runs Hub-owned graph ticks and delegates each executable node;
- LangGraph runs as an optional worker adapter behind Hub delegation;
- Temporal durably schedules the same steps and each Activity submits a signed
  operation to the Hub task queue;
- LangChain, if installed, is only a provider/retriever/tool adapter.

The plan drafts a small JSON artifact, waits for operator approval before an
idempotent publication and verifies the resulting artifact. The deterministic
[fake provider](../../../examples/workflow-runtime/fake-provider.v1.json) fails
the first draft attempt and succeeds on the second. It performs no network I/O
and contains no credentials, live identifiers or timestamps.

## Dependencies and profiles

| Path | Dependency | Network/provider | Durability |
| --- | --- | --- | --- |
| Native | core Ananta image | deterministic fake | Hub checkpoint store |
| LangGraph | optional `lc-lg` extra | deterministic fake; live provider remains separately gated | only the configured evidenced checkpointer |
| Temporal | optional `temporal` extra and `compose.temporal.yml` | deterministic fake through Hub task | Temporal history plus Hub canonical state |

Optional OTLP traces use the `observability` extra and
`ANANTA_WORKFLOW_OTEL_ENABLED`, `ANANTA_WORKFLOW_OTEL_ENDPOINT` and an absolute
`ANANTA_WORKFLOW_OTEL_HEADERS_FILE`. The headers file is a read-only secret;
remote endpoints require TLS and plain HTTP is only for localhost/internal
`otel-collector`. Canonical events remain the source of truth, with fixed
low-cardinality attributes and bounded/redacted payloads.

The default Compose image contains core Ananta. Optional framework packages do
not become core imports. A local live-provider experiment is a separate,
additive profile and must carry provider egress approval; it is not needed for
the deterministic example or CI. The checked-in profile permits LangChain only
as provider, retriever or tool adapter and explicitly denies control-plane,
workflow-scheduler and task-queue ownership.

## Validate Compose

From the repository root, validate fixtures and render the complete disposable
example without installing a test runner:

```bash
python scripts/validate_workflow_runtime_docs.py
TEMPORAL_POSTGRES_PASSWORD=public-disposable-example-only \
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example config --quiet
```

Production Temporal remains a separate profile with external read-only secrets
and the fail-closed Hub gateway described in the
[Temporal runbook](../../operations/temporal-runtime.md). This example never
weakens or satisfies that production boundary.

## Optional local live-provider profile

[`compose.workflow-runtime-example.live-provider.yml`](../../../docker/compose-next/compose.workflow-runtime-example.live-provider.yml)
adds a bounded OpenAI-compatible provider probe. It does not alter the shared
ExecutionPlan and does not participate in Failure, Approval, Cancel, Crash or
Resume. The probe uses LangChain `RunnableLambda` only as a provider adapter.
It cannot submit Hub tasks, schedule graph nodes or control Native, LangGraph or
Temporal.

The endpoint and model are explicit environment references. The credential is
never placed in Compose or `.env`; Compose mounts the host file read-only as a
secret. The endpoint hostname must be one of the local names declared in
[`live-provider-profile.v1.json`](../../../examples/workflow-runtime/live-provider-profile.v1.json),
redirects are rejected, the response is size-bounded and evidence stores only
a content digest and usage metadata.

For an OpenAI-compatible LM Studio server on the Docker host:

```bash
export ANANTA_EXAMPLE_LIVE_PROVIDER_BASE_URL=http://host.docker.internal:1234/v1
export ANANTA_EXAMPLE_LIVE_PROVIDER_MODEL=your-loaded-local-model
export ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE="$HOME/.config/ananta/live-provider-token"

test -s "$ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE"
test "$(stat -c '%a' "$ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE")" = 600

docker compose --project-name ananta-runtime-live-provider \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  -f docker/compose-next/compose.workflow-runtime-example.live-provider.yml \
  --profile workflow-runtime-live-provider \
  config --quiet
docker compose --project-name ananta-runtime-live-provider \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  -f docker/compose-next/compose.workflow-runtime-example.live-provider.yml \
  --profile workflow-runtime-live-provider \
  run --rm --build workflow-runtime-example-live-provider
docker compose --project-name ananta-runtime-live-provider \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  -f docker/compose-next/compose.workflow-runtime-example.live-provider.yml \
  --profile workflow-runtime-live-provider \
  down --volumes --remove-orphans
```

Configure the same bearer value in the local provider. Even when a local
server ignores authentication, use a dedicated non-production credential file
rather than an inline Compose value. The resulting
`workflow-runtime-live-provider-v1.json` remains example evidence with
`production_release_gate: false`.

## Scenario matrix

Production user actions enter the authenticated Hub API. The isolated runner
uses signed, revision-bound example commands directly against its disposable
Temporal workflow and labels that boundary non-production. It does not invent
or claim source-grounding identifiers.

| Scenario | Action | Required canonical observation |
| --- | --- | --- |
| Failure | example Hub rejects draft attempt 1 | real Temporal Activity retry plus `task_submission_failed` and `retry_consumed` example-Hub records; publish has not started |
| Approval | draft completes and publish gate opens | revision-bound update is accepted; publish task is submitted only afterwards |
| Cancel | signed cancel while the draft Activity is running | Activity forwards cancel to the example Hub; workflow is terminal `cancelled` |
| Crash | `SIGKILL` only the Temporal worker after the draft checkpoint | Temporal server and Hub remain; workflow stays at the same checkpoint/revision |
| Resume | recreate worker and approve the current checkpoint | same workflow history advances to `completed`; completed draft is not repeated |

## Standalone executable drill

The drill has no `pytest` runtime dependency. It uses a dedicated Compose
overlay and three isolated roles:

- `workflow-runtime-example-hub` owns deterministic example task receipts and
  ledger decisions;
- `workflow-runtime-example-temporal-worker` is a real Temporal SDK worker and
  uses the production `HttpHubTaskGateway` Activity path;
- `workflow-runtime-example-runner` loads the checked-in ExecutionPlan,
  executes Native and pinned LangGraph drills, and controls real Temporal
  workflows.

The Hub endpoint, Native persistence/task ports and LangGraph node executor are
explicit example doubles. There are no provider network calls or production
credentials. The artifact is therefore `example_only` with
`production_release_gate: false`; it cannot satisfy a production gate.

CI executes this entire sequence in
[`quality-and-docs.yml`](../../../.github/workflows/quality-and-docs.yml):
it builds the image, runs `prepare`, verifies that the worker exits with code
137 after `SIGKILL`, recreates it, runs `resume`, validates the final evidence,
uploads diagnostics and always removes containers and volumes. Every startup
and runner command has an explicit timeout and the job is capped at 30 minutes.

Render the complete configuration first:

```bash
TEMPORAL_POSTGRES_PASSWORD=public-disposable-example-only \
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example config --quiet
```

Use a disposable project and volume. `prepare` runs Failure, Approval and
Cancel, then leaves the crash workflow at its persisted approval checkpoint:

```bash
export TEMPORAL_POSTGRES_PASSWORD=public-disposable-example-only
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  up -d --build temporal workflow-runtime-example-hub \
  workflow-runtime-example-temporal-worker
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  run --rm workflow-runtime-example-runner --mode prepare >/dev/null
```

Kill only the Temporal worker, recreate it, and resume the same workflow and
Temporal history. The Temporal server and example Hub stay running:

```bash
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  kill -s SIGKILL workflow-runtime-example-temporal-worker
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  up -d --force-recreate workflow-runtime-example-temporal-worker
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  run --rm workflow-runtime-example-runner --mode resume >/dev/null
```

Export and validate the machine-readable artifact. `artifacts/*.json` remains
an untracked runtime output:

```bash
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  run --rm --entrypoint cat workflow-runtime-example-runner \
  /evidence/workflow-runtime-example-v1.json \
  > artifacts/workflow-runtime-example-v1.json
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example \
  run --rm workflow-runtime-example-runner --mode validate-evidence >/dev/null
```

The Native crash observation is a checkpoint-backed orchestrator
reconstruction and the LangGraph resume observation is a non-durable adapter
reinvocation. Both are marked `production_equivalent: false`. Only Temporal
performs a real container `SIGKILL` and resumes from durable server history. A
staging promotion still requires the production release gate and destructive
drills from the [rollout runbook](../../operations/workflow-runtime-rollout.md).

## Expected artifacts and interpretation

The run is complete only when Hub read models link:

- `draft`, `published` and `verification` artifact references with expected
  schema/digest;
- canonical events with monotonic sequence and dedupe keys;
- current signed checkpoint or protected Temporal history reference;
- stable publication operation with completed side-effect ledger evidence;
- runtime/build, capabilities, cost/latency, recovery and any deviations;
- release gate evidence matching the plan/contract hash.

Do not expect raw provider payloads, credentials, artifact bodies or Temporal
heartbeat contents in these artifacts. `completed` without verified evidence is
degraded. A Temporal visibility status without current Hub projection is not
canonical success.

## Shutdown

```bash
docker compose --project-name ananta-runtime-example \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.workflow-runtime-example.yml \
  --profile temporal --profile workflow-runtime-example down --volumes
```

This project is deliberately disposable, so volume deletion after export is
required. Never reuse that command for a production Compose project.

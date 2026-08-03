# LangGraph Hub-owned Checkpoint Runtime

## Boundary and ownership

LangGraph remains an optional Worker execution runtime. The Hub remains the
control plane and owns task routing, authorization, fencing and persistence.
The dedicated LangGraph Worker never talks to another Worker and never opens a
database connection for checkpoint state.

The checkpoint path is deliberately narrow:

1. The Hub delegates a step with a signed authorization envelope and current
   fencing token.
2. The Worker runs LangGraph with `LangGraphHubOwnedCheckpointer`.
3. The checkpointer sends bounded JSON commands by authenticated `POST` to
   `/api/internal/workflow-runtime/langgraph/checkpoints` on the Hub.
4. The Hub revalidates tenant, workflow, run, step, plan, policy, active lease
   and fencing token for every read and write.
5. The Hub signs an immutable `SignedCheckpoint` and performs an atomic
   compare-and-set through the shared `CheckpointStore`.

The Hub imports neither LangGraph nor Worker modules. The Worker imports only
the neutral `ananta_contracts.langgraph_checkpoint` wire contract. Typed
LangGraph values are serialized by LangGraph's Worker-side serializer before
crossing the boundary and are covered by the Hub signature at rest.

`MemorySaver` and unsigned JSON resume tokens are restricted to the explicit
development combination `checkpoint_policy=local_ephemeral` and
`state_policy=ephemeral`. A production or Hub-owned profile fails closed when
the gateway, signed binding, lease or LangGraph runtime is unavailable.

## Production image and Compose overlay

The base image does not install optional LangGraph dependencies. The dedicated
production Worker uses the exact additive lock
`docker/compose-next/requirements.langgraph-worker.lock`. The build argument
`INSTALL_LANGGRAPH_RUNTIME=1` is set only by
`compose.langgraph.production.yml`; its default remains `0` for Hub, Angular,
ordinary Workers and development stacks.

The production overlay creates `ai-agent-langgraph-worker` on the dedicated
`langgraph-runtime` network. Only that Worker and the Hub join this network.
The Worker publishes no host port and receives only the public verification
keyring plus its own registration, service and session secrets. The Hub-admin
token, private authorization keyring and dispatch keyring remain Hub-only.

Prepare the common production credential files outside the repository:

- an authorization Ed25519 signing-keyring JSON for the Hub;
- a public verification-keyring JSON for runtime verifiers;
- a dispatch Fernet keyring JSON;
- separate Hub/Alpha/Beta session, registration and service files;
- the Hub-only Worker registration keyring.

Then create three independent LangGraph files: registration token, service
token and session signing key. Each is whitespace-free, at least 32 bytes and
must not reuse any common credential. The registration keyring entry for the
effective LangGraph Worker ID binds that registration token to
`http://ai-agent-langgraph-worker:5000` and includes exactly this complete
Hub-side allowlist (the Worker cannot expand it through self-reporting):

```json
"allowed_capabilities": [
  "planning", "analysis", "research", "source_analysis", "coding", "implementation",
  "review", "testing", "verification", "workflow.adapter.langgraph"
]
```

The Hub persists `strict_registration_keyring_v1` provenance; a legacy database
row is never an authorization source for scoped Worker routes.

The formats and a safe generation example are documented in
[Temporal Runtime Operations](temporal-runtime.md#install-and-configuration),
because both overlays intentionally use the same Hub workflow-secret contract.
Do not put secret values in Compose YAML, `.env`, images or fixtures.

Export the common paths listed in
[`docker/compose-next/README.md`](../../docker/compose-next/README.md#native-runtime),
then add:

```bash
export ANANTA_WORKFLOW_WORKER_LANGGRAPH_REGISTRATION_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-registration-token
export ANANTA_WORKFLOW_WORKER_LANGGRAPH_SERVICE_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-service-token
export ANANTA_WORKER_LANGGRAPH_SESSION_SIGNING_KEY_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-session-signing-key

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.langgraph.production.yml \
  --profile langgraph config --quiet

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.langgraph.production.yml \
  --profile langgraph up -d --build
```

`INITIAL_ADMIN_PASSWORD` and `POSTGRES_PASSWORD` are still required by the
full stack. Always use `config --quiet` in automation so expanded application
settings are not written to logs.

## Provider activation

Installing the runtime does not activate graphs. Activate LangGraph through a
reviewed Worker profile with Hub-owned state:

```json
{
  "providers": {
    "langgraph": {
      "enabled": true,
      "mode": "local_live",
      "state_policy": "hub_owned",
      "checkpoint_policy": "hub_owned",
      "external_calls_allowed": false
    }
  }
}
```

Provider credentials remain references managed by the normal provider
middleware. They must not be copied into checkpoint metadata or the delegated
payload. Cloud mode additionally requires the existing Hub policy and approval
gates.

The v1 checkpoint gateway supports the root LangGraph checkpoint namespace.
A non-empty `checkpoint_ns` is rejected instead of being silently mixed with a
different state scope. Namespace expansion requires its own conformance gate.

## Verification

Run the static Compose/security contract and the dependency-safe unit suite:

```bash
python -m pytest -q \
  tests/security/workflow_runtime/test_langgraph_checkpoint_production_compose.py \
  tests/test_langgraph_checkpoint_gateway_service.py \
  tests/test_langgraph_checkpoint_internal_api.py \
  tests/test_langgraph_checkpoint_worker_adapter.py
```

Inside the LangGraph Worker image, the live-extra test compiles a real
`StateGraph`, persists typed message state through the Hub adapter, constructs
a new saver and resumes from the signed checkpoint:

```bash
python -m pytest -q tests/test_langgraph_checkpoint_live_extra.py
```

Promotion requires successful tamper, concurrent-write, stale-version,
stale-fence, cross-tenant and cross-runtime tests. A skipped live-extra test is
not evidence for promotion of the LangGraph image.

## Rotation, recovery and rollback

Rotate the authorization keyring by distributing old and new verification keys
to the Hub, switching the active key, draining old tasks and only then removing
the previous key. Rotate the service-token source file atomically and recreate
the Hub and dedicated Worker together. Never fall back to inline tokens.

After a Worker crash, a newly leased Worker receives a higher fencing token and
may read the previously signed checkpoint; the stale Worker is denied. A Hub
restart uses the SQLAlchemy-backed checkpoint and ownership stores. Revision
conflicts are surfaced and never converted to ephemeral success.

Rollback by disabling the LangGraph provider profile and removing the
production overlay from the Compose command. Signed checkpoint history remains
in Hub persistence for audit and later controlled recovery; do not delete it as
part of application rollback.

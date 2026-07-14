# Workflow Runtime Threat Model

Status: production security contract for Native, LangGraph and Temporal workflow
runtimes. The machine-readable gate is
[`workflow-runtime-security-gates.v1.json`](workflow-runtime-security-gates.v1.json).

## Scope and non-negotiable architecture

The Hub is Ananta's only control plane. It authenticates users, compiles the
runtime-neutral `ExecutionPlan`, selects a compatible runtime, owns policy,
approval, budgets, the task queue and canonical events. Workers execute exactly
one Hub-delegated step. Workers never select, call or orchestrate other workers.
Temporal supplies durable timers, history and Activity scheduling, but it does
not own the Ananta task queue. A Temporal Activity can only submit a signed
command back to the Hub task gateway.

This model covers:

- Native graph orchestration and delegated worker execution;
- LangGraph as an optional worker runtime;
- Temporal Server, the dedicated Temporal worker and Activities;
- LangChain only as an optional provider, tool or retriever integration;
- provider and tool egress, persistence, telemetry and Angular operations UI.

Out of scope are compromise of the host, Docker daemon, container runtime or
database superuser. Those identities can replace executable code or stored
data. Production therefore also needs host hardening, encrypted backups,
restricted Docker access and TLS/mTLS as described in the
[rollout runbook](../operations/workflow-runtime-rollout.md).

## Protected assets and security invariants

Protected assets are tenant identity, plans and policy versions, delegated task
payloads, authorization/signing/encryption keys, approvals, provider
credentials, artifacts and provenance, canonical events, checkpoints, Temporal
history, side-effect ledger records and release-gate evidence.

The following invariants are release blocking:

1. Only the Hub may create or control Ananta tasks.
2. A runtime may execute only the tenant/run/step and tools signed into its
   current authorization envelope.
3. Provider text, retrieved content and worker output are untrusted data; none
   may mutate the plan, policy, approval or capability set.
4. A write is not successful until authorization, policy, approval, ownership
   fence, side-effect ledger and artifact evidence agree.
5. Unknown outcomes are `uncertain`; stale or inconsistent history is never
   `current`; unverified output is never successful evidence.
6. State contains secret references, not secret values. Logs, events, streams,
   heartbeats and UI read models are bounded and redacted.
7. Runtime fallback and rollback fail closed when any required security or
   durability capability would be lost.
8. Runtime rollout has no unscoped compatibility path: plan scope, side-effect
   classes and egress destinations are checked before delegation.
9. Shadow promotion evidence is derived from successful canonical Hub event
   sequences, signed by the Hub, owner-only, fresh and bound to tenant,
   workflow, runs, plan, policy revision, runtime builds and source revision.
10. Authorization signing material is Hub-only. Workers may verify Ed25519
    signatures or revalidate online at the Hub, but cannot mint an envelope.
11. Internal runtime/checkpoint gateways accept service identities only; a
    user or administrator JWT is never equivalent to a Worker credential.

Optional OpenTelemetry export is downstream of canonical event persistence. It
uses TLS for remote endpoints (or an internal/local Compose collector), reads
headers only from an absolute read-only secret file, emits fixed-cardinality
attributes and replaces payloads above 16 KiB with digest metadata. Spans are
neither state nor success evidence.

## Trust boundaries

```text
Browser / Angular
        | TB-01: authenticated, tenant-scoped Hub API
        v
Hub control plane ---- TB-02 ---- Hub persistence
        |
        +---- TB-03: signed delegation ----> Worker runtime
        |                                      |
        |                                      +-- TB-04 --> Provider / tool
        |
        +---- TB-05 ----> Temporal Server ---- TB-08 ---- Temporal persistence
                               |
                               +-- TB-06 --> Temporal worker / Activity
                                                 |
                                                 +-- TB-07 --> Hub task gateway
```

| Boundary | What crosses it | Required control |
| --- | --- | --- |
| TB-01 UI–Hub | JWT/session identity, read queries, approved commands | TLS, strict authentication, tenant derivation from identity, payload bounds, redaction, CSRF/origin controls where cookies are used |
| TB-02 Hub–persistence | events, checkpoints, leases, ledger, evidence | tenant keys, transactions/CAS, append-only events, encryption at rest, least-privilege DB role, backup validation |
| TB-03 Hub–worker | one delegated step and artifact references | signed short-lived authorization, encrypted task payload, plan/policy binding, ownership fence, no worker orchestration |
| TB-04 Worker–provider/tool | redacted prompt/context or allowlisted call | default-deny egress, destination and tool allowlist, budget/deadline, approval, provenance and side-effect ledger |
| TB-05 Hub–Temporal | start/query/signal/cancel/history | TLS/mTLS or API key, namespace/task-queue allowlist, tenant/run binding, bounded history reads |
| TB-06 Temporal–Activity | workflow state and Activity input | deterministic workflow code, bounded secret-free payload, finite retry class, content-free heartbeat |
| TB-07 Activity–Hub | signed delegated command and receipt | internal service authentication, replay store, Hub revalidation, operation-id and fence binding, encrypted persistence |
| TB-08 Temporal–persistence | history, visibility and task state | isolated DB role/network, encrypted backup, retention, official schema migration and replay test |

Angular is not trusted to make policy decisions and must never contact a worker
or Temporal directly. The separately exposed Temporal UI is an administrative
surface, not an Ananta user API; restrict it with its own authentication and
network policy in production.

Every full Hub user session carries an explicit `tenant_id`. Local and
OIDC-linked Hub accounts receive a stable one-account tenant derived by the Hub
from the persisted username; no caller-provided tenant is trusted. This keeps
the tenant equal to the pre-existing local workflow fallback and therefore does
not orphan earlier runs. Usernames used for new sessions must be canonical,
bounded identifiers: leading/trailing whitespace, control characters and values
longer than 160 characters fail closed instead of being trimmed or truncated.
Legacy accounts that violate this invariant require an explicit,
administrator-controlled data correction or migration before another session can be issued,
preventing two distinct accounts from collapsing into one tenant. Ananta does
not currently expose an in-place username-rename endpoint.
OIDC accounts auto-provisioned before explicit issuer/subject links were
introduced also require a controlled one-time link or migration. The Hub does
not infer ownership from a matching email address because two external
subjects may legitimately publish the same address.
The tenant-strict Operations API rejects otherwise valid tokens that omit this
identity; older full-session tokens retain the established subject-derived
tenant on compatibility routes until login or refresh replaces them.
Restricted derivative tokens, such as Control-Center stream tokens, do not
inherit workflow access merely because their parent session had a tenant.

## Threat analysis and control mapping

The JSON gate contains the normative, detailed prevention, detection, audit and
test mapping. This table is the operator summary.

| ID | Threat | Prevention | Detection and audit | Automated proof |
| --- | --- | --- | --- | --- |
| WRT-001 | Prompt injection changes control flow | Hub-owned plan; provenance allowlist; strict schema; policy-gated tools | provenance/schema/tool denial reason; plan hash and redacted correlation trail | retrieval contract and tool pipeline tests |
| WRT-002 | Tool escalation | signed allowlist and budgets; approval; fence; stable ledger operation | deny before invoker; audit operation, gate, attempt and ledger state | tool pipeline and side-effect ledger tests |
| WRT-003 | Confused deputy | tenant/workflow/run/step/plan/policy binding; mandatory Hub rollout scope; Activity returns to Hub | binding mismatch and cross-tenant access fail closed; actor and decision audit | authorization, rollout security, Hub gateway and tenant API tests |
| WRT-004 | Replay abuse | expiry, nonce, idempotency key, dedupe, CAS and stable operation ID | replay/dedupe conflict reason; combined retry count | command, gateway and repeated Temporal signal tests |
| WRT-005 | State poisoning | bounded versioned contracts; secret references; pure upcasters; declarative conditions | schema/hash/sequence/quarantine reason without rejected content | state, evolution, plan and condition tests |
| WRT-006 | History/checkpoint tampering | signed bound checkpoints; versioned deterministic projection | stale/inconsistent on gaps, loops or signature/revision mismatch | checkpoint and Temporal projection tests |
| WRT-007 | Cross-tenant exposure | tenant-bound stores/cache/provider context/read models; identity-local production Worker volumes | not-found or denial without object disclosure; tenant-safe telemetry; merged Compose rejects shared Worker roots | retrieval, telemetry, projection, API and production Compose tests |
| WRT-008 | Provider egress/secret leak | default-deny egress and pre-transport redaction | destination/budget denial and content-free observation | provider middleware tests |
| WRT-009 | UI success spoofing | Hub read model with staleness/evidence; approved commands | missing evidence degrades; stale commands denied and audited | operations API and Angular tests |
| WRT-010 | Retry/resource amplification | combined retry budget; bounded fan-out, streams, history and payloads | explicit exhaustion/backpressure/threshold state | ownership, streaming and Temporal retry tests |
| WRT-011 | Credential injection/runtime impersonation | Hub-private Ed25519 signer; Worker-public verifier; scoped internal routes; per-identity registration/service tokens; Hub-provisioned capability allowlists; `strict_registration_keyring_v1` provenance; no-follow, descriptor-verified bounded secret and OTEL-header reads; file-managed service credentials excluded from URL queries; purpose-bound user SSE derivative limited to its exact route | legacy provenance, private/miswired, symlinked, hardlinked, writable, oversized or malformed credentials and user JWTs fail closed; merged Compose model proves secret separation | registration/provenance/capability, Ed25519 forge/private-field, internal-auth, file-auth, telemetry-header and production Compose tests |
| WRT-012 | Forged or stale shadow promotion | canonical-event-derived invariants; Hub Ed25519 signature; owner-only evidence; policy/build/revision/expiry binding; service-level approval | stable signature, binding, freshness and approval denial reasons; immutable rollout audit | shadow comparison and rollout security tests |

### Prompt injection

Prompts, documents, repository text, provider output and tool output are data.
They cannot create an `ExecutionNode`, expand `allowed_tools`, issue an approval
or choose a runtime. The Hub validates the plan before delegation. Retrieval
accepts only caller-provided, tenant-bound source identifiers; a missing or
unknown identifier is a failed grounding check and is never synthesized. Tool
arguments are schema checked after the model response and before the tool
pipeline. A malicious instruction such as “ignore policy and publish” therefore
ends as a policy/approval denial, not a new control-plane instruction.

### Tool escalation and confused deputy

The authorization envelope binds the least privilege required for one step.
The worker verifies it locally, and writes require current Hub revalidation.
The Hub then checks policy, budget and approval, claims ownership with a fencing
token, and uses a stable side-effect operation ID. A model-facing tool adapter
must not expose the internal Hub gateway or accept an arbitrary tenant/run.
Temporal Activities use the same contract: they cannot select a worker and a
valid Temporal command for one run cannot be reused for another.

### Replay and uncertain outcomes

At-least-once delivery is expected. Repeated transport delivery is safe only
because nonce consumption, command idempotency, event dedupe, ownership CAS and
the side-effect ledger are checked together. A timeout after a possible write
is `uncertain`. Operators reconcile the stable operation against external
evidence; they never retry a non-idempotent write merely because Temporal or a
worker timed out.

### State, history and checkpoint integrity

State and checkpoints reject embedded secrets and bind the plan/policy/runtime,
revision and fence. Event stores enforce tenant scope and monotonic sequence.
Unknown schema versions are quarantined. Temporal history projection tracks the
Temporal Event ID, page cursor, mapping version and consistency. Gaps, page-token
loops, binding conflicts and mapping changes force `stale` or `inconsistent`.
Promotion after a mapping change requires a full rebuild and deterministic
comparison; changing a status in the UI or visibility store is not recovery.

### Runtime credential isolation

Production uses two distinct authorization files. Only the Hub receives the
Ed25519 private signing keyring. Native, LangGraph and Temporal workers receive
the public verification keyring, which rejects private keys, legacy symmetric
keys and unknown fields; Native also supports online Hub revalidation without
any local key. Only the Hub can decrypt dispatch payloads. Shared HMAC is
disabled by default and survives solely behind an explicit development flag.

The production Compose boundary also separates data-plane reachability. Only
the Hub joins the network containing central PostgreSQL and Redis, and neither
service publishes a host port. Native workers use the dedicated
`workflow-worker-control` network, LangGraph uses `langgraph-runtime`, and the
Temporal worker uses `temporal-runtime`; those workers do not join the Hub data
network. This network control is independent of environment-variable redaction
and prevents direct access even after a Worker process compromise.
Angular is restricted to the separate `workflow-ui-control` network shared only
with the Hub. The production overlay removes its writable source/cache mounts
and keeps the development server's host validation enabled; development
overlays retain hot reload as an explicitly non-production convenience.

Filesystem reachability is isolated independently from the network boundary.
Only the Hub retains the operator-selected `project-workspaces` host bind.
Native Alpha, Native Beta and LangGraph each receive a different Docker named
volume as their complete writable `/project-workspaces` root; none can mount the
Hub tree or another Worker's tree. The Temporal Activity Worker has no workspace
mount at all and keeps its image filesystem read-only. Cross-worker inputs and
results therefore travel through Hub-owned task, context and artifact contracts,
not through an implicit shared filesystem. Persistent Worker volumes remain an
operator lifecycle concern and must be removed or archived according to the
tenant-retention policy. Development Compose deliberately retains its shared
bind mount for local iteration and must not be used as a production overlay.

Internal runtime, tool-decision and checkpoint routes require an agent service
credential/service JWT and reject browser user/admin JWTs. The Angular frontend
receives no runtime credential. Base Temporal and smoke overlays stay
credential-free, so a probe cannot become a productive Activity executor.
File-managed service tokens are absolute, bounded, checked on every request,
rejected on unsafe configuration and never accepted in a URL query. In this
production file-managed mode, the sole query-token exception is a short-lived,
purpose-bound user derivative with an exact tenant/user binding. It is accepted
only by `GET /api/events/stream` and cannot authenticate another HTTP or
WebSocket route. Failures expose only a stable reason code, not a key, token or
secret-file path.
The Hub also validates credential disjointness before it starts serving: the
user-session signing secret, Hub service token, persisted Worker service
tokens, registration tokens and runtime service tokens must all belong to
different trust domains and values. New Worker registration repeats this check
before persistence, preventing a service bearer from doubling as a user-JWT
signing key.

## Security gate and critical findings

Production profiles must load only release evidence whose contract hash matches
the deployed contracts. The gate fails when:

- a `critical` threat in the JSON gate has status `open`, `unmitigated` or
  `unverified`;
- prevention, detection, audit or an automated test mapping is missing;
- a mapped test no longer exists or does not pass;
- a profile enables a runtime without the required capabilities;
- a projection is stale/inconsistent, an operation is uncertain, or evidence is
  unverified and the rollout attempts promotion or rollback.

There are no accepted-risk exceptions for critical findings. A temporary
exception must keep the production runtime disabled or shadow-only and be
represented as an open critical finding until verified mitigation lands. High
or lower findings require an owner, expiry and deployment-specific decision;
they cannot waive a required capability.

Validate the policy without network access:

```bash
python scripts/validate_workflow_runtime_docs.py
pytest -q tests/security/workflow_runtime/test_workflow_runtime_security_docs.py
```

The required CI job also runs the mapped runtime security regression files. Test
fixtures are deterministic, contain no credentials, live data, random IDs or
volatile timestamps.

## Operational detection and incident triggers

Page the runtime owner and block new production runs for the affected scope on
any signature failure burst, replay conflict with different payload, fence
conflict, cross-tenant denial anomaly, unexplained provider destination,
checkpoint/history inconsistency, duplicate external effect, key exposure or
unverified-success display. Preserve canonical event and ledger references,
revoke affected commands/keys, isolate egress, and follow the
[incident procedure](../operations/workflow-runtime-rollout.md#incident-response).

Do not copy prompts, decrypted task payloads, provider credentials or raw
Temporal payloads into tickets. Use redacted event, operation, checkpoint and
release-evidence references.

## Verification inventory

The mandatory suite is rooted in:

- [`tests/test_workflow_runtime_security_and_evolution.py`](../../tests/test_workflow_runtime_security_and_evolution.py)
- [`tests/test_workflow_runtime_commands_components.py`](../../tests/test_workflow_runtime_commands_components.py)
- [`tests/test_workflow_runtime_side_effect_ledger.py`](../../tests/test_workflow_runtime_side_effect_ledger.py)
- [`tests/test_workflow_runtime_event_and_checkpoint_stores.py`](../../tests/test_workflow_runtime_event_and_checkpoint_stores.py)
- [`tests/test_tool_calling_pipeline.py`](../../tests/test_tool_calling_pipeline.py)
- [`tests/test_temporal_history_projection.py`](../../tests/test_temporal_history_projection.py)
- [`tests/test_temporal_runtime_contracts.py`](../../tests/test_temporal_runtime_contracts.py)

Temporal replay and race drills are described in the
[Temporal operations runbook](../operations/temporal-runtime.md). Rollout,
backup, restore, rotation and incident drills are in the
[workflow runtime rollout runbook](../operations/workflow-runtime-rollout.md).

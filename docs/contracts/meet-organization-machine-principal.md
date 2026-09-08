# Hub-owned Organization machine principals

MAP-05/MAP-28 source audit: Ananta `e5c96e093`, Meet `3441454`.
Current Meet v2 already accepts an opaque signed subject and pins it in all
membership/renewal/observation bindings. Ananta still signs every dialog as
`ananta`, including role-bound Organization Tasks. Its existing role gate
already checks the exact active assignment, registered publisher, topology
and policy snapshot. Persona `agent` owners refer to that assignment ID,
not a display name or an invented additional Agent database ID.

## Additive implementation contract

- Add an explicit Hub-only `ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED=1`
  option for **new** Organization dialog Tasks. Default/legacy Tasks retain
  their existing `ananta` identity; no in-flight identity retrofit. Missing
  eligible role assignment in the enabled path fails bounded before dispatch.
- The existing checked role-assignment port returns its verified binding.
  A separate pure model derives a stable opaque `org-agent-<sha256>` subject
  from the exact tenant/project/Organization/role-slot/assignment/publisher
  tuple. Store the closed principal projection beside the role binding in
  the same Hub Task aggregate. This is an authentication principal, never a
  `SRC_*`/`RUN_*` identity or release evidence. Do not expose publisher URLs.
- Every grant and backchannel revalidates the existing lifecycle/assignment
  and compares the stored principal to that verified binding. Disabling
  new-principal admission cannot turn an existing assigned principal back
  into `ananta`. No Worker or start-request field chooses its identity.
- Freeze principal admission/removal/replacement and its Task scope at the
  authoritative Task-write seam, including active Tasks. Controls, current
  audio jobs and lifecycle phases may still evolve; terminal fencing stays.
  Keep ordinary Task retry/administration behavior unchanged.
- Use the existing closed v2 grant payload: only its already supported
  `sub` changes. Meet independently compares the opaque subject and exact
  tenant/project/capabilities to its operator-pinned profile. The Hub owns
  the subject-to-Organization/assignment mapping; Meet does not claim to
  query or verify Ananta's Organization database independently.
- Add an authenticated read-only identity receipt for an authorized active
  dialog, allowing automation to inspect the Hub mapping without obtaining
  signing keys, grants, dispatch credentials or Worker URLs. Existing
  status/phase/start and Worker contracts remain unchanged.

The large Task adapter/Meet service are existing SRP debt. Add only narrow
composition calls; derivation/validation/write policy and receipt access
belong in focused model/service modules. Reuse the current assignment read,
not a second identity directory or scheduler (SRP/DIP/OCP).

## Verification

Real SQL admission, immutable active-write attempts, restart/reconstruction,
assignment/publisher/topology revocation and legacy compatibility; ephemeral
real Hub signatures verified by Meet; two distinct assignment principals
with same human-visible name and disjoint scope pins; no cross-subject
renewal/observation. All checks are bounded and headless. Production trust,
running rooms, private operator configuration and unrelated GPU workloads
stay untouched. Multi-agent media fairness, dynamic persona ownership and
public TURN/soak remain separate Tasks, not completed by identity tests.

## Source-checked provisioning dependency

The first implementation and its 188 model/SQL/regression checks are green;
two actual Hub role identities have also crossed Meet HTTP/WS with exact
subject isolation. Before finalizing provisioning, add a read-only **parent
Task preflight**: an active-dialog receipt alone is too late to pin a new
subject before the first Meet join. Resolve the same current eligible role
and operator-configured publisher from an authorized parent Task, without
inventing Task/lease/runtime IDs or creating a dialog. Return its candidate
principal explicitly as `preflight_only`; actual start still revalidates
everything. Reuse the existing role-resolution port, with pure derivation
from its typed scope/facts. The caller cannot select a Worker/publisher.
Test no task ingestion/dispatch/signing and revoked/foreign parent denial.

## Implemented API and rollout order

With the dialog feature enabled on the Hub, the additional startup flag
`ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED=1` enables principal admission
for new role-bound Organization dialogs and the parent preflight endpoint:

`GET /api/meet/v1/projects/<project>/tasks/<parent_task>/machine-principal`

It requires the existing authenticated user's project/Task write authority,
an active parent/topology and an eligible assignment of the configured
publisher. Its closed `ananta.meet-machine-principal-preflight.v1` response
contains `issuer`, `parent_task_id`, `principal` and `preflight_only: true`.
It does not create a Task, reserve a lease, sign a grant, join Meet or
authorize a later start. A changed role or revoked permission is checked
again at admission; a preflight response cannot be submitted as authority.

Automation may use the returned subject and exact tenant/project to prepare
the separately operator-authorized public Meet trust profile. Pin the
configured issuer, key, capability ceiling and version through the existing
versioned trust/preflight contract. Neither endpoint writes that profile or
reloads a running server. No private key or publisher URL is returned.

After normal Task-backed start, its owner can inspect the fresh admitted
identity at:

`GET /api/meet/v1/projects/<project>/dialogs/<dialog_task>/principal`

This returns `ananta.meet-machine-principal-receipt.v1` with `issuer`,
`task_id` and `principal`. It remains available for an already admitted
principal if new-principal admission is subsequently disabled, but only
while the original Task/lease/assignment/owner remain authorized. Legacy
dialogs return `meet_dialog_organization_principal_unavailable`, never a
manufactured Organization identity. Both routes are bodyless, reject query
overrides, set `Cache-Control: no-store`, and cannot be called by Workers.

The pure identity model, fresh SQL role port, Task write policy and two
read-only services stay separate (SRP/DIP). Existing orchestration and
Worker payloads do not gain another identity authority. The two-subject
interop test represents two role assignments on one configured publisher;
it does not establish multi-Worker media capacity or production readiness.

## Verification checkpoint, 2026-09-08

The main 307-test model/SQL/authority/route/interoperability regression passed
in 114.42 seconds; the additional model/bootstrap selection checks passed
45 tests in 25.03 seconds. Exact subject isolation was exercised using actual
Hub-signed grants, two real SQL role assignments and Meet's P-256/HTTP/WS
admission, including cross-subject renewal and observation denial.

The original named-memory browser attempts exposed SQLite shared-cache
locking. An incorrectly isolated diagnostic is excluded entirely (see
`docs/operations/test-runtime-isolation.md`). With the harness-owned WAL
database, both legacy and new Organization-principal browser variants passed
in 99.10 seconds. They use actual private Hub/Worker/Meet processes, moving
screen media, two correlated chat replies, independent source changes and
persisted terminal state. No source assertion, grant policy or Worker timeout
was relaxed. The receipt is checked against the exact admitted Task identity.

These are synthetic/private technical checks. They do not complete public
TURN/production rollout, multi-Worker capacity or the separate intermittent
SFrame startup investigation. No full-project test-suite completion is claimed.

Final combined regression after key-file and test-isolation integration:
400 checks passed in 142.53 seconds. Ruff and the 77-file Worker packaging
boundary check pass. The two correctly isolated WAL browser results above
remain the applicable media verification; the earlier invalid diagnostic
is not counted.

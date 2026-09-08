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

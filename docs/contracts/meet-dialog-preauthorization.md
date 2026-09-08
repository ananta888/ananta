# Durable exact-scope dialog preauthorization (MAP-05)

Source audit: Ananta `0cc42eec7`, companion Meet `ba67caa`. The existing Hub
requires static tenant/project capability policy, current project/task and
Organization role authority, signed machine grants and acknowledged Meet
receive/publication rights. Key-only provisioning exists. However static
`ANANTA_MEET_DIALOG_POLICIES` alone does not bind operator preauthorization to
an exact owner, parent Task and room, nor persist revocation/dispatch budgets.
The parallel Meet deployment validator handles public trust, not this Hub gap.

Add an optional stricter Hub-owned preauthorization boundary:

1. A closed operator document specifies policy ID, tenant/project, exact parent
   Task and owner, canonical Meet origin and exact room, allowed capabilities,
   bounded validity, maximum session duration and maximum admitted dispatches.
   No wildcard scope, invented evidence identity, Worker-selected policy or
   capability expansion beyond the existing static operator policy.
2. A dedicated Hub SQL store keeps current policy revision/status, used dispatch
   count and content-free audit digests. Provision/update and revoke are CAS
   operations. Replacing a policy increments its revision; old Tasks must not
   inherit a renewed grant or revive after revocation. Store/clock errors deny.
3. Before ordinary Task ingestion, reserve one dispatch against that exact
   policy under a database lock, atomically checking validity and budget. A
   failed/uncertain start burns this allowance; no retry can redispatch it.
   Bind policy/revision and a closed assignment digest to the ordinary Task
   outside its unchanged v1 `meet_dialog` projection. No extra Worker envelope
   fields or Worker orchestration. Existing role and source checks stay intact.
4. Revalidate the exact current policy and original assignment binding on all
   existing authority checks, therefore also renewal and output. Revocation
   removes permission through the existing Hub freshness/stop fences. A Task
   carrying this binding may never fall back to legacy policy when the optional
   provider is disabled or missing. Legacy mode remains unchanged by default;
   enabling the stricter mode denies unbound old Tasks and requires new starts.
5. Provide a headless local operator CLI using the Hub's configured database,
   bounded private-file input and explicit expected revision. It provisions or
   revokes policy only; it does not create a room, join, sign a grant, dispatch
   work or modify Meet trust. The operator's OS identity is auditable; no input
   body, room invite, private path, database credential or arbitrary exception
   appears in command output. Never activate a real policy as part of tests.
6. Cover document mutation, scope/owner/room/task substitution, revision races,
   restart, expiry, budget exhaustion, ambiguous/uncertain writes, no fallback,
   automated CLI and the actual Hub start/current/renewal/stop paths. Verify
   compatibility with the unchanged legacy wire and terminal immutability.

SRP/ISP/DIP: document and assignment validation, persistence, authority adapter
and operator I/O are separate. Existing broad dialog composition/authority
modules remain preserved debt; they receive only narrow calls, not SQL, file
parsing or CLI behavior. The Hub remains the only issuer and task owner.
No production flag, key, public trust or operator scope is activated by this
implementation. Deployment/public TURN and other unfinished MAP tasks remain
separate verification obligations.

Packaged acceptance profile, fixed before execution: use the existing two
isolated Worker/role/screen/persona-speech fixtures with explicit synthetic
per-parent policy (existing 120-second screen / 180-second persona-speech
session ceiling, one burned dispatch each).
After ordinary cancellation of the first Task, revoke only the second operator
policy through its SQL CAS while its Worker is still active. Require the real
receiver to become alone within five seconds, a failed second Hub Task, and
the new signed terminal observation. The existing 2.5-second local control
freshness and all media/queue/lease limits are unchanged; five seconds is the
separate end-to-end policy-write/receiver-departure budget, not a relaxed local
stop fence. No explicit Task cancellation may satisfy this second assertion.

The first packaged run failed both cases (73.78s). The screen-only policy
revocation did remove the receiver's peer, but the fixture incorrectly compared
the pre-finish Task to its subsequent legitimate terminal transition. Wait for
that transition before capturing the immutable terminal snapshot. The media
case correctly rejected a 180-second existing assignment under the test's
incorrect 120-second policy. Derive the explicit test policy duration from the
existing scenario rather than change either policy enforcement or media
duration. Both failures are retained; rerun the corrected fixtures below.

Corrected packaged result: both cases passed in 114.81s against immutable
Worker `3951a3feaa58ddb174df884e4204a04664b8bbdc9186313b25450499234c9aac`
(source `67fa83f4b`) and private Meet `ba67caa`. Exact operator policy revocation
removed the still-running second participant at the real receiver in 118.90ms
(screen) and 815.17ms (persona/speech). No separate second Task-stop operation;
its ordinary callback then recorded `failed` and its signed content-free
diagnostics. The first independently cancelled Task remained cancelled. No
human captures, proxy drops or transform errors. Current Hub source `e59e3b9d8`.
The separate immutable Meet trust/rotation/HTTP/preflight suite also passed all
112 cases in 1.965s, no skips. No public serving build or operator trust changed.

## MAP-05 acceptance mapping

All five implementation criteria now have concrete source and verification:

| Criterion | Implementation and verification |
| --- | --- |
| Issuer and tenant/Organization/agent identity | `MeetMachineGrantIssuer`, `meet_machine_principal` model, current SQL role-assignment checks, parent preflight and owner receipt; actual two-principal Meet interop |
| Versioned independent Meet trust | Companion `machine-trust-profile.js` / `machine-grant-trust.js`: exact issuer/audience/EdDSA/scopes, bounded overlapping key windows, replay and removal; real HTTP/WS three-renewal test |
| Headless provisioning/renewal/revocation | Key-only provisioner, exact-policy operator CLI/SQL CAS, existing room allocation and signed renewal APIs; real policy withdrawal removes a running packaged participant without Task-stop assistance |
| Machine/nonhuman credentials and boundaries | Existing closed v1 Worker assignment and Hub-private key loading, scoped short-lived grants; real nonroot read-only packaged Workers contain no Hub package or signing-key mount, and human captures remain zero |
| Exact preauthorization without inferred approval | New immutable policy/dispatch binding and additional current-authority checks for tenant/project/owner/parent Task/room, validity and burned allowance; missing/expired/revoked policy and provider removal deny without legacy fallback |

Final identity regression: 155 root tests passed in 105.48s, including actual
ephemeral keyed v1/v2 signatures accepted only by Meet's matching trust, two
Organization principals with the same display name, private-key file safety,
headless CLI, current assignment revocation and owner-scoped preflight/receipts.
This closes MAP-05's implementation, not MAP-08 startup intermittence, MAP-11
reconnect/recovery, MAP-31 public activation/TURN/soak or production release.
Trust replacement is explicit configuration/restart (which ends memberships),
not a claim of seamless arbitrary hot reload. See
`docs/operations/meet-dialog-preauthorization.md` for the exact operator steps.

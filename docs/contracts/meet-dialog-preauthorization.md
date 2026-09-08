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

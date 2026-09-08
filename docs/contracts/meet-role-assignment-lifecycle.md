# Publisher role assignment admission (MAP-09/20)

Source audit at `ac34c1351`: topology is current, but a Meet dialog still has no
persisted role assignment or registered publisher binding. An active slot alone
does not authorize the configured media Worker. The existing generic planning
dispatch resolver is specific to its outbox/revision lifecycle and must not be
misused as a long-lived Meet authority.

Add a narrow Hub-owned Meet role-binding service with an exact-scope SQL metadata
port and a pure closed snapshot validator. For role-scoped dialogs, select only
the active assignment for the configured publisher's exact registered origin;
require validated online Worker registration, explicitly authorized dialog and
slot capabilities, allowed agent principal kind and slot write-access policy.
Persist the assignment ID and immutable snapshot digest with exact task,
parent, dispatch lease, runtime and organization tuple. Reread them before each
current dialog authority and before delegated media/ASR work. Missing legacy
role bindings fail closed; standalone and organization-only dialogs remain
compatible. Never select another Worker implicitly or accept a caller URL.

Changes to assignment incarnation, policy or authorization require a fresh
session. The digest is a local binding integrity fence, not a Hub Evidence
Registry identity or cryptographic authorization proof. No SRC/RUN issuance,
Meet principal/wire expansion, automatic registration, deployment or trust
change. Registered publisher identity is not yet a distinct organization/persona
principal visible inside Meet. Capacity reservation/fairness stays a separate
existing resource policy; this slice must not claim to solve concurrent global
Worker admission.

First reproduce revocation with real SQL. Cover missing/suspended/ended or
foreign assignments, malformed policies, unregistered/offline/unvalidated
Workers, capability loss, changed assignment incarnation, binding tampering,
no grant/dispatch on denial, and cleanup after revocation. Update synthetic
organization fixtures to contain explicit synthetic assignment/registration,
without granting production policy. Run focused regression and a private real
Hub/Worker/Meet assignment-revocation browser gate. SQL, immutable data and
eligibility decisions remain separate (SRP/DIP); do not grow the broad dialog
service or change generic organization routing.

## Implemented boundary

`HttpMediaWorker.publisher_url` projects the exact origin of the configured
private transport (no endpoint path). Bootstrap passes that identity to
`HubDialogTasks`. Role-scoped ingestion writes `assigned_agent_url` and the
separate closed `meet_role_assignment` Hub context. The existing v1 Worker/Meet
assignment wire does not gain a role, URL, snapshot or authority field.

The metadata adapter uses one bounded joined SELECT over the exact scoped
assignment, slot, organization and registered Worker. It never projects tokens,
legacy advertised capabilities, assignment metadata or role overlays. Only
explicit `authorized_capabilities` qualify: clearing them cannot fall back to
the legacy capability list. Policy must contain exactly the four existing
assignment-policy fields with correctly typed values. Missing policy is denied.
The admission requires `meet_dialog_session`; it does not infer codec, GPU or
general execution rights from that capability.

The immutable local snapshot includes assignment incarnation (`assigned_at`),
authorized capabilities, execution limits, complete slot policy, organization
lock version, definition revision and effective policy hash. The existing
topology writer increments the organization lock version on each apply and
updates assignment time when reactivating an assignment. A changed organization
revision conservatively requires a new session, including unrelated topology
edits. No automatic rebase or database schema migration was added. This is not
an independent append-only registry of all directory/lifecycle history: an
out-of-band database edit that restores every original value is outside that
revision guarantee. Distinct principal and broader recovery criteria remain open.

The existing parent lifecycle invokes the narrow role-binding current check for
dialog parents and dialog tasks, so ASR and generated-media children inherit the
same revocation boundary. Cleanup remains possible without reauthorization.
Injected SQL/role ports keep tests headless and self-contained. Value-object
invariants and closed projection are separate from persistence and Hub task
mutation (SRP/DIP); the broad pre-existing dialog service and browser fixture
remain explicit SRP debt and are not expanded with role-policy logic.

Operators must register the exact configured publisher origin and explicitly
authorize it for the role through the existing organization control plane.
This implementation does not register agents, edit production policy or deploy
services. Existing role-scoped sessions without this binding cannot renew;
standalone and organization-level sessions without a slot are unchanged.

## Verification (2026-09-08)

The original suspended-assignment regression failed in 7.58 seconds. Initial
integration passed 12 tests in 15.98 seconds. The expanded run had 148 passes
and one test-fixture FK failure in 60.24 seconds: the intentionally different
assigned Worker had not been registered. Registering that synthetic second
Worker corrected the fixture without weakening SQL constraints. The next 158
tests passed in 66.07 seconds; the broad focused acceptance passed **363 tests
in 133.55 seconds**.

Final review additionally requires a strictly positive integer configured
execution capacity: zero, negative, boolean, string, fractional and null limits
deny admission. This checks the registered execution prohibition, not a global
concurrent reservation or fairness guarantee. The final affected policy,
assignment/SQL, child-fencing and bootstrap suite passed **129 tests in 53.55
seconds**, including twelve new malformed/disabled-capacity cases. Ruff and the
77-file Worker boundary detector passed.

The private real browser matrix passed parent cancellation (202.26 ms Worker
stop) and organization pause (986.16 ms), then failed **before** the role-draining
stimulus on the known intermittent initial screen decode symptom: 348 RTP
packets, zero decoded/key frames, 33 PLIs, 32 healthy source pushes, live Worker
and one transform error. That run had 2 passes / 1 failure in 78.87 seconds.
The separately selected role-draining and assignment-suspension cases then both
passed in **55.77 seconds**, with Worker stop observations of **274.39 / 805.67
ms**. Both first observed moving screen, two correlated replies and completed
scoped media children, then changed only the authoritative SQL lifecycle and
observed terminal cleanup and remote participant removal. No dialog-stop API
initiated revocation. The browser matrix was not one uninterrupted green run.

These are private synthetic-policy/model tests with real Hub SQL, signed
transport and browsers, not GPU, public TURN, production or exact remote-media
latency evidence. Green repeated role gates do **not** fix the initial screen
failure. The companion's `docs/machine-key-startup-regression.md` tracks its next
bounded, content-free diagnostic. Distinct Meet principals, multi-session
isolation/resource fairness and broader lifecycle criteria remain open.

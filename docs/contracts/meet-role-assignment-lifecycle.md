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

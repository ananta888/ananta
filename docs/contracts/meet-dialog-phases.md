# Persistent Hub dialog phases (MAP-09)

Source audit at Ananta `ce70629ac`, Meet `058e4e4`: start receipts and source
observation exist, but status still exposes only the ordinary Task status.
Implement an optional closed phase record alongside `meet_dialog` in the existing
Task execution context, not in the Worker assignment and not in another database.
The same Task aggregate owns persistence, revision and terminal status (SRP/DIP).

New configured starts record queued at Task ingestion, admitted after current Hub
authority, and connecting before Worker dispatch. The existing verified Meet
authorization exchange records joined and pins its exact machine membership;
it does not add a second TLS call to the one-second Worker control path. A separate
explicit Hub observation endpoint reads the machine's own publications and records
joined/publishing with monotone publication revisions. Publishing means registered
sources, never decoded delivery. Status reads neither join nor activate sources.

Transitions use an immutable Task/tenant/project/owner/parent/dispatch/runtime/
session/room/deadline/capability binding and a full-context compare-and-set under
the existing Task mutation/audit path. Concurrent controls cannot be overwritten.
Repeated identical phase events are no-ops; stale/conflicting observations fail
closed with a bounded conflict and no command redispatch. Pin the Meet lease ID
and peer; do not confuse the room's membership counter with a stable lease ID.
Persist no grants, nonce, media, display names or incoming human publications.

Stopping may be recorded before cancellation, but telemetry failure must never
prevent the existing bound cancellation. The persisted ordinary Task terminal
status dominates the last phase; its effective revision is the final phase
revision plus one. This is an explicit deterministic projection of the same
aggregate, not post-hoc history or a second terminal write. Deadline cleanup,
revocation and Worker finish retain their existing cleanup authority and bounds.
No phase observation grants execution authority. Observations expose their age
and freshness; a stored publishing phase alone is not a current liveness claim.

Older Tasks without the negotiated Hub record remain readable through the old
status API; the new phase API reports unavailable and does not invent history.
The optional record never changes the existing start/status/Worker wire shape.
Use a pure transition model, narrow Task-CAS adapter, separate phase coordinator,
and additive access-controlled HTTP routes. Test transition/replay/membership/
publication mutation, clock/revision bounds, restart, real SQL CAS/control races,
terminal precedence, stop on storage failure, access isolation and actual private
browser pause/resume/stop. Synthetic runs remain non-production observations.

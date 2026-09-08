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

## Implementation and checks (2026-09-08)

The pure phase model, `TaskDialogPhases` adapter and `MeetDialogPhases` coordinator
are wired into configured Hub starts and the existing verified exchange. New
bodyless GET/POST `/api/meet/v1/projects/<project>/dialogs/<task_id>/phase` routes
read/refresh only; no automatic polling, joining, source activation or redispatch.
The closed response distinguishes last recorded sources, observation timestamp
and a five-second freshness-at-query bound. Membership IDs and publication digests
remain Hub-only. The Task CAS also pins parent, organization tuple and publisher.

Initial model/SQL integration exposed an incorrect direct archived attribute
access: this TaskDB version has no archived field. The adapter now follows the
existing optional-field compatibility rule, without disabling archive checks.
Then 48 new model/SQL tests passed (28.48 s), 94 combined tests (42.00 s), and the
expanded unique regression passed 220 tests (83.22 s).

The actual private Hub/Worker/Meet browser passed (33.84 s), after moving screen
and two correlated chat replies. Explicit observation recorded publishing ->
joined -> publishing -> cancelled at revisions 5/9/17/19; intermediate refreshes
also advance metadata revisions. Source pause/resume converged in 486.67/1120.65 ms.
A reconstructed coordinator read the same terminal Task and denied refresh.
No resumed-decoding claim or production evidence is derived from this fixture.

The terminal completion audit additionally reproduced a pre-existing general Task
retry bypass. Its separate immutable terminal identity/rename/restore policy is
documented in `meet-dialog-terminal-fence.md`. Phase terminal precedence requires
that shared repository fence; the projection alone is not a retry prohibition.
The existing large dialog coordinator and Task repository remain SRP debt; new
transition, transport and mutation policies are separate narrow modules, with
only additive wiring at these boundaries. No second persistence authority exists.

With the terminal fence included, the final combined regression passed 301
distinct tests in 113.92 seconds. Worker namespace isolation and Ruff checks pass.

Final private browser including that fence: one pass in 39.18 s, phases
publishing/joined/publishing/cancelled, revisions 5/10/19/21 and reconstructed
terminal state. See `meet-dialog-task-completion-audit.md` for criterion boundaries.

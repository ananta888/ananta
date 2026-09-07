# Meet session parent and organization lifecycle (MAP-09/20)

Source audit 2026-09-08: `HubDialogTasks.start` supplies tenant/project explicitly,
so ordinary ingestion deliberately does not inherit its parent's organization
tuple. `MeetDialogAuthority.current` rechecks the session, project write access
and room binding, but does not itself bind the parent's current lifecycle or
organization tuple. The existing general organization dispatch gate does not
replace checks on this directly delegated, already-in-progress media path.

Implement a narrow Hub-only lifecycle port, separate from session/grant/media
logic. Resolve the authoritative parent, require exact tenant/project and a
known active, non-archived task, and carry the whole organization/unit/team/role
tuple when creating its dialog task. Do not accept caller-supplied identities or
invent an organization principal. On every current-authority read, require the
same parent and tuple and reuse the existing organization lifecycle gate. A
missing/inactive organization, missing/terminal/archived parent, changed scope,
malformed scope or policy-read failure returns a fixed bounded denial before
grant issuance or further work. Standalone project sessions remain compatible.

The new port receives task lookup and organization gate dependencies (SRP/DIP).
The existing broad dialog service remains documented structural debt, not a
place for copied SQL/organization policy. No task scheduler, Worker policy,
Meet identity/wire change or deployment is introduced. Legacy organization
sessions with missing inherited scope must fail closed, not silently repair
authority after dispatch. Cleanup of rejected/expired work remains possible.

Tests: reproduce the missing parent fence; deterministic scope/lifecycle/missing
state matrix; real SQL parent/child persistence and active-to-inactive
organization changes; no grant/Worker dispatch on denial; existing standalone
authority/source/profile/control tests; one real private Hub/Worker browser
composition. Audio-child organization propagation, live role-assignment
eligibility/revision and distinct organization/agent Meet principal identity
remain separately open until implemented and verified. Do not close MAP-09/20
or claim production evidence from this bounded slice.

## Implemented and verified (2026-09-08)

`MeetDialogLifecycle` now owns this check through narrow lookup/gate ports.
`HubDialogTasks.start` resolves the parent before ingestion and passes the entire
organization tuple, with team through the queue's separate `team_id` argument.
`MeetDialogAuthority.current` checks parent linkage, exact inherited scope and
current organization lifecycle before room access/authorization. Parent status
uses the existing canonical nonterminal `ACTIVE_TASK_STATUSES`; this is not an
independent authorization to execute the parent's tools or bypass its gates.
Lookup/provider errors have fixed content-free denials. Cleanup remains lease-
fenced and possible after authority loss; no permission, retry or deadline grows.

The original cancelled-parent regression failed in 7.22 s before correction.
Real SQL tests now cover the complete organization/unit/team/slot graph,
parent cancellation/archive, organization pause/archive, no grant/dispatch on
denial and terminal cleanup without resurrection. Two early SQL-fixture mistakes
(missing project creator and assuming a nonexistent TaskDB archive flag) were
corrected against the schema; constraints were not disabled. Those failed runs
are not counted as successful application tests.

Final targeted regression: 216 passed in 82.23 s, including parent/scope,
standalone session, source profile, avatar/voice selection, controls, transport,
routes, deadline cleanup and ordinary task-scope inheritance. The static Worker
boundary check passed all 77 files. Ruff checks and formatting passed for new
files using the locally cached tool.

The private real Hub/Worker/Meet composition passed both lifecycle scenarios in
54.83 s against the current private Meet build. Each first delivered a moving
screen and two correlated chat replies. Only the authoritative parent was
cancelled or organization paused; no dialog-stop call triggered teardown.
Worker completion was observed after 905.41 ms and 663.22 ms respectively, its
own finish callback settled the dialog as failed, and the remote participant
was removed. These times are Worker-stop observations, not a full remote-media
latency benchmark. Model/owner policy and organization data were synthetic;
transport, SQL, signatures and browser execution were real and fully headless.

MAP-09/20 remain open: this does not yet bind a distinct organization/agent
principal in Meet, prove role-assignment eligibility/revision, propagate every
audio/media child scope or provide public/GPU/production release evidence. The
existing broad cross-repository test composition retains SRP debt; the new
lifecycle scenario and graph fixture stay separate rather than embedding their
SQL/model policy in the production dialog service or a Worker.

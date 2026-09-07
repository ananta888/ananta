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

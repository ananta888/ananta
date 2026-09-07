# Current organization topology for Meet (MAP-09/20)

Source audit after `d0ba42f60`: Meet preserves exact organization/unit/team/role
task tuples and rechecks the organization lifecycle. The reused general dispatch
gate checks the organization, but not the referenced unit, team link, team or
role slot. A draining/archived leaf or inactive team could still authorize a
Meet refresh under an active organization. Individual foreign keys do not prove
that a role slot and team link belong to the same unit represented by the task.

Add a narrow Meet-specific topology gate around the existing organization gate.
Use a bounded exact-scope SQL read port: organization must remain active;
referenced unit, team link and role slot must exist and be active; a linked team
must be active; team link and slot must match the task's exact unit. An
organization-level task without those optional leaves remains valid. Malformed,
missing, foreign or inactive references fail closed with content-free decisions.
Keep generic organization dispatch behavior unchanged for other subsystems.

Compose this gate as the default of the existing Meet parent lifecycle port,
with explicit injection for tests. Current dialog, audio and media checks then
share the same topology policy. No Worker orchestration, assignment minting,
new identity, database migration or serving deployment. SRP separates topology
read decisions from task/CAS, grant and transport concerns; DIP keeps SQL session
and existing organization gate replaceable.

First reproduce refresh after unit/team/slot deactivation with real SQL rows.
Cover exact scope and same-unit relationships, partial/missing rows, all
non-active lifecycle states, standalone and organization-only compatibility,
provider failure and no grant/dispatch when denied. Repeat private real
Hub/Worker/Meet teardown after a role slot starts draining, alongside existing
parent/organization scenarios and targeted regressions. Do not close MAP-09/20:
actual role-assignment/registered-agent eligibility and immutable assignment
revision, distinct Meet principal identity and production evidence remain open.

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

## Implementation and verification (2026-09-08)

The default Meet lifecycle now composes the existing organization gate with a
separate topology policy, immutable scope/snapshot DTOs and an injected SQL read
adapter. Strict task-scope parsing has its own small module; the old lifecycle
import remains compatible. The adapter performs at most four additional bounded
leaf-metadata SELECTs, each scoped to the exact tenant/project/organization.
The global team flag is not read without its matching scoped link. No JSON role
policy or agent metadata is loaded. Existing parent and organization reads are
separate from this four-query bound.

The original three stale-leaf cases failed before implementation. Final targeted
acceptance: **205 passed in 78.90 seconds**. This includes foreign and missing
rows, every non-active leaf lifecycle, mixed-unit links/slots, malformed scope,
provider failures, exact SQL projection/query bounds, inherited child scopes and
no grant/dispatch on denied admission. Ruff and the 77-file Worker boundary
detector passed.

The private real Hub/Worker/Meet browser gate passed all three cases in **79.18
seconds**. Each observed a moving shared screen, two correlated chat replies and
two completed media children with the full organization tuple. Changing only the
parent status, organization lifecycle or role-slot lifecycle stopped the Worker
in **254.86 / 304.50 / 1030.11 ms**, respectively. Each task terminated and the
remote participant disappeared without initiating stop through the dialog API.
Those numbers measure Worker stop observations, not a remote-media latency
benchmark. Policy, organization and model fixtures are synthetic; SQL, signed
transport and browsers are real. This is not GPU or production release evidence.

SRP/DIP are protected by separating immutable data, SQL reads and policy rather
than growing the existing broad dialog service. Existing broad service/fixture
responsibilities remain debt. Actual agent assignment/eligibility, immutable
assignment revision and distinct Meet principals are still open; an active role
slot alone does not establish that an agent is entitled to execute it.

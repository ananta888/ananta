# Headless room assignment (MAP-07)

## Source check and additive contract

The Hub currently only attaches a copied invitation. `MeetProfile.public()`
explicitly describes `meet_ui_then_attach`; dialog start requires that existing
binding. Meet's `RoomRegistry.join` creates an ephemeral room on the first
authorized join, not through a separate room-creation API. Generating an invite
therefore cannot claim room existence, membership, moderation or media rights.

Add an explicit, default-denied Hub room-assignment policy and API:

`POST /api/meet/v1/projects/{project}[/tasks/{task}]/binding/allocate`

The only input is `expected_revision`. Use the existing Hub-user authentication,
current project write/task access checks and operator-pinned Meet origin. A
separate exact tenant/project allowlist must permit allocation; media/dialog
opt-ins do not imply it. Never accept a caller URL, room ID, auto-approval flag,
persona or worker/service bearer as allocation authority.

If the exact current binding is already populated, return it unchanged. For an
unbound binding, generate one cryptographically random canonical room identifier
and attach it through the existing revision CAS and content-free audit path.
An intervening attach/unlink or stale revision returns a bounded conflict, not
a new room or overwrite. Reauthorize immediately before the existing write.
Task-specific bindings never fall back to the project room. No network call,
Meet join, participant, grant or source is created by this configuration action.
Actual joining remains an ordinary, separately authorized Hub dialog task.

Keep the existing manual flow/profile fields compatible. The returned binding
retains `room_verified=false` and `membership_granted=false`. This closes the
manual-copy prerequisite for preparing a room assignment, not all MAP-07 or
public provisioning criteria. Existing Meet admission and creator-message
restrictions still apply even if a machine is the first room participant.

Separate the allocation service and its narrow binding port from route parsing,
operator configuration and SQL persistence (SRP/ISP/DIP). Test exact opt-in,
unknown/duplicate fields, foreign users/scopes, disabled/Worker runtime, existing
binding reuse, independent task scopes, random-source failure, reauthorization,
CAS races, persistence and unchanged membership flags. All tests are headless;
do not enable policy, generate production credentials or touch live meetings.

## Implementation and verification, 2026-09-07

`MeetRoomAllocation` depends on a three-method binding port and injected canonical
invite/entropy functions. Operator configuration, strict HTTP parsing, existing
binding persistence and orchestration remain separate. The configuration flag
and exact scope allowlist are exposed in `.env.example` and the existing Meet
Compose overlay, both default-denied. Neither the old binding response/profile
nor the manually attached invitation flow changes.

The initial allocation/binding/API suite passed 93 tests in 41.09 seconds. The
expanded six-file regression passed **113 tests in 47.96 seconds**. An additional
actual top-level bootstrap composition check passed in 7.32 seconds: allocation
works without enabling or starting the media/dialog Worker. The overlay's
default-off/empty-policy values were checked independently. Tests cover real
isolated SQL persistence/CAS, a winning concurrent manual attachment, permission
withdrawal before write, unlink tombstones, no project fallback for tasks,
scope/role denial, bounded malformed config/HTTP input and duplicate JSON keys.

All identities/policies are synthetic. No runtime flags, production database,
public endpoint, credential or serving deployment was changed. The API prepares
the association; it is not a live room-creation, remote participant or public
provisioning acceptance result. MAP-07 remains partial for the wider workflow.

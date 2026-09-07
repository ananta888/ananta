# Automatic exact-video retention

Video retirement now composes the existing bounded Hub retention lifecycle
with its own `persona_video_retention` grant table, audit table and
`persona_video_retention` Hub tasks. Image tables/indexes, grants, task kind
and execution context remain unchanged. Image runners cannot claim video
rows; image task adapters cannot authorize or finish video attempts.

The user-authenticated `/projects/{project}/videos/{artifact_id}/retention`
path below `/api/persona-media/v1` supports PUT, GET and DELETE. PUT requires
exactly `asset_revision`, `expected_revision`, `delete_after_seconds` (60
seconds through 365 days). Only an already-retired exact bundle is eligible.
GET exposes bounded state/revision/due/attempt metadata. DELETE cancels the
exact grant revision, including an active attempt; its next checkpoint cannot
continue or commit success. All routes inherit private response headers and
strict JSON/authentication boundaries.

Scheduling checks current project management rights, immutable bundle digest
and catalog revision. The persisted actor is later reconstructed as an
ordinary user, never as a retained administrator token. Current membership
must still allow deletion at execution. Each due attempt obtains a SQL CAS
claim, fresh task/lease and a sixty-second execution fence before accessing
the exact files. Normal Hub task state and lease are rechecked alongside
current policy and immutable catalog metadata around each filesystem action.

The existing runner processes at most five due entries per tick, limits
retries to five attempts (including crashes) and applies bounded backoff.
Revoked rights, changed bytes/layout, cancellation and changed revision produce
a machine-readable blocked/cancelled result, not an interactive wait. A partial
unlink or transient failure resumes only the exact retired MP4/PNG paths under
a fresh attempt. No directory discovery, broad sweep or implicit asset expiry
is introduced. Secure device/backup erasure is not claimed.

The lifecycle-managed video tick is independently opt-in through
`ANANTA_PERSONA_VIDEO_RETENTION_ENABLED=1`, on a Hub with video composition.
Enabling image retention does not start it. The Hub lifecycle starts/stops
both configured reconciler types; workers cannot start either. The video
compose overlay exposes the flag as disabled by default. Passive scheduling
services and an empty ledger may be composed without starting a thread or
creating any deletion grants. No real project retention was enabled here.

SRP/OCP/ISP: the shared SQL store owns grant/CAS/audit persistence, the closed
table factory owns schema selection, narrow policy/catalog/task/erasure ports
retain separate responsibilities, and the video bootstrap supplies concrete
dependencies. Existing image constructors/table exports and its named
background thread remain compatible. The Hub tick is a lifecycle adapter,
not a worker-owned orchestration loop.

Tests use real Hub tasks/project membership/SQL/temporary files and explicitly
synthetic clips. They verify image/video isolation, competing Hub claims,
cancelled/expired leases, revoked membership, partial erasure recovery,
authenticated scheduling and independent headless startup/shutdown. The
optional CPU-container gate also imports an actual FFmpeg clip through the
signed worker and user API, retires it, advances only an injected retention
test clock, then deletes the exact test bundle via normal Hub maintenance.
Registry identities remain test-scope; no production cleanup or live Meet
delivery evidence is inferred.

The combined real CPU HTTP/API (purge and automatic-retention variants),
video/image retention, erasure, bootstrap and API regression passed 94 tests
in 77.02 seconds. No production retention grant or timer was enabled.
